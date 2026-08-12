"""Rate-of-change detection for USGS sites.

Percentile bands only fire when a river leaves its historical normal range,
which can stay silent for weeks while the water moves several feet. These
helpers look at how fast a site's readings are changing inside a recent
window so a fast rise or fall raises an alert on its own.
"""

import logging
from datetime import datetime, timedelta, timezone

from db.models import get_db, get_setting

logger = logging.getLogger(__name__)

# USGS parameter code for gauge height in feet; everything else (discharge in
# cfs, for example) is compared as a percentage of where the window started.
STAGE_PARAMETER_CODE = "00065"


def compute_trend(site_id, window_hours, db_path=None):
    """Summarize how a site's readings moved over the last `window_hours`.

    Returns None when fewer than two readings fall inside the window,
    otherwise a dict with:

    ``direction``   "RISING", "FALLING", or "STEADY"
    ``delta``       signed end-minus-start change in the site's own unit
    ``start_value`` oldest reading inside the window
    ``end_value``   newest reading inside the window
    ``hours``       actual span between those two readings
    """
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT current_value, checked_at::timestamptz AS checked_at
               FROM site_conditions
               WHERE site_id = %s
                 AND current_value IS NOT NULL
                 AND checked_at::timestamptz >= NOW() - (%s * INTERVAL '1 hour')
               ORDER BY id""",
            (site_id, float(window_hours))
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if len(rows) < 2:
        return None

    first, last = rows[0], rows[-1]
    start_value = float(first["current_value"])
    end_value = float(last["current_value"])
    delta = end_value - start_value
    hours = (last["checked_at"] - first["checked_at"]).total_seconds() / 3600.0

    if delta > 0:
        direction = "RISING"
    elif delta < 0:
        direction = "FALLING"
    else:
        direction = "STEADY"

    return {
        "direction": direction,
        "delta": delta,
        "start_value": start_value,
        "end_value": end_value,
        "hours": hours,
    }


def exceeds_trend_threshold(delta, parameter_code, start_value, db_path=None):
    """Return True when `delta` is a big enough change to be worth an alert.

    Stage sites (parameter 00065) use an absolute threshold in feet
    (`rate_change_threshold_ft`). Every other parameter — discharge in cfs,
    say — uses a percentage of the window's starting value
    (`rate_change_threshold_pct`), and is never alertable when that starting
    value is zero or negative, since the percentage would be meaningless.
    """
    change = abs(float(delta))

    if parameter_code == STAGE_PARAMETER_CODE:
        threshold_ft = float(get_setting("rate_change_threshold_ft", db_path, default="2.0"))
        return change >= threshold_ft

    if start_value is None or float(start_value) <= 0:
        return False

    threshold_pct = float(get_setting("rate_change_threshold_pct", db_path, default="25"))
    return change >= float(start_value) * threshold_pct / 100.0


def trend_alert_due(site_id, db_path=None):
    """Return True unless a trend alert for this site was sent too recently.

    The most recent trend notification is found with ORDER BY id DESC.
    `sent_at` is a TEXT rendering of a timestamptz, so sorting on it
    lexicographically mis-orders rows whose printed UTC offset differs (for
    example across a daylight-saving change) — insertion order is the only
    reliable ordering here.
    """
    min_interval_hours = float(
        get_setting("rate_change_min_interval_hours", db_path, default="6")
    )

    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT sent_at FROM notifications
               WHERE site_id = %s AND trigger_type = 'trend'
               ORDER BY id DESC LIMIT 1""",
            (site_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if row is None:
        return True

    try:
        parsed = datetime.fromisoformat(row["sent_at"])
    except (TypeError, ValueError):
        logger.warning("Unparseable sent_at %r for site %s; allowing trend alert",
                       row["sent_at"], site_id)
        return True

    last_sent = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_sent >= timedelta(hours=min_interval_hours)
