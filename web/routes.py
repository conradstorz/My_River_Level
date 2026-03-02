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
        return render_template("subscribers.html", subscribers=[])

    @app.route("/sites")
    def sites():
        return render_template("sites.html", sites=[])

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        return render_template("settings.html", fields=[], current={})

    @app.route("/broadcast", methods=["GET", "POST"])
    def broadcast():
        return render_template("broadcast.html")
