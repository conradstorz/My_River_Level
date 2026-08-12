"""HTTP Basic authentication for the River Monitor management portal.

The portal shows and accepts live credentials (Telegram bot token, Twilio
account SID and auth token, Facebook page token), so every admin route is
guarded. Credentials come from the environment, never from the database — the
database is what the portal is protecting.

Environment variables:

``ADMIN_USERNAME``
    Login name. Defaults to ``admin``.
``ADMIN_PASSWORD_HASH``
    A ``werkzeug.security.generate_password_hash`` output. Preferred, because
    the plaintext never appears in the environment or in ``docker inspect``.
``ADMIN_PASSWORD``
    Plaintext fallback, hashed on first use.

If neither password variable is set the guard **fails closed**: protected
routes return 503 rather than falling open to anonymous access.

Endpoints listed in :data:`PUBLIC_ENDPOINTS` are exempt. These are either
unauthenticated by design (the container healthcheck, the provider webhooks,
which are authenticated by signature instead) or authenticated by an
unguessable token in the URL (the ``/view/<public_token>`` and
``/edit/<edit_token>`` landing-page family).
"""

import logging
import os

from flask import Response, request
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

#: Endpoint names that never require Basic auth.
PUBLIC_ENDPOINTS = frozenset({
    "healthz",
    "static",
    "webhook_twilio",
    "webhook_twilio_status",
    "webhook_facebook",
    "page_new",
    "page_view",
    "page_edit",
    "page_add_gauge",
    "page_search_gauges",
    "page_remove_gauge",
    "page_subscribe",
    "page_unsubscribe",
})

UNCONFIGURED_MESSAGE = (
    "Admin credentials are not configured. "
    "Set ADMIN_PASSWORD_HASH or ADMIN_PASSWORD."
)

_REALM = 'Basic realm="River Monitor"'

# Hashing is deliberately slow, so remember the hash we derived for a given
# plaintext password instead of re-deriving it on every request.
_derived_hashes = {}


def admin_credentials():
    """Return ``(username, password_hash)`` from the environment, or None.

    None means no password is configured at all, which callers must treat as
    "refuse every request" — never as "allow every request".
    """
    username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
    password_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
    if password_hash:
        return username, password_hash
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        return None
    derived = _derived_hashes.get(password)
    if derived is None:
        derived = generate_password_hash(password)
        _derived_hashes[password] = derived
    return username, derived


def _unauthorized():
    """Build the 401 that prompts a browser for Basic credentials."""
    return Response(
        "Authentication required.\n",
        status=401,
        headers={"WWW-Authenticate": _REALM},
    )


def init_auth(app):
    """Install the before_request guard that protects every non-public route."""

    @app.before_request
    def _require_admin():
        endpoint = request.endpoint
        # endpoint is None for unrouted URLs; let Flask return its own 404.
        if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
            return None
        credentials = admin_credentials()
        if credentials is None:
            logger.error(
                "Refusing %s %s: no admin password configured", request.method, request.path
            )
            return Response(UNCONFIGURED_MESSAGE, status=503)
        username, password_hash = credentials
        auth = request.authorization
        if auth is None or auth.type != "basic" or not auth.password:
            return _unauthorized()
        if auth.username != username:
            return _unauthorized()
        if not check_password_hash(password_hash, auth.password):
            logger.warning("Failed portal login for user %r", auth.username)
            return _unauthorized()
        return None
