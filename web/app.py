"""
Flask application factory for the River Monitor management portal.

Builds the Flask app, wires the database path, notification queue, and worker
thread registry into its config, registers all portal and webhook routes, then
installs the Basic-auth guard and the /healthz liveness endpoint.
"""

import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from db.models import DATABASE_URL


def create_app(db_path=None, notification_queue=None, thread_registry=None):
    """Create and configure the Flask app, registering routes and app config.

    ``thread_registry`` is a ``dict[str, threading.Thread]`` of the worker
    threads ``main.py`` started; ``/healthz`` reports their liveness.
    """
    app = Flask(__name__, template_folder="templates")
    app.config["DB_PATH"] = db_path or DATABASE_URL
    app.config["NOTIFICATION_QUEUE"] = notification_queue
    app.config["THREAD_REGISTRY"] = thread_registry
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "river-monitor-dev-secret")

    # Rate limits (e.g. the pin flow) key on the request's client address.
    # Behind a reverse proxy every visitor otherwise shares the proxy's IP,
    # so X-Forwarded-For is trusted only when a proxy hop count is declared.
    trusted_proxy_count = int(os.environ.get("TRUSTED_PROXY_COUNT", "0") or 0)
    if trusted_proxy_count > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_count,
                                x_proto=trusted_proxy_count)

    from web.routes import register_routes
    register_routes(app)

    from web.auth import init_auth
    from web.health import register_health
    init_auth(app)
    register_health(app)

    return app
