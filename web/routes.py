from flask import render_template, current_app, request, redirect, url_for, flash
from db.models import get_db, get_setting, set_setting


def register_routes(app):

    @app.route("/")
    def dashboard():
        return render_template("dashboard.html", sites=[], recent_notifications=[])

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
