"""Flask route definitions for the River Monitor web layer.

Defines every HTTP route for the management portal (dashboard, sites,
subscribers, settings, broadcast, admin), the public landing pages
(create/view/edit/subscribe), and the inbound Twilio and Facebook
webhooks. All routes are attached to the app by ``register_routes(app)``.
"""

import logging

from flask import render_template, current_app, request, redirect, url_for, flash
from db.models import get_db, get_setting, set_setting
from version import VERSION, RELEASE_DATE
from monitor.site_validation import validate_usgs_site
from monitor.site_search import search_sites_by_name
from monitor.phone_utils import normalize_e164
from monitor.noaa_client import fetch_gauge_metadata

logger = logging.getLogger(__name__)

SETTINGS_FIELDS = [
    ("poll_interval_minutes", "Poll Interval (minutes)", "number"),
    ("low_percentile", "Low Flow Percentile", "number"),
    ("high_percentile", "High Flow Percentile", "number"),
    ("very_low_percentile", "Very Low Percentile", "number"),
    ("very_high_percentile", "Very High Percentile", "number"),
    ("reminder_low_high_hours", "Reminder Interval: LOW/HIGH (hours)", "number"),
    ("reminder_severe_hours", "Reminder Interval: SEVERE (hours)", "number"),
    ("historical_start_year", "Historical Start Year", "number"),
    ("search_radius_miles", "Search Radius (miles)", "number"),
    ("telegram_bot_token", "Telegram Bot Token", "password"),
    ("twilio_account_sid", "Twilio Account SID", "text"),
    ("twilio_auth_token", "Twilio Auth Token", "password"),
    ("twilio_sms_number", "Twilio SMS Number", "text"),
    ("twilio_whatsapp_number", "Twilio WhatsApp Number", "text"),
    ("facebook_page_token", "Facebook Page Token", "password"),
    ("facebook_verify_token", "Facebook Verify Token", "text"),
]


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
        """
        from_number = request.form.get("From", "")
        body = request.form.get("Body", "").strip().upper()
        to_number = request.form.get("To", "")
        db_path = current_app.config["DB_PATH"]
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
        """
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
        the verify token matches (else 403). On POST, opts in any sender whose
        message is ``JOIN`` and returns ``OK``.
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
        data = request.get_json(force=True, silent=True) or {}
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
        """GET /sites — list all monitored sites with the add/search forms."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM sites ORDER BY station_name")
        all_sites = cur.fetchall()
        cur.close()
        conn.close()
        return render_template("sites.html", sites=all_sites)

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

    @app.route("/sites/search", methods=["POST"])
    def search_sites():
        """POST /sites/search — search USGS gauges by name.

        Requires a gauge name; on error or no matches, flashes a message and
        redirects to the sites page. Otherwise re-renders the sites page with
        the ranked matches and a truncation flag.
        """
        db_path = current_app.config["DB_PATH"]
        query = request.form.get("gauge_name", "").strip()
        if not query:
            flash("Enter a gauge name to search.", "danger")
            return redirect(url_for("sites"))

        matches, truncated, error = search_sites_by_name(query)
        if error:
            flash(error, "danger")
            return redirect(url_for("sites"))
        if not matches:
            flash(f"No gauges found matching {query!r}.", "warning")
            return redirect(url_for("sites"))

        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM sites ORDER BY station_name")
        all_sites = cur.fetchall()
        cur.close()
        conn.close()
        return render_template(
            "sites.html",
            sites=all_sites,
            matches=matches,
            query=query,
            truncated=truncated,
        )

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

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        """GET|POST /settings — view or save runtime settings.

        POST persists every field in ``SETTINGS_FIELDS`` and redirects; GET
        renders the form pre-filled with current values.
        """
        db_path = current_app.config["DB_PATH"]
        if request.method == "POST":
            for key, _, _ in SETTINGS_FIELDS:
                value = request.form.get(key, "")
                set_setting(key, value, db_path)
            flash("Settings saved.", "success")
            return redirect(url_for("settings"))
        current = {key: get_setting(key, db_path, default="") for key, _, _ in SETTINGS_FIELDS}
        return render_template("settings.html", fields=SETTINGS_FIELDS, current=current)

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

    @app.route("/view/<public_token>")
    def page_view(public_token):
        """GET /view/<public_token> — render a public landing page, or 404 if missing/inactive."""
        from flask import abort
        from db.models import get_page_by_public_token, get_page_gauges
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_public_token(public_token, db_path)
        if not page or not page["active"]:
            abort(404)
        gauges = get_page_gauges(page["id"], db_path)
        return render_template("page_view.html", page=page, gauges=gauges)

    @app.route("/edit/<edit_token>")
    def page_edit(edit_token):
        """GET /edit/<edit_token> — render the page editor with its gauges and active subscribers, or 404."""
        from flask import abort
        from db.models import get_page_by_edit_token, get_page_gauges, get_active_page_subscribers
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        gauges = get_page_gauges(page["id"], db_path)
        subscribers = get_active_page_subscribers(page["id"], db_path)
        return render_template("page_edit.html", page=page, gauges=gauges,
                               subscribers=subscribers, edit_token=edit_token)

    @app.route("/edit/<edit_token>/gauges/add", methods=["POST"])
    def page_add_gauge(edit_token):
        """POST /edit/<edit_token>/gauges/add — add a NOAA gauge to the page.

        Requires a gauge ``lid``; looks up its NOAA metadata, upserts the gauge,
        links it to the page, and redirects to the editor. Flashes an error and
        redirects if the token is unknown (404), the id is missing, or the gauge
        is not found.
        """
        from flask import abort
        from db.models import get_page_by_edit_token, get_or_create_noaa_gauge, link_page_gauge
        db_path = current_app.config["DB_PATH"]
        page = get_page_by_edit_token(edit_token, db_path)
        if not page:
            abort(404)
        lid = request.form.get("lid", "").strip().upper()
        if not lid:
            flash("Gauge ID is required.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))
        meta = fetch_gauge_metadata(lid)
        if meta is None:
            flash(f"Gauge '{lid}' not found in the NOAA database.", "danger")
            return redirect(url_for("page_edit", edit_token=edit_token))
        gauge_id = get_or_create_noaa_gauge(
            lid, meta["station_name"],
            meta["action_stage"], meta["minor_flood_stage"],
            meta["moderate_flood_stage"], meta["major_flood_stage"],
            db_path
        )
        link_page_gauge(page["id"], gauge_id, db_path)
        flash(f"Added {meta['station_name']}.", "success")
        return redirect(url_for("page_edit", edit_token=edit_token))

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

    @app.route("/admin/pages")
    def admin_pages():
        """GET /admin/pages — list all landing pages with gauge and active-subscriber counts."""
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT up.*,
                   COUNT(DISTINCT png.noaa_gauge_id) AS gauge_count,
                   COUNT(DISTINCT ps.id) AS subscriber_count
            FROM user_pages up
            LEFT JOIN page_noaa_gauges png ON png.page_id = up.id
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
