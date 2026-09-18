# monitor/pin_discovery.py
"""Turn a dropped pin into a list of gauges on that stretch of river.

The USGS Network Linked Data Index (NLDI) snaps a point to the nearest
NHDPlus flowline and can walk the main stem upstream (UM) and downstream
(DM), returning the USGS gauges on it. That is hydrologically right — it
follows the channel and excludes tributaries — so it is tried first. When
the pin is off the network, navigation finds nothing, or NLDI is down, the
existing bounding-box site search fills the list instead, tagged "nearest",
so the confirm screen is never empty for a bad reason. NLDI publishes no
GNIS river name on either its position or comid endpoint, so the river
name is instead derived from the names of the gauges found on the stem.

NOAA gauges have no NLDI layer. Each proposed USGS site is looked up on the
NWPS per-gauge endpoint (it resolves a USGS number to its LID), so a matched
NOAA gauge inherits the site's upstream/downstream tag; the NWPS bounding-box
listing then adds any other gauges in range as "nearby". The listing itself
publishes no usgsId, which is why the match goes through the per-gauge call.

This module touches no database. Every network step degrades to "nothing
from that step" and logs why, never raising to the caller.
"""

import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

import dataretrieval.nwis as nwis
import requests

from monitor.noaa_client import fetch_gauge_metadata, gauges_near

logger = logging.getLogger(__name__)

NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"
TIMEOUT = 15
#: A pin farther than this from the snapped flowline is treated as off-network.
SNAP_MAX_KM = 2.0
#: Parameters a site must report to be worth proposing; first match wins.
WANTED_PARAMETERS = ("00065", "00060")

#: Text that separates a river name from the location description that
#: follows it in a USGS station name or NWPS gauge name, e.g. "OHIO RIVER
#: AT LOUISVILLE, KY" or "S FK BEARGRASS CREEK NR LOUISVILLE, KY".
_LOCATION_MARKERS = (
    " AT ", " NR ", " NEAR ", " BL ", " BLW ", " BELOW ",
    " AB ", " ABV ", " ABOVE ", " US OF ", " DS OF ", " @ ", " - ", ",",
)
_LOCATION_MARKER_RE = re.compile(
    "|".join(re.escape(marker) for marker in _LOCATION_MARKERS), re.IGNORECASE)

#: Whole-word abbreviations expanded before title-casing a derived river name.
_RIVER_WORD_ABBREVIATIONS = {
    "R": "River", "RV": "River", "CR": "Creek", "CK": "Creek", "BR": "Branch",
    "FK": "Fork", "S": "South", "N": "North", "E": "East", "W": "West",
    "M": "Middle", "LK": "Lake", "BYU": "Bayou", "SLU": "Slough",
}


@dataclass
class Candidate:
    kind: str
    id: str
    name: str
    distance_km: float
    tag: str
    usgs_id: str | None = None
    parameter_code: str | None = None


@dataclass
class Discovery:
    river_name: str | None
    snap: str
    candidates: list = field(default_factory=list)

    def to_dict(self):
        """JSON-ready form for the /discover route."""
        return {
            "river_name": self.river_name,
            "snap": self.snap,
            "candidates": [asdict(c) for c in self.candidates],
        }


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ── NLDI ────────────────────────────────────────────────────────────────────

def _get_json(url, params=None):
    resp = requests.get(url, params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    return resp.json()


def _snap(lat, lon):
    """Return (comid, river_name, distance_km_to_flowline) or raise."""
    data = _get_json(f"{NLDI_BASE}/comid/position?coords=POINT({lon} {lat})")
    features = data.get("features") or []
    if not features:
        raise RuntimeError("NLDI position returned no flowline")
    feat = features[0]
    props = feat.get("properties") or {}
    comid = str(props.get("comid") or props.get("identifier") or "")
    if not comid:
        raise RuntimeError("NLDI flowline has no comid")
    name = (props.get("name") or "").strip() or None
    coords = (feat.get("geometry") or {}).get("coordinates") or []
    if coords and isinstance(coords[0], (int, float)):
        coords = [coords]
    distance = min((haversine_km(lat, lon, c[1], c[0]) for c in coords), default=0.0)
    return comid, name, distance


def _navigate(comid, direction, reach_km):
    """Return [(site_number, name, lat, lon)] for USGS sites along `direction`."""
    data = _get_json(f"{NLDI_BASE}/comid/{comid}/navigation/{direction}/nwissite",
                     params={"distance": reach_km})
    out = []
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        ident = str(props.get("identifier") or "")
        number = ident.split("-", 1)[1] if "-" in ident else ident
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if not number or len(coords) < 2:
            continue
        out.append((number, props.get("name") or number, float(coords[1]), float(coords[0])))
    return out


# ── USGS helpers ─────────────────────────────────────────────────────────────

def _parameters_by_site(site_numbers):
    """Map site number -> preferred parameter code, dropping sites with neither."""
    if not site_numbers:
        return {}
    df, _ = nwis.get_info(sites=list(site_numbers), seriesCatalogOutput=True)
    available = {}
    if df is not None and len(df):
        for _, row in df.iterrows():
            available.setdefault(str(row["site_no"]), set()).add(str(row["parm_cd"]))
    chosen = {}
    for number in site_numbers:
        for code in WANTED_PARAMETERS:
            if code in available.get(number, ()):
                chosen[number] = code
                break
    return chosen


def _bbox_sites(lat, lon, radius_miles):
    """Nearest-within-radius fallback via nwis.what_sites."""
    dlat = radius_miles / 69.0
    dlon = radius_miles / (69.0 * max(math.cos(math.radians(lat)), 0.01))
    bbox = [round(lon - dlon, 6), round(lat - dlat, 6), round(lon + dlon, 6), round(lat + dlat, 6)]
    df, _ = nwis.what_sites(bBox=bbox, siteType="ST", siteStatus="active")
    out = []
    if df is None or not len(df):
        return out
    for _, row in df.iterrows():
        try:
            out.append((str(row["site_no"]), str(row.get("station_nm", "") or row["site_no"]),
                        float(row["dec_lat_va"]), float(row["dec_long_va"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _normalize_river_name(prefix):
    """Expand abbreviated words in `prefix` and title-case the result."""
    words = prefix.split()
    expanded = [_RIVER_WORD_ABBREVIATIONS.get(word.upper(), word) for word in words]
    return " ".join(expanded).title()


def river_name_from_gauges(names):
    """Derive a river name from gauge station names, e.g. "OHIO RIVER AT
    LOUISVILLE, KY" -> "Ohio River". Returns the most common normalised
    name (ties broken by first occurrence), or None if nothing usable.
    """
    counts = Counter()
    first_seen = []
    for raw in names or []:
        if not raw:
            continue
        match = _LOCATION_MARKER_RE.search(raw)
        if not match:
            continue
        prefix = raw[:match.start()].strip()
        if not prefix:
            continue
        normalized = _normalize_river_name(prefix)
        if not normalized:
            continue
        if normalized not in counts:
            first_seen.append(normalized)
        counts[normalized] += 1
    if not counts:
        return None
    return max(first_seen, key=lambda name: counts[name])


# ── Entry point ──────────────────────────────────────────────────────────────

def discover(lat, lon, *, reach_km, fallback_radius_miles):
    """Propose gauges for a pin. See the module docstring for the strategy."""
    river_name = None
    snap = "failed"
    raw = []   # (number, name, lat, lon, tag)

    try:
        comid, river_name, snap_distance = _snap(lat, lon)
        if snap_distance > SNAP_MAX_KM:
            snap = "off_network"
            river_name = None
            logger.info("Pin %s,%s is %.1f km from the nearest flowline", lat, lon, snap_distance)
        else:
            snap = "on_network"
            for direction, tag in (("UM", "upstream"), ("DM", "downstream")):
                try:
                    for number, name, slat, slon in _navigate(comid, direction, reach_km):
                        raw.append((number, name, slat, slon, tag))
                except Exception as exc:
                    logger.warning("NLDI %s navigation failed for comid %s: %s", direction, comid, exc)
    except Exception as exc:
        logger.warning("NLDI snap failed for %s,%s: %s", lat, lon, exc)

    if not raw:
        try:
            for number, name, slat, slon in _bbox_sites(lat, lon, fallback_radius_miles):
                raw.append((number, name, slat, slon, "nearest"))
        except Exception as exc:
            logger.warning("Bounding-box site search failed for %s,%s: %s", lat, lon, exc)

    # De-duplicate (a site can appear in both directions at the snap point).
    seen = {}
    for number, name, slat, slon, tag in raw:
        seen.setdefault(number, (name, slat, slon, tag))

    try:
        parameters = _parameters_by_site(list(seen))
    except Exception as exc:
        # The parameter check is a hard requirement: guessing a code (e.g.
        # always WANTED_PARAMETERS[0]) would provision a site that polls
        # with a parameter it may not report, forever. Drop every USGS
        # candidate for this discovery instead; NOAA gauges from the
        # bounding-box listing are unaffected and still proposed below.
        logger.warning("USGS parameter check failed: %s", exc)
        parameters = {}

    candidates = []
    for number, (name, slat, slon, tag) in seen.items():
        if number not in parameters:
            continue
        candidates.append(Candidate(
            kind="usgs", id=number, name=name,
            distance_km=round(haversine_km(lat, lon, slat, slon), 1),
            tag=tag, parameter_code=parameters[number]))

    # NOAA gauges that share a proposed USGS site inherit its tag. NWPS
    # resolves a USGS number on its per-gauge endpoint; the listing does not
    # publish usgsId, so this is the only reliable way to pair them.
    matched = {}
    for c in list(candidates):
        try:
            meta = fetch_gauge_metadata(c.id)
        except Exception as exc:
            logger.warning("NWPS lookup failed for USGS %s: %s", c.id, exc)
            continue
        if not meta or not meta.get("lid") or meta["lid"] in matched:
            continue
        matched[meta["lid"]] = Candidate(
            kind="noaa", id=meta["lid"], name=meta.get("station_name") or meta["lid"],
            distance_km=c.distance_km, tag=c.tag, usgs_id=c.id)
    candidates.extend(matched.values())

    try:
        for g in gauges_near(lat, lon, fallback_radius_miles):
            if g["lid"] in matched:
                continue
            candidates.append(Candidate(
                kind="noaa", id=g["lid"], name=g["name"],
                distance_km=round(haversine_km(lat, lon, g["lat"], g["lon"]), 1),
                tag="nearby", usgs_id=g["usgs_id"]))
    except Exception as exc:
        logger.warning("NWPS gauge listing failed: %s", exc)

    if river_name is None and snap == "on_network":
        # NWPS names are cleaner than USGS station names, so prefer them:
        # try to derive a name from NOAA stem gauges first, and fall back
        # to USGS stem gauges only if that yields nothing. "nearby"/
        # "nearest" candidates are excluded since they are not necessarily
        # on the same river as the pin.
        stem_tags = ("upstream", "downstream")
        noaa_names = [c.name for c in candidates if c.kind == "noaa" and c.tag in stem_tags]
        usgs_names = [c.name for c in candidates if c.kind == "usgs" and c.tag in stem_tags]
        river_name = river_name_from_gauges(noaa_names)
        if river_name is None:
            river_name = river_name_from_gauges(usgs_names)

    order = {"upstream": 0, "downstream": 1, "nearby": 2, "nearest": 2}
    candidates.sort(key=lambda c: (order.get(c.tag, 3), c.distance_km, c.kind))
    return Discovery(river_name=river_name, snap=snap, candidates=candidates)
