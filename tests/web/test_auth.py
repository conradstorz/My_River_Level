import base64
import pytest
from db.models import init_db
from web.app import create_app


def _client(monkeypatch, tmp_db, password="secret"):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    if password is None:
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    else:
        monkeypatch.setenv("ADMIN_PASSWORD", password)
        monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    return app.test_client()


def _auth(user="admin", pw="secret"):
    raw = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def test_dashboard_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/").status_code == 401


def test_dashboard_allows_correct_credentials(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/", headers=_auth()).status_code == 200


def test_dashboard_rejects_wrong_password(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/", headers=_auth(pw="wrong")).status_code == 401


def test_dashboard_rejects_wrong_username(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/", headers=_auth(user="nobody")).status_code == 401


def test_unauthorized_response_asks_for_basic_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    resp = c.get("/")
    assert "Basic" in resp.headers.get("WWW-Authenticate", "")


def test_admin_password_hash_env_is_accepted(monkeypatch, tmp_db):
    from werkzeug.security import generate_password_hash
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("secret"))
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    c = app.test_client()
    assert c.get("/", headers=_auth()).status_code == 200
    assert c.get("/", headers=_auth(pw="nope")).status_code == 401


def test_settings_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/settings/channels").status_code == 401


def test_broadcast_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.post("/broadcast", data={"message": "hi"}).status_code == 401


def test_admin_pages_requires_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/admin/pages").status_code == 401


def test_unconfigured_password_returns_503_not_open_access(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db, password=None)
    resp = c.get("/")
    assert resp.status_code == 503
    assert b"ADMIN_PASSWORD" in resp.data


def test_unconfigured_password_still_503_when_credentials_supplied(monkeypatch, tmp_db):
    """Fail closed: a client cannot guess its way in when nothing is configured."""
    c = _client(monkeypatch, tmp_db, password=None)
    assert c.get("/", headers=_auth()).status_code == 503


def test_public_page_view_does_not_require_auth(monkeypatch, tmp_db):
    from db.models import create_user_page
    c = _client(monkeypatch, tmp_db)
    public, _ = create_user_page("Test", tmp_db)
    assert c.get(f"/view/{public}").status_code == 200


def test_public_page_edit_does_not_require_auth(monkeypatch, tmp_db):
    from db.models import create_user_page
    c = _client(monkeypatch, tmp_db)
    _, edit = create_user_page("Test", tmp_db)
    assert c.get(f"/edit/{edit}").status_code == 200


def test_new_page_does_not_require_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/pages/new").status_code == 200


def test_healthz_does_not_require_auth(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db)
    assert c.get("/healthz").status_code == 200


def test_healthz_open_even_when_password_unconfigured(monkeypatch, tmp_db):
    c = _client(monkeypatch, tmp_db, password=None)
    assert c.get("/healthz").status_code == 200


def test_facebook_webhook_rejects_unsigned_post(monkeypatch, tmp_db):
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_app_secret", "s3cret", tmp_db)
    resp = c.post("/webhook/facebook", json={"entry": []})
    assert resp.status_code == 403


def test_facebook_webhook_rejects_wrong_signature(monkeypatch, tmp_db):
    import json
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_app_secret", "s3cret", tmp_db)
    body = json.dumps({"entry": []}).encode()
    resp = c.post("/webhook/facebook", data=body,
                  headers={"Content-Type": "application/json",
                           "X-Hub-Signature-256": "sha256=" + "0" * 64})
    assert resp.status_code == 403


def test_facebook_webhook_rejects_when_secret_unconfigured(monkeypatch, tmp_db):
    """Empty facebook_app_secret must fail closed, not accept everything."""
    import hmac, hashlib, json
    c = _client(monkeypatch, tmp_db)
    body = json.dumps({"entry": []}).encode()
    sig = "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()
    resp = c.post("/webhook/facebook", data=body,
                  headers={"Content-Type": "application/json",
                           "X-Hub-Signature-256": sig})
    assert resp.status_code == 403


def test_facebook_webhook_accepts_valid_signature(monkeypatch, tmp_db):
    import hmac, hashlib, json
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_app_secret", "s3cret", tmp_db)
    body = json.dumps({"entry": []}).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    resp = c.post("/webhook/facebook", data=body,
                  headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    assert resp.status_code == 200


def test_facebook_signed_post_still_enrolls_join(monkeypatch, tmp_db):
    """Signature checking must not break the payload parsing it guards."""
    import hmac, hashlib, json
    from db.models import set_setting, get_db
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_app_secret", "s3cret", tmp_db)
    payload = {"entry": [{"messaging": [
        {"sender": {"id": "psid-1"}, "message": {"text": "JOIN"}}
    ]}]}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    resp = c.post("/webhook/facebook", data=body,
                  headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    assert resp.status_code == 200
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT active FROM subscribers WHERE channel='facebook' AND channel_id='psid-1'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row is not None and row["active"] == 1


def test_facebook_verify_get_still_works(monkeypatch, tmp_db):
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("facebook_verify_token", "mytoken", tmp_db)
    resp = c.get("/webhook/facebook", query_string={
        "hub.mode": "subscribe", "hub.verify_token": "mytoken", "hub.challenge": "abc123"})
    assert resp.status_code == 200
    assert b"abc123" in resp.data


def test_twilio_webhook_rejects_unsigned_post(monkeypatch, tmp_db):
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("twilio_auth_token", "tok", tmp_db)
    resp = c.post("/webhook/twilio", data={"From": "+15025551234", "Body": "JOIN"})
    assert resp.status_code == 403


def test_twilio_webhook_rejects_when_token_unconfigured(monkeypatch, tmp_db):
    from twilio.request_validator import RequestValidator
    c = _client(monkeypatch, tmp_db)
    params = {"From": "+15025551234", "Body": "JOIN"}
    sig = RequestValidator("").compute_signature("http://localhost/webhook/twilio", params)
    resp = c.post("/webhook/twilio", data=params, headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 403


def test_twilio_webhook_accepts_valid_signature(monkeypatch, tmp_db):
    from twilio.request_validator import RequestValidator
    from db.models import set_setting, get_db
    c = _client(monkeypatch, tmp_db)
    set_setting("twilio_auth_token", "tok", tmp_db)
    params = {"From": "+15025551234", "Body": "JOIN", "To": "+18005550000"}
    sig = RequestValidator("tok").compute_signature("http://localhost/webhook/twilio", params)
    resp = c.post("/webhook/twilio", data=params, headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 200
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT active FROM subscribers WHERE channel_id='+15025551234'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row is not None and row["active"] == 1


def test_twilio_webhook_rejects_tampered_body(monkeypatch, tmp_db):
    """A signature computed over different params must not validate."""
    from twilio.request_validator import RequestValidator
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("twilio_auth_token", "tok", tmp_db)
    sig = RequestValidator("tok").compute_signature(
        "http://localhost/webhook/twilio", {"From": "+15025551234", "Body": "JOIN"})
    resp = c.post("/webhook/twilio",
                  data={"From": "+15025559999", "Body": "JOIN"},
                  headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 403


def test_twilio_status_webhook_rejects_unsigned_post(monkeypatch, tmp_db):
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("twilio_auth_token", "tok", tmp_db)
    resp = c.post("/webhook/twilio/status", data={"MessageSid": "SM1", "MessageStatus": "delivered"})
    assert resp.status_code == 403


def test_twilio_status_webhook_accepts_valid_signature(monkeypatch, tmp_db):
    from twilio.request_validator import RequestValidator
    from db.models import set_setting
    c = _client(monkeypatch, tmp_db)
    set_setting("twilio_auth_token", "tok", tmp_db)
    params = {"MessageSid": "SM1", "MessageStatus": "delivered", "To": "+18125550000"}
    sig = RequestValidator("tok").compute_signature(
        "http://localhost/webhook/twilio/status", params)
    resp = c.post("/webhook/twilio/status", data=params, headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 204


def _hash_client(monkeypatch, tmp_db, password_hash):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", password_hash)
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    return app.test_client()


def test_hash_format_error_accepts_a_real_hash():
    from werkzeug.security import generate_password_hash
    from web.auth import hash_format_error
    assert hash_format_error(generate_password_hash("secret")) is None


def test_hash_format_error_rejects_stripped_separators():
    """The PowerShell/compose failure: every `$` eaten, hash still 'looks' fine."""
    from werkzeug.security import generate_password_hash
    from web.auth import hash_format_error
    mangled = generate_password_hash("secret").replace(chr(36), "")
    assert hash_format_error(mangled) is not None


def test_hash_format_error_rejects_empty_salt():
    from web.auth import hash_format_error
    assert hash_format_error("scrypt:32768:8:1" + chr(36) + chr(36) + "ab") is not None


def test_mangled_hash_returns_503_not_401(monkeypatch, tmp_db):
    """A broken hash must be reported as misconfiguration, not a bad password.

    check_password_hash returns False for a separator-less string, so without
    this the operator sees an endless 401 loop and hunts for a typo.
    """
    from werkzeug.security import generate_password_hash
    mangled = generate_password_hash("secret").replace(chr(36), "")
    c = _hash_client(monkeypatch, tmp_db, mangled)
    resp = c.get("/", headers=_auth())
    assert resp.status_code == 503
    assert b"ADMIN_PASSWORD_HASH" in resp.data


def test_mangled_hash_blocks_even_without_credentials(monkeypatch, tmp_db):
    from werkzeug.security import generate_password_hash
    mangled = generate_password_hash("secret").replace(chr(36), "")
    c = _hash_client(monkeypatch, tmp_db, mangled)
    assert c.get("/").status_code == 503


def test_mangled_hash_does_not_block_public_routes(monkeypatch, tmp_db):
    from werkzeug.security import generate_password_hash
    mangled = generate_password_hash("secret").replace(chr(36), "")
    c = _hash_client(monkeypatch, tmp_db, mangled)
    assert c.get("/healthz").status_code == 200


def test_check_admin_config_reports_mangled_hash(monkeypatch):
    from werkzeug.security import generate_password_hash
    from web.auth import check_admin_config
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH",
                       generate_password_hash("secret").replace(chr(36), ""))
    assert check_admin_config() is not None


def test_check_admin_config_silent_when_hash_is_valid(monkeypatch):
    from werkzeug.security import generate_password_hash
    from web.auth import check_admin_config
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", generate_password_hash("secret"))
    assert check_admin_config() is None
