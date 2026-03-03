from flask import render_template, current_app, request, redirect, url_for, flash
from db.models import get_db, get_setting, set_setting


def register_routes(app):

    @app.route("/")
    def dashboard():
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        sites = conn.execute("""
            SELECT s.site_number, s.station_name, sc.current_value, sc.unit,
                   sc.percentile, sc.severity, sc.checked_at
            FROM sites s
            LEFT JOIN site_conditions sc ON sc.id = (
                SELECT id FROM site_conditions WHERE site_id = s.id ORDER BY id DESC LIMIT 1
            )
            WHERE s.active = 1
            ORDER BY s.station_name
        """).fetchall()
        recent_notifications = conn.execute("""
            SELECT n.sent_at, n.channel, n.message_text, n.trigger_type, n.success,
                   s.station_name
            FROM notifications n
            LEFT JOIN sites s ON s.id = n.site_id
            ORDER BY n.sent_at DESC LIMIT 20
        """).fetchall()
        conn.close()
        return render_template("dashboard.html", sites=sites, recent_notifications=recent_notifications)

    @app.route("/subscribers")
    def subscribers():
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        subs = conn.execute("SELECT * FROM subscribers ORDER BY opted_in_at DESC").fetchall()
        conn.close()
        return render_template("subscribers.html", subscribers=subs)

    @app.route("/subscribers/add", methods=["POST"])
    def add_subscriber():
        db_path = current_app.config["DB_PATH"]
        display_name = request.form.get("display_name", "").strip()
        channel = request.form.get("channel", "").strip()
        channel_id = request.form.get("channel_id", "").strip()
        if channel and channel_id:
            conn = get_db(db_path)
            conn.execute(
                "INSERT OR REPLACE INTO subscribers (display_name, channel, channel_id, active) VALUES (?,?,?,1)",
                (display_name, channel, channel_id)
            )
            conn.commit()
            conn.close()
            flash("Subscriber added.", "success")
        else:
            flash("Channel and channel ID are required.", "danger")
        return redirect(url_for("subscribers"))

    @app.route("/subscribers/<int:sub_id>/remove", methods=["POST"])
    def remove_subscriber(sub_id):
        db_path = current_app.config["DB_PATH"]
        conn = get_db(db_path)
        conn.execute("UPDATE subscribers SET active=0 WHERE id=?", (sub_id,))
        conn.commit()
        conn.close()
        flash("Subscriber removed.", "success")
        return redirect(url_for("subscribers"))

    @app.route("/webhook/twilio", methods=["POST"])
    def webhook_twilio():
        from_number = request.form.get("From", "")
        body = request.form.get("Body", "").strip().upper()
        to_number = request.form.get("To", "")
        db_path = current_app.config["DB_PATH"]
        wa_number = get_setting("twilio_whatsapp_number", db_path)
        channel = "whatsapp" if wa_number and wa_number in to_number else "sms"
        clean_from = from_number.replace("whatsapp:", "")
        conn = get_db(db_path)
        if body == "JOIN":
            conn.execute(
                "INSERT OR REPLACE INTO subscribers (display_name, channel, channel_id, active) VALUES (?,?,?,1)",
                (clean_from, channel, clean_from)
            )
            conn.commit()
        elif body in ("STOP", "UNSUBSCRIBE"):
            conn.execute(
                "UPDATE subscribers SET active=0 WHERE channel=? AND channel_id=?",
                (channel, clean_from)
            )
            conn.commit()
        conn.close()
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>', 200, {"Content-Type": "text/xml"}

    @app.route("/webhook/facebook", methods=["GET", "POST"])
    def webhook_facebook():
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
                    conn.execute(
                        "INSERT OR REPLACE INTO subscribers (display_name, channel, channel_id, active) VALUES (?,?,?,1)",
                        (psid, "facebook", psid)
                    )
                    conn.commit()
                    conn.close()
        return "OK", 200

    @app.route("/sites")
    def sites():
        return render_template("sites.html", sites=[])

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        return render_template("settings.html", fields=[], current={})

    @app.route("/broadcast", methods=["GET", "POST"])
    def broadcast():
        return render_template("broadcast.html")
