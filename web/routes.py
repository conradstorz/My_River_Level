"""Flask route definitions for the River Monitor web layer.

Defines every HTTP route for the management portal (dashboard, sites,
subscribers, settings, broadcast, admin), the public landing pages
(create/view/edit/subscribe), and the inbound Twilio and Facebook
webhooks. All routes are attached to the app by ``register_routes(app)``.

The webhook routes are exempt from the portal's Basic auth (see
``web/auth.py``) because the providers cannot supply credentials. They are
authenticated instead by the provider's request signature, verified by
:func:`verify_facebook_signature` and :func:`verify_twilio_signature`. Both
fail closed: an unconfigured secret rejects every request rather than
accepting them all.
"""

import hashlib
import hmac
import json
import logging

from flask import (render_template, current_app, request, redirect, url_for,
                   flash, jsonify, abort)
from twilio.request_validator import RequestValidator
from db.models import (get_db, get_setting, set_setting, get_sites_with_health,
                       create_pin_page, get_page_by_edit_token, save_pin,
                       SENSITIVITY_LEVELS)
from version import VERSION, RELEASE_DATE
from monitor.site_validation import validate_usgs_site
from monitor.site_search import search_sites_by_name
from monitor.phone_utils import normalize_e164
from monitor.noaa_client import fetch_gauge_metadata
from monitor.gauge_enrich import annotate_liveness, annotate_noaa
from monitor.gauge_discovery import combined_site_matches
from monitor.search_cache import get_or_compute
from monitor.noaa_search import search_noaa_gauges_by_name
from monitor.gauge_quality import split_quality_detail
from monitor.pin_discovery import discover
from web.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# Bootstrap colour for each flood-prediction grade badge. "Unrated" is not a
# failing grade -- it means we have not collected enough forecasts to judge the
# gauge yet -- so it gets a calm grey, the same as anything unrecognised.
GRADE_BADGE_CLASSES = {
    "A": "bg-success",
    "B": "bg-success",
    "C": "bg-warning text-dark",
    "D": "bg-danger",
    "F": "bg-danger",
}
UNRATED_BADGE_CLASS = "bg-secondary"

# Shown when a gauge has never been scored. The forecast archive starts empty
# and fills over days, so this is the normal state for a newly added gauge and
# has to read that way rather than as a fault.
UNRATED_HEADLINE = "Not yet assessed"
UNRATED_DETAIL = (
    "We have not compared this gauge's forecasts against what the river "
    "actually did yet. A grade appears once enough NOAA forecasts have been "
    "collected — usually a few days after the gauge is added."
)

# Public pin routes call third-party APIs on a visitor's click, so they are
# throttled. Per process, per container: a brake, not a distributed quota.
PIN_CREATE_LIMITER = RateLimiter(limit=10, per_seconds=86400)
DISCOVER_TOKEN_LIMITER = RateLimiter(limit=20, per_seconds=3600)
DISCOVER_IP_LIMITER = RateLimiter(limit=60, per_seconds=3600)
PIN_SAVE_LIMITER = RateLimiter(limit=10, per_seconds=3600)

# A pin page's sources come from a visitor-driven discovery search, not an
# admin form, so the count needs an upper bound independent of what the map
# UI happens to send.
MAX_PIN_SOURCES = 30

SENSITIVITY_LABELS = [
    ("floods", "Floods only",
     "NOAA flood-category changes and severe highs"),
    ("unusual", "Floods and unusual levels",
     "Also unusually low or high readings for the time of year"),
    ("all", "Everything, including rapid changes",
     "Also fast rises and falls between readings"),
]


def bot_deep_link(edit_token, db_path):
    """Return the t.me link that binds this page to a Telegram chat, or None."""
    username = (get_setting("telegram_bot_username", db_path, default="") or "").strip()
    if not username:
        return None
    return f"https://t.me/{username}?start={edit_token}"


def _parse_coordinates(payload):
    """Return (lat, lon) floats from a JSON body, or None if unusable."""
    if not isinstance(payload, dict):
        return None
    try:
        lat = float(payload.get("lat"))
        lon = float(payload.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _client_ip():
    return request.remote_addr or "unknown"


def _float_setting(key, default, db_path):
    """Read a numeric setting, falling back to `default` if it won't parse.

    Settings are free-text in the DB; a malformed value here must not 500 a
    public route.
    """
    try:
        return float(get_setting(key, db_path, default=str(default)))
    except (TypeError, ValueError):
        return default


def gauge_quality_view(lid, db_path=None):
    """Return the display fields for one gauge's flood-prediction grade.

    Keys: ``grade`` (a letter or "Unrated"), ``badge_class``, ``headline``
    (the plain-English verdict), ``detail`` (the sentence explaining it), and
    ``checked_at``. A gauge that has never been scored comes back as
    "Unrated / Not yet assessed" rather than as a missing value, so every
    gauge on a page says something honest about itself.
    """
    from db.models import get_gauge_quality
    record = get_gauge_quality(lid, db_path)
    if record is None:
        return {
            "grade": "Unrated",
            "badge_class": UNRATED_BADGE_CLASS,
            "headline": UNRATED_HEADLINE,
            "detail": UNRATED_DETAIL,
            "checked_at": "",
        }
    headline, detail = split_quality_detail(record["detail"])
    return {
        "grade": record["grade"],
        "badge_class": GRADE_BADGE_CLASSES.get(record["grade"], UNRATED_BADGE_CLASS),
        "headline": headline or UNRATED_HEADLINE,
        "detail": detail,
        "checked_at": record["checked_at"],
    }


def annotate_gauge_quality(gauges, db_path=None):
    """Attach a ``quality`` dict to every gauge row in place; returns the list."""
    for gauge in gauges:
        gauge["quality"] = gauge_quality_view(gauge["lid"], db_path)
    return gauges



def verify_facebook_signature(raw_body, db_path):
    """Return True when `raw_body` carries a valid X-Hub-Signature-256 header.

    Facebook signs the exact bytes it POSTed with the app secret. An empty
    ``facebook_app_secret`` setting returns False — an unsigned webhook must
    never be able to enroll subscribers.
    """
    app_secret = (get_setting("facebook_app_secret", db_path, default="") or "").strip()
    if not app_secret:
        logger.warning("Rejecting Facebook webhook: facebook_app_secret is not set")
        return False
    header = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(header, expected):
        logger.warning("Rejecting Facebook webhook: signature mismatch")
        return False
    return True


def verify_twilio_signature(db_path):
    """Return True when the current request carries a valid X-Twilio-Signature.

    Uses Twilio's own ``RequestValidator`` against the full request URL and
    form parameters. An empty ``twilio_auth_token`` setting returns False.
    """
    auth_token = (get_setting("twilio_auth_token", db_path, default="") or "").strip()
    if not auth_token:
        logger.warning("Rejecting Twilio webhook: twilio_auth_token is not set")
        return False
    signature = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(auth_token).validate(
        request.url, request.form.to_dict(), signature
    ):
        logger.warning("Rejecting Twilio webhook: signature mismatch for %s", request.path)
        return False
    return True


SETTINGS_GROUPS = [
    {
        "slug": "monitoring",
        "title": "Monitoring",
        "sections": [
            {"subtitle": None, "fields": [
                ("poll_interval_minutes", "Poll Interval (minutes)", "number"),
                ("historical_start_year", "Historical Start Year", "number"),
                ("search_radius_miles", "Search Radius (miles)", "number"),
                ("discovery_reach_km", "Pin discovery reach along the river (km)", "number"),
                ("public_base_url", "Public base URL (e.g. https://river.example.com)", "text"),
            ]},
        ],
    },
    {
        "slug": "thresholds",
        "title": "Alert Thresholds",
        "sections": [
            {"subtitle": None, "fields": [
                ("low_percentile", "Low Flow Percentile", "number"),
                ("high_percentile", "High Flow Percentile", "number"),
                ("very_low_percentile", "Very Low Percentile", "number"),
                ("very_high_percentile", "Very High Percentile", "number"),
                ("reminder_low_high_hours", "Reminder Interval: LOW/HIGH (hours)", "number"),
                ("reminder_severe_hours", "Reminder Interval: SEVERE (hours)", "number"),
            ]},
        ],
    },
    {
        "slug": "channels",
        "title": "Notification Channels",
        "sections": [
            {"subtitle": "Telegram", "fields": [
                ("telegram_bot_token", "Telegram Bot Token", "password"),
            ]},
            {"subtitle": "Twilio (SMS / WhatsApp)", "fields": [
                ("twilio_account_sid", "Twilio Account SID", "text"),
                ("twilio_auth_token", "Twilio Auth Token", "password"),
                ("twilio_sms_number", "Twilio SMS Number", "text"),
                ("twilio_whatsapp_number", "Twilio WhatsApp Number", "text"),
            ]},
            {"subtitle": "Facebook Messenger", "fields": [
                ("facebook_page_token", "Facebook Page Token", "password"),
                ("facebook_verify_token", "Facebook Verify Token", "text"),
            ]},
        ],
    },
]

# Flattened view — kept so the old module-level name still resolves.
SETTINGS_FIELDS = [f for g in SETTINGS_GROUPS
                   for s in g["sections"] for f in s["fields"]]


def register_routes(app):
    """Register all portal, landing-page, and webhook routes on ``app``."""

    @app.route("/")
    def dashboard():
        """GET / — render the dashboard with active sites' latest conditions and the 20 most recent notifications."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT s.site_number, s.station_name, sc.current_value, sc.unit,
                   sc.percentile, sc.severity, sc.checked_at
            FROM sites s
            LEFT JOIN site_conditions sc ON sc.id = (
                SELECT id FROM site_conditions WHERE site_id = s.id ORDER BY id DESC LIMIT 1
            )
            WHERE s.active = 1
            ORDER BY s.station_name
        """)
        sites = cur.fetchall()
        cur.execute("""
            SELECT n.sent_at, n.channel, n.message_text, n.trigger_type, n.success,
                   s.station_name
            FROM notifications n
            LEFT JOIN sites s ON s.id = n.site_id
            ORDER BY n.sent_at DESC LIMIT 20
        """)
        recent_notifications = cur.fetchall()
        cur.close()
        conn.close()
        return render_template(
            "dashboard.html",
            sites=sites,
            recent_notifications=recent_notifications,
            version=VERSION,
            release_date=RELEASE_DATE,
        )

    @app.route("/subscribers")
    def subscribers():
        """GET /subscribers — list all subscribers newest first."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM subscribers ORDER BY opted_in_at DESC")
        subs = cur.fetchall()
        cur.close()
        conn.close()
        return render_template("subscribers.html", subscribers=subs)

    @app.route("/subscribers/add", methods=["POST"])
    def add_subscriber():
        """POST /subscribers/add — upsert a subscriber from the form.

        Requires ``channel`` and ``channel_id``; SMS/WhatsApp numbers are
        normalized to E.164. Flashes success or an error and redirects back
        to the subscribers page.
        """
        db_path = current_app.config["DB_PATH"]
        display_name = request.form.get("display_name", "").strip()
        channel = request.form.get("channel", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        if channel and channel_id:
            if channel in ("sms", "whatsapp"):
                channel_id = normalize_e164(channel_id)
            conn = get_db(db_path)
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO subscribers (display_name, channel, channel_id, active)
                   VALUES (%s, %s, %s, 1)
                   ON CONFLICT (channel, channel_id)
                   DO UPDATE SET display_name = EXCLUDED.display_name, active = 1""",
                (display_name, channel, channel_id)
            )
            conn.commit()
            cur.close()
            conn.close()
            flash("Subscriber added.", "success")
        else:
            flash("Channel and channel ID are required.", "danger")
        return redirect(url_for("subscribers"))

    @app.route("/subscribers/<int:sub_id>/remove", methods=["POST"])
    def remove_subscriber(sub_id):
        """POST /subscribers/<sub_id>/remove — deactivate a subscriber and redirect back."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("UPDATE subscribers SET active=0 WHERE id=%s", (sub_id,))
        conn.commit()
        cur.close()
        conn.close()
        flash("Subscriber removed.", "success")
        return redirect(url_for("subscribers"))

    @app.route("/webhook/twilio", methods=["POST"])
    def webhook_twilio():
        """POST /webhook/twilio — handle inbound Twilio SMS/WhatsApp keywords.

        Detects the channel from the ``To`` number, then acts on the message
        body: ``JOIN`` opts the sender in, ``STOP``/``UNSUBSCRIBE`` opts them
        out, and ``PAUSE``/``RESUME`` toggle page-subscriber status. Both the
        ``subscribers`` and ``page_subscribers`` tables are updated
        independently. Returns an empty TwiML response.

        Rejects the request with 403 unless it carries a valid
        ``X-Twilio-Signature``.
        """
        db_path = current_app.config["DB_PATH"]
        if not verify_twilio_signature(db_path):
            return "Forbidden", 403
        from_number = request.form.get("From", "")
        body = request.form.get("Body", "").strip().upper()
        to_number = request.form.get("To", "")
        wa_number = get_setting("twilio_whatsapp_number", db_path)
        channel = "whatsapp" if wa_number and wa_number in to_number else "sms"
        clean_from = from_number.replace("whatsapp:", "")
        conn = get_db(db_path)
        cur = conn.cursor()
        if body == "JOIN":
            cur.execute(
                """INSERT INTO subscribers (display_name, channel, channel_id, active)
                   VALUES (%s, %s, %s, 1)
                   ON CONFLICT (channel, channel_id)
                   DO UPDATE SET active = 1""",
                (clean_from, channel, clean_from)
            )
            conn.commit()
        elif body in ("STOP", "UNSUBSCRIBE"):
            cur.execute(
                "UPDATE subscribers SET active=0 WHERE channel=%s AND channel_id=%s",
                (channel, clean_from)
            )
            conn.commit()
        # Page subscriber self-service
        if body == "PAUSE":
            cur.execute(
                "UPDATE page_subscribers SET status='paused' WHERE channel=%s AND channel_id=%s",
                (channel, clean_from)
            )
            conn.commit()
        elif body == "RESUME":
            cur.execute(
                "UPDATE page_subscribers SET status='active' WHERE channel=%s AND channel_id=%s",
                (channel, clean_from)
            )
            conn.commit()
        # Also update page_subscribers — both tables must be updated independently
        # since page_subscribers.id is not the same as subscribers.id
        if body in ("STOP", "UNSUBSCRIBE"):
            cur.execute(
                "UPDATE page_subscribers SET status='unsubscribed' WHERE channel=%s AND channel_id=%s",
                (channel, clean_from)
            )
            conn.commit()
        cur.close()
        conn.close()
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200, {"Content-Type": "text/xml"}

    @app.route("/webhook/twilio/status", methods=["POST"])
    def webhook_twilio_status():
        """
        Twilio delivery status callback.

        Configure this URL in the Twilio console as the "Status Callback URL"
        on your messaging service or phone number. Twilio will POST here when
        a message transitions to delivered, undelivered, or failed.

        Error 30034 = US A2P 10DLC campaign not registered — requires
        registering the sending number with an A2P campaign in Twilio console.

        Rejects the request with 403 unless it carries a valid
        ``X-Twilio-Signature``.
        """
        if not verify_twilio_signature(current_app.config["DB_PATH"]):
            return "Forbidden", 403
        msg_sid = request.form.get("MessageSid", "")
        msg_status = request.form.get("MessageStatus", "")
        error_code = request.form.get("ErrorCode", "")
        to_number = request.form.get("To", "")

        if msg_status in ("undelivered", "failed"):
            logger.warning(
                "Twilio delivery failure: SID=%s status=%s error=%s to=%s",
                msg_sid, msg_status, error_code, to_number,
            )
            if error_code == "30034":
                logger.error(
                    "Twilio error 30034: sending number is not registered with a "
                    "US A2P 10DLC campaign. Register at console.twilio.com → "
                    "Messaging → A2P 10DLC."
                )
        return "", 204

    @app.route("/webhook/facebook", methods=["GET", "POST"])
    def webhook_facebook():
        """GET|POST /webhook/facebook — verify the webhook and handle inbound messages.

        On GET, echoes ``hub.challenge`` when ``hub.mode`` is ``subscribe`` and
        the verify token matches (else 403). On POST, verifies the
        ``X-Hub-Signature-256`` header against the raw body (else 403), then
        opts in any sender whose message is ``JOIN`` and returns ``OK``.
        """
        db_path = current_app.config["DB_PATH"]
        if request.method == "GET":
            mode = request.args.get("hub.mode")
            token = request.args.get("hub.verify_token")
            challenge = request.args.get("hub.challenge")
            verify_token = get_setting("facebook_verify_token", db_path)
            if mode == "subscribe" and token == verify_token:
                return challenge, 200
            return "Forbidden", 403
        # Read the body once — the signature covers these exact bytes, so the
        # payload must be parsed from the same buffer that was verified.
        raw_body = request.get_data()
        if not verify_facebook_signature(raw_body, db_path):
            return "Forbidden", 403
        try:
            data = json.loads(raw_body or b"{}")
        except (ValueError, TypeError):
            logger.warning("Facebook webhook body was not valid JSON")
            data = {}
        if not isinstance(data, dict):
            data = {}
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                psid = event.get("sender", {}).get("id")
                text = event.get("message", {}).get("text", "").strip().upper()
                if psid and text == "JOIN":
                    conn = get_db(db_path)
                    cur = conn.cursor()
                    cur.execute(
                        """INSERT INTO subscribers (display_name, channel, channel_id, active)
                           VALUES (%s, %s, %s, 1)
                           ON CONFLICT (channel, channel_id)
                           DO UPDATE SET active = 1""",
                        (psid, "facebook", psid)
                    )
                    conn.commit()
                    cur.close()
                    conn.close()
        return "OK", 200

    @app.route("/sites")
    def sites():
        """GET /sites — list every monitored site, with health, plus the add/search forms.

        Sites come from ``get_sites_with_health`` so a gauge that has stopped
        returning readings is flagged in the listing instead of sitting there
        looking fine.
        """
        db_path = current_app.config["DB_PATH"]
        return render_template("sites.html", sites=get_sites_with_health(db_path))

    @app.route("/sites/add", methods=["POST"])
    def add_site():
        """POST /sites/add — validate a USGS site number and add it.

        Requires a site number; validates it against the USGS API before
        inserting (existing site numbers are left untouched). Flashes the
        outcome and redirects back to the sites page.
        """
        db_path = current_app.config["DB_PATH"]
        site_number = request.form.get("site_number", "").strip()
        param_code = request.form.get("parameter_code", "00060").strip()
        if not site_number:
            flash("Site number is required.", "danger")
            return redirect(url_for("sites"))

        is_valid, usgs_name, error = validate_usgs_site(site_number, param_code)
        if not is_valid:
            flash(f"Invalid site number: {error}", "danger")
            return redirect(url_for("sites"))

        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO sites (site_number, station_name, parameter_code)
               VALUES (%s, %s, %s)
               ON CONFLICT (site_number) DO NOTHING""",
            (site_number, usgs_name, param_code)
        )
        conn.commit()
        cur.close()
        conn.close()
        flash(f"Site {site_number} ({usgs_name}) added.", "success")
        return redirect(url_for("sites"))

    @app.route("/sites/search", methods=["GET"])
    def search_sites():
        """GET /sites/search?q=&page= — paginated USGS+NOAA gauge search."""
        db_path = current_app.config["DB_PATH"]
        query = request.args.get("q", "").strip()
        if not query:
            flash("Enter a gauge name to search.", "danger")
            return redirect(url_for("sites"))
        page = request.args.get("page", 1, type=int)
        if page < 1:
            page = 1

        rows, noaa_only, capped, error = get_or_compute(
            f"sites:{query.lower()}", lambda: combined_site_matches(query))
        if error:
            flash(error, "danger")
            return redirect(url_for("sites"))
        if not rows:
            flash(f"No gauges found matching {query!r}.", "warning")
            return redirect(url_for("sites"))

        per_page = 25
        total = len(rows)
        pages = (total + per_page - 1) // per_page
        page = min(page, pages)
        start = (page - 1) * per_page
        page_rows = rows[start:start + per_page]

        try:
            annotate_liveness(page_rows)
        except Exception:
            logger.warning("Liveness enrichment failed", exc_info=True)
        try:
            annotate_noaa(page_rows)
        except Exception:
            logger.warning("NOAA enrichment failed", exc_info=True)
        page_rows.sort(key=lambda m: 0 if m.get("live") else 1)

        return render_template(
            "sites.html", sites=get_sites_with_health(db_path),
            matches=page_rows, query=query,
            page=page, pages=pages, total=total, capped=capped,
            noaa_only=noaa_only)

    @app.route("/sites/<int:site_id>/toggle", methods=["POST"])
    def toggle_site(site_id):
        """POST /sites/<site_id>/toggle — flip a site's active flag and redirect back."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sites SET active = 1 - active WHERE id=%s", (site_id,))
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for("sites"))

    @app.route("/sites/<int:site_id>/remove", methods=["POST"])
    def remove_site(site_id):
        """POST /sites/<site_id>/remove — delete a site and redirect back."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM sites WHERE id=%s", (site_id,))
        conn.commit()
        cur.close()
        conn.close()
        flash("Site removed.", "success")
        return redirect(url_for("sites"))

    @app.route("/settings")
    def settings():
        """GET /settings — redirect to the first settings sub-page."""
        return redirect(url_for("settings_group", slug=SETTINGS_GROUPS[0]["slug"]))

    @app.route("/settings/<slug>", methods=["GET", "POST"])
    def settings_group(slug):
        """GET|POST /settings/<slug> — view or save one group of settings.

        GET renders the group's sections plus the tab nav. POST persists only
        this group's fields and redirects back to the same tab. Unknown slug → 404.
        """
        from flask import abort
        group = next((g for g in SETTINGS_GROUPS if g["slug"] == slug), None)
        if not group:
            abort(404)
        db_path = current_app.config["DB_PATH"]
        keys = [f[0] for s in group["sections"] for f in s["fields"]]
        if request.method == "POST":
            for key in keys:
                set_setting(key, request.form.get(key, ""), db_path)
            flash("Settings saved.", "success")
            return redirect(url_for("settings_group", slug=slug))
        current = {key: get_setting(key, db_path, default="") for key in keys}
        return render_template("settings.html", groups=SETTINGS_GROUPS,
                               active=group, current=current)

    @app.route("/pages/new", methods=["GET", "POST"])
    def page_new():
        """GET|POST /pages/new — create a public landing page.

        POST requires a page name, creates the page, and renders the created
        page with its public and edit tokens; GET renders the creation form.
        """
        if request.method == "POST":
            page_name = request.form.get("page_name", "").strip()
            if not page_name:
                flash("Page name is required.", "danger")
                return render_template("page_new.html")
            from db.models import create_user_page
            db_path = current_app.config["DB_PATH"]
            public_token, edit_token = create_user_page(page_name, db_path)
            return render_template("page_created.html",
                                   page_name=page_name,
                                   public_token=public_token,
                                   edit_token=edit_token)
        return render_template("page_new.html")

    @app.route("/pin")
    def pin_start():
        """GET /pin — web-first entry: create a pending page and open its map."""
        if not PIN_CREATE_LIMITER.allow(_client_ip()):
            return "Too many new pages from this address today.\n", 429
        db_path = current_app.config["DB_PATH"]
        page = create_pin_page(None, db_path)
        return redirect(url_for("pin_map", edit_token=page["edit_token"]))

    def _pin_page_or_abort(edit_token):
        page = get_page_by_edit_token(edit_token, current_app.config["DB_PATH"])
        if not page:
            abort(404)
        if page["status"] == "stopped":
            abort(410)
        return page

    @app.route("/pin/<edit_token>")
    def pin_map(edit_token):
        """GET /pin/<edit_token> — the map screen."""
        db_path = current_app.config["DB_PATH"]
        page = _pin_page_or_abort(edit_token)
        return render_template(
            "pin.html", page=page, edit_token=edit_token,
            sensitivity_options=SENSITIVITY_LABELS,
            bot_link=bot_deep_link(edit_token, db_path),
        )

    @app.route("/pin/<edit_token>/discover", methods=["POST"])
    def pin_discover(edit_token):
        """POST /pin/<edit_token>/discover — propose gauges for a lat/lon."""
        db_path = current_app.config["DB_PATH"]
        _pin_page_or_abort(edit_token)
        if not DISCOVER_TOKEN_LIMITER.allow(edit_token) or not DISCOVER_IP_LIMITER.allow(_client_ip()):
            return jsonify({"error": "Too many searches; please wait a while."}), 429
        coords = _parse_coordinates(request.get_json(silent=True))
        if coords is None:
            return jsonify({"error": "lat and lon must be valid coordinates."}), 400
        reach_km = _float_setting("discovery_reach_km", 50.0, db_path)
        radius = _float_setting("search_radius_miles", 25.0, db_path)
        result = discover(coords[0], coords[1], reach_km=reach_km,
                          fallback_radius_miles=radius)
        return jsonify(result.to_dict())

    @app.route("/pin/<edit_token>/save", methods=["POST"])
    def pin_save(edit_token):
        """POST /pin/<edit_token>/save — store the pin and provision its sources."""
        db_path = current_app.config["DB_PATH"]
        page = _pin_page_or_abort(edit_token)
        if not PIN_SAVE_LIMITER.allow(edit_token):
            return jsonify({"error": "Too many saves; please wait a while."}), 429
        payload = request.get_json(silent=True) or {}
        coords = _parse_coordinates(payload)
        if coords is None:
            return jsonify({"error": "lat and lon must be valid coordinates."}), 400
        sensitivity = payload.get("sensitivity", "unusual")
        if sensitivity not in SENSITIVITY_LEVELS:
            return jsonify({"error": "Unknown sensitivity level."}), 400
        sources = payload.get("sources") or []
        if not isinstance(sources, list) or not sources:
            return jsonify({"error": "Choose at least one source."}), 400
        if len(sources) > MAX_PIN_SOURCES:
            return jsonify({"error": "Too many sources; choose at most 30."}), 400

        usgs_sites, noaa_gauges, skipped = [], [], []
        for src in sources:
            if not isinstance(src, dict):
                skipped.append("invalid")
                continue
            kind = src.get("kind")
            ident = str(src.get("id", "") or "").strip()
            if not ident:
                skipped.append("invalid")
                continue
            if kind == "usgs":
                code = str(src.get("parameter_code") or "00065")
                if code not in ("00065", "00060"):
                    skipped.append(f"usgs:{ident}")
                    continue
                ok, name, _err = validate_usgs_site(ident, code)
                if ok:
                    usgs_sites.append({"site_number": ident, "station_name": name,
                                       "parameter_code": code})
                else:
                    skipped.append(f"usgs:{ident}")
            elif kind == "noaa":
                meta = fetch_gauge_metadata(ident)
                if meta:
                    noaa_gauges.append(meta)
                else:
                    skipped.append(f"noaa:{ident}")
        if not usgs_sites and not noaa_gauges:
            return jsonify({"error": "None of the chosen sources could be verified.",
                            "skipped": skipped}), 400

        river_name = (payload.get("river_name") or "").strip() or None
        save_pin(page["id"], coords[0], coords[1], river_name, sensitivity,
                 usgs_sites, noaa_gauges, db_path)
        saved = get_page_by_edit_token(edit_token, db_path)

        queue_ = current_app.config.get("NOTIFICATION_QUEUE")
        if saved["owner_chat_id"] is not None and queue_ is not None:
            names = [s["station_name"] or s["site_number"] for s in usgs_sites] + \
                    [g["station_name"] for g in noaa_gauges]
            listing = "\n".join(f"• {n}" for n in names)
            queue_.put({"type": "direct", "data": {
                "channel": "telegram",
                "channel_id": str(saved["owner_chat_id"]),
                "message": (
                    f"✓ You're set up for {river_name or 'your river'}. "
                    f"You'll hear about:\n{listing}\n\n"
                    "Send /settings any time to change gauges or sensitivity."
                ),
            }})

        return jsonify({
            "ok": True,
            "status": saved["status"],
            "skipped": skipped,
            "bot_link": bot_deep_link(edit_token, db_path),
            "edit_url": url_for("page_edit", edit_token=edit_token),
        })

    @app.route("/view/<public_token>")
    def page_view(public_token):
        """GET /view/<public_token> — render a public landing page, or 404 if missing/inactive."""
        from flask import abort
        from db.models import get_page_by_public_token, get_page_gauges
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_public_token(public_token, db_path)
        if not page or not page["active"]:
            abort(404)
        gauges = annotate_gauge_quality(get_page_gauges(page["id"], db_path), db_path)
        return render_template("page_view.html", page=page, gauges=gauges)

    @app.route("/edit/<edit_token>")
    def page_edit(edit_token):
        """GET /edit/<edit_token> — render the page editor with its gauges and active subscribers, or 404."""
        from flask import abort
        from db.models import (get_page_by_edit_token, get_page_gauges,
                               get_active_page_subscribers, get_page_sites)
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        gauges = annotate_gauge_quality(get_page_gauges(page["id"], db_path), db_path)
        subscribers = get_active_page_subscribers(page["id"], db_path)
        sites = get_page_sites(page["id"], db_path)
        return render_template("page_edit.html", page=page, gauges=gauges,
                               subscribers=subscribers, sites=sites,
                               edit_token=edit_token, sensitivity_options=SENSITIVITY_LABELS)

    @app.route("/edit/<edit_token>/gauges/add", methods=["POST"])
    def page_add_gauge(edit_token):
        """POST /edit/<edit_token>/gauges/add — add a NOAA gauge to the page.

        Accepts a gauge ``lid`` **or** a USGS site number (``fetch_gauge_metadata``
        resolves either); looks up its NOAA metadata, upserts the gauge under its
        canonical LID, links it to the page, and redirects to the editor. Flashes
        an error and redirects if the token is unknown (404), the id is missing,
        or the gauge is not found.
        """
        from flask import abort
        from db.models import get_page_by_edit_token, get_or_create_noaa_gauge, link_page_gauge
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)

        identifier = request.form.get("lid", "").strip()
        if not identifier:
            flash("Gauge ID is required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        meta = fetch_gauge_metadata(identifier)
        if not meta:
            flash(f"Gauge '{identifier}' not found in the NOAA database.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        gauge_id = get_or_create_noaa_gauge(
            meta["lid"], meta["station_name"],
            meta["action_stage"], meta["minor_flood_stage"],
            meta["moderate_flood_stage"], meta["major_flood_stage"],
            db_path,
        )
        link_page_gauge(page["id"], gauge_id, db_path)
        flash(f"Gauge {meta['station_name']} added.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/gauges/search", methods=["GET"])
    def page_search_gauges(edit_token):
        """GET /edit/<edit_token>/gauges/search?q=&page= — find NOAA gauges by name."""
        from flask import abort
        db_path = current_app.config["DB_PATH"]
        from db.models import (get_page_by_edit_token, get_page_gauges,
                               get_active_page_subscribers, get_page_sites)
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)

        query = request.args.get("q", "").strip()
        pageno = request.args.get("page", 1, type=int)
        if pageno < 1:
            pageno = 1
        gauge_matches, pages, total = [], 1, 0
        if not query:
            flash("Enter a gauge name to search.", "danger")
        else:
            cands, _capped, error = get_or_compute(
                f"noaa:{query.lower()}", lambda: search_noaa_gauges_by_name(query))
            if error:
                flash(error, "danger")
            elif not cands:
                flash(f"No NOAA gauges found matching {query!r}.", "warning")
            else:
                per_page = 25
                total = len(cands)
                pages = (total + per_page - 1) // per_page
                pageno = min(pageno, pages)
                start = (pageno - 1) * per_page
                gauge_matches = cands[start:start + per_page]

        gauges = annotate_gauge_quality(get_page_gauges(page["id"], db_path), db_path)
        subscribers = get_active_page_subscribers(page["id"], db_path)
        sites = get_page_sites(page["id"], db_path)
        return render_template(
            "page_edit.html", page=page, gauges=gauges, subscribers=subscribers,
            sites=sites, edit_token=edit_token, gauge_matches=gauge_matches,
            gauge_query=query, gauge_page=pageno, gauge_pages=pages,
            gauge_total=total, sensitivity_options=SENSITIVITY_LABELS)

    @app.route("/edit/<edit_token>/gauges/remove", methods=["POST"])
    def page_remove_gauge(edit_token):
        """POST /edit/<edit_token>/gauges/remove — unlink a gauge from the page and redirect to the editor (404 on bad token)."""
        from flask import abort
        from db.models import get_page_by_edit_token, unlink_page_gauge
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        gauge_id = request.form.get("gauge_id", type=int)
        if gauge_id:
            unlink_page_gauge(page["id"], gauge_id, db_path)
            flash("Gauge removed.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/sites/add", methods=["POST"])
    def page_add_site(edit_token):
        """POST /edit/<edit_token>/sites/add — attach a USGS river gauge to the page.

        USGS alerts are routed through ``page_sites``, so this link is what
        decides who hears about a river changing. Takes ``site_number`` and an
        optional ``parameter_code``, validates the number against the USGS API,
        inserts the site if it is new, then links it to the page. 404s on an
        unknown edit token.
        """
        from flask import abort
        from db.models import get_page_by_edit_token, link_page_site
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)

        site_number = request.form.get("site_number", "").strip()
        param_code = request.form.get("parameter_code", "00065").strip() or "00065"
        if not site_number:
            flash("USGS site number is required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        is_valid, usgs_name, error = validate_usgs_site(site_number, param_code)
        if not is_valid:
            flash(f"Invalid site number: {error}", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        conn = get_db(db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO sites (site_number, station_name, parameter_code)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (site_number) DO NOTHING""",
                (site_number, usgs_name, param_code)
            )
            conn.commit()
            cur.execute("SELECT id FROM sites WHERE site_number=%s", (site_number,))
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()
        if row is None:
            flash(f"Could not add site {site_number}.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))

        link_page_site(page["id"], row["id"], db_path)
        flash(f"River gauge {usgs_name or site_number} added to this page.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/sites/remove", methods=["POST"])
    def page_remove_site(edit_token):
        """POST /edit/<edit_token>/sites/remove — detach a USGS gauge from the page.

        Takes ``site_id`` and unlinks it. The site itself stays configured, so
        any other page using it keeps its alerts. 404s on an unknown edit token.
        """
        from flask import abort
        from db.models import get_page_by_edit_token, unlink_page_site
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        site_id = request.form.get("site_id", type=int)
        if site_id:
            unlink_page_site(page["id"], site_id, db_path)
            flash("River gauge removed from this page.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/subscribe", methods=["POST"])
    def page_subscribe(edit_token):
        """POST /edit/<edit_token>/subscribe — subscribe a recipient to the page's alerts.

        Requires ``channel`` and ``channel_id`` (SMS/WhatsApp normalized to
        E.164); adds the page subscriber and redirects to the editor. 404s on an
        unknown token.
        """
        from flask import abort
        from db.models import get_page_by_edit_token, add_page_subscriber
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        channel = request.form.get("channel", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        display_name = request.form.get("display_name", "").strip()
        if not channel or not channel_id:
            flash("Channel and channel ID are required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))
        if channel in ("sms", "whatsapp"):
            channel_id = normalize_e164(channel_id)
        add_page_subscriber(page["id"], channel, channel_id, display_name, db_path)
        flash("Subscribed to alerts for this page.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/unsubscribe", methods=["POST"])
    def page_unsubscribe(edit_token):
        """POST /edit/<edit_token>/unsubscribe — change a page subscriber's status.

        Requires ``channel`` and ``channel_id``; sets the status (default
        ``unsubscribed``) and redirects to the editor. 404s on an unknown token.
        """
        from flask import abort
        from db.models import get_page_by_edit_token, set_page_subscriber_status
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        channel = request.form.get("channel", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        new_status = request.form.get("status", "unsubscribed")
        if channel and channel_id:
            set_page_subscriber_status(page["id"], channel, channel_id, new_status, db_path)
            flash(f"Status updated to {new_status}.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/edit/<edit_token>/sensitivity", methods=["POST"])
    def page_set_sensitivity(edit_token):
        """POST /edit/<edit_token>/sensitivity — set how much this page hears about."""
        from db.models import set_page_sensitivity
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        value = request.form.get("sensitivity", "")
        if value not in SENSITIVITY_LEVELS:
            flash("Unknown sensitivity level.", "danger")
        else:
            set_page_sensitivity(page["id"], value, db_path)
            flash("Sensitivity updated.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

    @app.route("/admin/pages")
    def admin_pages():
        """GET /admin/pages — list all landing pages with gauge and active-subscriber counts."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT up.*,
                   COUNT(DISTINCT png.noaa_gauge_id) AS gauge_count,
                   COUNT(DISTINCT pst.site_id) AS site_count,
                   COUNT(DISTINCT ps.id) AS subscriber_count
            FROM user_pages up
            LEFT JOIN page_noaa_gauges png ON png.page_id = up.id
            LEFT JOIN page_sites pst ON pst.page_id = up.id
            LEFT JOIN page_subscribers ps ON ps.page_id = up.id AND ps.status='active'
            GROUP BY up.id
            ORDER BY up.created_at DESC
        """)
        pages = cur.fetchall()
        cur.close()
        conn.close()
        return render_template("admin_pages.html", pages=pages)

    @app.route("/admin/pages/<int:page_id>/toggle", methods=["POST"])
    def admin_toggle_page(page_id):
        """POST /admin/pages/<page_id>/toggle — flip a page's active flag and redirect to the admin list."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("UPDATE user_pages SET active = 1 - active WHERE id=%s", (page_id,))
        conn.commit()
        cur.close()
        conn.close()
        flash("Page status updated.", "success")
        return redirect(url_for("admin_pages"))

    @app.route("/broadcast", methods=["GET", "POST"])
    def broadcast():
        """GET|POST /broadcast — send a broadcast message to selected channels.

        POST enqueues a broadcast notification when a non-empty message and a
        notification queue are available, flashing the outcome, then redirects;
        GET renders the broadcast form.
        """
        db_path = current_app.config["DB_PATH"]
        notification_queue = current_app.config.get("NOTIFICATION_QUEUE")
        if request.method == "POST":
            message = request.form.get("message", "").strip()
            channels = request.form.getlist("channels")
            if message and notification_queue is not None:
                notification_queue.put({
                    "type": "broadcast",
                    "data": {
                        "message": message,
                        "channels": channels,
                    }
                })
                flash("Broadcast queued.", "success")
            elif not message:
                flash("Message cannot be empty.", "danger")
            return redirect(url_for("broadcast"))
        return render_template("broadcast.html")
