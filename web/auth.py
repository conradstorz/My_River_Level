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
routes return 503 rather than falling open to anonymous access. A hash that is
set but malformed also returns 503 — see :func:`hash_format_error`.

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
    "page_add_site",
    "page_remove_site",
    "page_subscribe",
    "page_unsubscribe",
    "page_set_sensitivity",
    "pin_start",
    "pin_map",
    "pin_discover",
    "pin_save",
})

UNCONFIGURED_MESSAGE = (
    "Admin credentials are not configured. "
    "Set ADMIN_PASSWORD_HASH or ADMIN_PASSWORD."
)

_REALM = 'Basic realm="River Monitor"'

# Written as a chr() so that no source file, shell command, or compose
# variable in this project ever has to quote a bare `$` — see the module
# docstring of set_admin_password.py for why that matters.
_SEP = chr(36)

# Hashing is deliberately slow, so remember the hash we derived for a given
# plaintext password instead of re-deriving it on every request.
_derived_hashes = {}


def hash_format_error(password_hash):
    """Return why ``password_hash`` is unusable, or None if it looks valid.

    werkzeug encodes a hash as ``method$salt$hash``. Those separators are
    exactly the character that docker compose interpolation eats (``$name``)
    and that PowerShell expands (``$$`` is its "last token of the previous
    command" variable), so a hash arriving with them stripped is by far the
    likeliest misconfiguration here.

    It is also the most confusing one: ``check_password_hash`` returns False
    for a separator-less string rather than raising, so a mangled hash is
    indistinguishable from a wrong password and produces an endless 401 loop.
    Detecting it lets the guard say what is actually wrong.
    """
    parts = password_hash.split(_SEP)
    if len(parts) != 3 or not all(parts):
        return (
            "ADMIN_PASSWORD_HASH is not a valid werkzeug hash: expected "
            "'method{sep}salt{sep}hash' but found {n} '{sep}' separator(s). "
            "If there are none, they were stripped in transit: every '{sep}' "
            "in .env must be written twice, and PowerShell eats a doubled "
            "one. Regenerate with: "
            "uv run --with werkzeug python set_admin_password.py"
        ).format(sep=_SEP, n=len(parts) - 1)
    return None


def admin_credentials():
    """Return ``(username, password_hash)`` from the environment, or None.

    None means no password is configured at all, which callers must treat as
    "refuse every request" — never as "allow every request". The hash is
    returned unvalidated; call :func:`hash_format_error` on it.
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


def _misconfigured(message):
    """Build the 503 that reports a config problem instead of a login failure."""
    return Response(message + "\n", status=503)


def check_admin_config():
    """Log any problem with the configured admin credentials; return the reason.

    Called at startup so a broken hash is visible in the logs immediately,
    rather than only as repeated 401s once someone tries to log in.
    """
    credentials = admin_credentials()
    if credentials is None:
        logger.error("%s Admin routes will return 503.", UNCONFIGURED_MESSAGE)
        return UNCONFIGURED_MESSAGE
    reason = hash_format_error(credentials[1])
    if reason:
        logger.error("%s Admin routes will return 503.", reason)
    return reason


def init_auth(app):
    """Install the before_request guard that protects every non-public route."""

    check_admin_config()

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
            return _misconfigured(UNCONFIGURED_MESSAGE)
        username, password_hash = credentials
        reason = hash_format_error(password_hash)
        if reason:
            # 503, not 401: the operator's password may well be correct, and
            # sending 401 here would send them hunting for a typo instead.
            logger.error("Refusing %s %s: %s", request.method, request.path, reason)
            return _misconfigured(reason)
        auth = request.authorization
        if auth is None or auth.type != "basic" or not auth.password:
            return _unauthorized()
        if auth.username != username:
            return _unauthorized()
        if not check_password_hash(password_hash, auth.password):
            logger.warning("Failed portal login for user %r", auth.username)
            return _unauthorized()
        return None
