"""
Flask application factory for the River Monitor management portal.

Builds the Flask app, wires the database path and notification queue into its
config, and registers all portal and webhook routes.
"""

import os

from flask import Flask
from db.models import DATABASE_URL


def create_app(db_path=None, notification_queue=None):
    """Create and configure the Flask app, registering routes and app config."""
    app = Flask(__name__, template_folder="templates")
    app.config["DB_PATH"] = db_path or DATABASE_URL
    app.config["NOTIFICATION_QUEUE"] = notification_queue
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "river-monitor-dev-secret")

    from web.routes import register_routes
    register_routes(app)

    return app
