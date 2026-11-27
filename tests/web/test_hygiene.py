"""Baseline web hygiene: request size, name validation, headers, SRI, cookies."""

import base64
import queue
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import pytest

from db.models import create_pin_page, get_page_by_edit_token, init_db
from web import routes as routes_mod

ADMIN_AUTH = "Basic " + base64.b64encode(b"admin:testpass").decode()

SECURITY_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

SAVE_BODY = {
    "lat": 38.28, "lon": -85.76, "river_name": "Ohio River", "sensitivity": "floods",
    "sources": [{"kind": "usgs", "id": "03294500", "parameter_code": "00065"}],
}


def _make_app(tmp_db, monkeypatch, trusted_proxies=None):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    if trusted_proxies is None:
        monkeypatch.delenv("TRUSTED_PROXY_COUNT", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_PROXY_COUNT", str(trusted_proxies))
    init_db(tmp_db)
    routes_mod.reset_limiters()
    from web.app import create_app
    app = create_app(db_path=tmp_db, notification_queue=queue.Queue())
    app.config["TESTING"] = True
    return app


@pytest.fixture
def app(tmp_db, monkeypatch):
    return _make_app(tmp_db, monkeypatch)


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _assert_security_headers(resp):
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers.get(name) == value, (name, resp.status_code)
    assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")


# --- request size -----------------------------------------------------------

def test_oversized_json_post_gets_413(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    body = {**SAVE_BODY, "river_name": "x" * (65 * 1024)}
    resp = client.post(f"/pin/{page['edit_token']}/save", json=body)
    assert resp.status_code == 413


def test_oversized_form_post_gets_413(client):
    resp = client.post("/pages/new", data={"page_name": "x" * (65 * 1024)})
    assert resp.status_code == 413


def test_oversized_post_gets_413_even_when_route_never_reads_body(client):
    resp = client.post("/pin/no-such-token/discover", data=b"x" * (65 * 1024),
                       content_type="application/octet-stream")
    assert resp.status_code == 413


def test_body_under_limit_is_accepted(client):
    resp = client.post("/pages/new", data={"page_name": "Ohio", "pad": "x" * 1000},
                       headers={"Authorization": ADMIN_AUTH})
    assert resp.status_code == 200


# --- page name --------------------------------------------------------------

def test_page_name_over_100_chars_gets_400(client):
    resp = client.post("/pages/new", data={"page_name": "x" * 101},
                       headers={"Authorization": ADMIN_AUTH})
    assert resp.status_code == 400
    assert "100 characters" in resp.data.decode()


def test_page_name_of_100_chars_after_trim_is_accepted(client):
    resp = client.post("/pages/new", data={"page_name": "   " + "x" * 100 + "   "},
                       headers={"Authorization": ADMIN_AUTH})
    assert resp.status_code == 200


# --- river name -------------------------------------------------------------

@pytest.mark.parametrize("bad", [123, ["Ohio"], {"n": "Ohio"}, True, "x" * 101])
def test_save_rejects_non_string_or_overlong_river_name(client, tmp_db, bad):
    page = create_pin_page(None, tmp_db)
    with patch("web.routes.validate_usgs_site") as v, \
         patch("web.routes.fetch_gauge_metadata") as m:
        resp = client.post(f"/pin/{page['edit_token']}/save",
                           json={**SAVE_BODY, "river_name": bad})
    assert resp.status_code == 400
    assert "river name" in resp.get_json()["error"].lower()
    v.assert_not_called()
    m.assert_not_called()


@pytest.mark.parametrize("value,stored", [
    ("  " + "r" * 100 + "  ", "r" * 100),
    (None, None),
    ("   ", None),
])
def test_save_accepts_valid_river_name(client, tmp_db, value, stored):
    page = create_pin_page(None, tmp_db)
    with patch("web.routes.validate_usgs_site",
               return_value=(True, "OHIO RIVER AT LOUISVILLE", "")):
        resp = client.post(f"/pin/{page['edit_token']}/save",
                           json={**SAVE_BODY, "river_name": value})
    assert resp.status_code == 200, resp.data
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["river_name"] == stored


# --- security headers -------------------------------------------------------

def test_headers_on_html_response(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    resp = client.get(f"/pin/{page['edit_token']}")
    assert resp.status_code == 200
    _assert_security_headers(resp)


def test_headers_on_json_response(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    resp = client.post(f"/pin/{page['edit_token']}/discover", json={"lat": 95})
    assert resp.status_code == 400 and resp.is_json
    _assert_security_headers(resp)


def test_headers_on_error_responses(client):
    for resp in (client.get("/pin/no-such-token"),        # 404
                 client.get("/settings"),                  # 401, no auth sent
                 client.post("/pages/new", data={"page_name": "x" * (65 * 1024)}),  # 413
                 client.get("/healthz")):
        _assert_security_headers(resp)


def test_headers_on_unhandled_500(tmp_db, monkeypatch):
    app = _make_app(tmp_db, monkeypatch)
    app.config["TESTING"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False

    def boom():
        raise RuntimeError("boom")
    app.add_url_rule("/boom", "boom", boom)
    with app.test_client() as c:
        resp = c.get("/boom", headers={"Authorization": ADMIN_AUTH})
    assert resp.status_code == 500
    _assert_security_headers(resp)


# --- subresource integrity --------------------------------------------------

# sha384 computed from the exact pinned CDN URLs; Leaflet's sha256 values are
# the ones Leaflet publishes and were re-verified against the same files.
PINNED_INTEGRITY = {
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css":
        "sha384-9ndCyUaIbzAi2FUVXJi0CjmCapSmO7SnpJef0486qhLnuZ2cdeRhO02iuK6FUUVM",
    "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js":
        "sha384-geWF76RCwLtnZ8qwWowPQNguL3RmwHVBC9FhGdlKrxdiJJigb/j/68SIy3Te4Bkz",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css":
        "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=",
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js":
        "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=",
}


class _AssetCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        url = a.get("src") if tag == "script" else a.get("href") if tag == "link" else None
        if url and url.startswith(("http://", "https://", "//")):
            self.assets.append((url, a))


def _third_party_assets():
    templates = Path(__file__).resolve().parents[2] / "web" / "templates"
    found = []
    for path in sorted(templates.rglob("*.html")):
        parser = _AssetCollector()
        parser.feed(path.read_text(encoding="utf-8"))
        found += [(path.name, url, attrs) for url, attrs in parser.assets]
    return found


def test_every_third_party_asset_has_matching_integrity_and_crossorigin():
    assets = _third_party_assets()
    assert assets, "expected the Bootstrap/Leaflet tags to be found"
    for name, url, attrs in assets:
        assert url in PINNED_INTEGRITY, f"{name}: unpinned third-party asset {url}"
        assert attrs.get("integrity") == PINNED_INTEGRITY[url], (name, url)
        assert "crossorigin" in attrs, (name, url)


# --- session cookie ---------------------------------------------------------

def _session_cookie_header(app):
    with app.test_client() as c:
        resp = c.post("/pages/new", data={"page_name": ""},   # flashes -> session
                      headers={"Authorization": ADMIN_AUTH})
    cookies = [h for h in resp.headers.getlist("Set-Cookie") if h.startswith("session=")]
    assert cookies, "expected a session cookie"
    return cookies[0]


def test_session_cookie_lax_and_not_secure_without_proxy(tmp_db, monkeypatch):
    header = _session_cookie_header(_make_app(tmp_db, monkeypatch))
    assert "SameSite=Lax" in header
    assert "Secure" not in header


def test_session_cookie_secure_behind_trusted_proxy(tmp_db, monkeypatch):
    header = _session_cookie_header(_make_app(tmp_db, monkeypatch, trusted_proxies=1))
    assert "SameSite=Lax" in header
    assert "Secure" in header
