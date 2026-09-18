import base64

import pytest
from unittest.mock import patch
from twilio.request_validator import RequestValidator
from db.models import init_db, set_setting
from monitor import search_cache

# The portal is behind HTTP Basic auth; the /admin routes below need it.
ADMIN_AUTH = "Basic " + base64.b64encode(b"admin:testpass").decode()

TWILIO_TOKEN = "test-auth-token"


def _twilio_post(client, path, data):
    """POST to a Twilio webhook with a real X-Twilio-Signature.

    The signature is computed by Twilio's own RequestValidator, so the request
    exercises the production verification path rather than bypassing it.
    """
    db_path = client.application.config["DB_PATH"]
    set_setting("twilio_auth_token", TWILIO_TOKEN, db_path)
    signature = RequestValidator(TWILIO_TOKEN).compute_signature(
        "http://localhost" + path, data
    )
    return client.post(path, data=data, headers={"X-Twilio-Signature": signature})


@pytest.fixture(autouse=True)
def _clear_search_cache():
    # The paging cache is a module-global TTL store; reset it between tests so
    # one test's cached pool can't satisfy another test's mocked search.
    search_cache.clear()
    yield
    search_cache.clear()


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    from web.app import create_app
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base["HTTP_AUTHORIZATION"] = ADMIN_AUTH
        yield c


def _make_edit_token(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    _pub, edit = create_user_page("Gauge Test Page", db_path)
    return edit


def _noaa_pool(*a, **k):
    cands = [{"lid": "MLUK2", "name": "McAlpine Upper",
              "waterbody": "Ohio River", "state": "KY"},
             {"lid": "MLPK2", "name": "McAlpine Lower",
              "waterbody": "Ohio River", "state": "KY"}]
    return cands, False, ""


def test_editor_noaa_search_lists_gauges_by_noaa_name(client):
    edit_token = _make_edit_token(client)
    with patch("web.routes.search_noaa_gauges_by_name", side_effect=_noaa_pool):
        resp = client.get(f"/edit/{edit_token}/gauges/search?q=mcalpine+upper")
    body = resp.data.decode()
    assert "McAlpine Upper" in body and "MLUK2" in body


def test_editor_noaa_search_paginates(client):
    edit_token = _make_edit_token(client)
    many = ([{"lid": f"L{i}", "name": f"GAUGE {i}", "waterbody": "R", "state": "KY"}
             for i in range(30)], False, "")
    with patch("web.routes.search_noaa_gauges_by_name", return_value=many):
        resp = client.get(f"/edit/{edit_token}/gauges/search?q=gauge&page=2")
    body = resp.data.decode()
    assert "GAUGE 25" in body and "GAUGE 29" in body
    assert "Page 2 of 2" in body


def test_editor_noaa_search_survives_failure(client):
    edit_token = _make_edit_token(client)
    with patch("web.routes.search_noaa_gauges_by_name",
               return_value=([], False, "NOAA search failed. Please try again later.")):
        resp = client.get(f"/edit/{edit_token}/gauges/search?q=x",
                          follow_redirects=True)
    assert resp.status_code == 200
    assert b"NOAA search failed" in resp.data


def _meta_ok(identifier, timeout=10):
    return {"lid": "MLUK2", "station_name": "Ohio River at McAlpine",
            "action_stage": 21.0, "minor_flood_stage": 23.0,
            "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}


def test_page_add_gauge_accepts_usgs_number_and_stores_lid(client, tmp_db):
    edit_token = _make_edit_token(client)
    with patch("web.routes.fetch_gauge_metadata", side_effect=_meta_ok):
        client.post(f"/edit/{edit_token}/gauges/add",
                    data={"lid": "03293551"}, follow_redirects=True)
    from db.models import get_all_noaa_gauges
    gauges = get_all_noaa_gauges(tmp_db)
    assert any(g["lid"] == "MLUK2" for g in gauges)  # canonical LID stored


def test_new_page_get(client):
    resp = client.get("/pages/new")
    assert resp.status_code == 200
    assert b"Create" in resp.data


def test_new_page_post_shows_tokens(client):
    resp = client.post("/pages/new", data={"page_name": "Test Page"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"/view/" in resp.data
    assert b"/edit/" in resp.data


def test_new_page_missing_name(client):
    resp = client.post("/pages/new", data={"page_name": ""}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"required" in resp.data.lower() or b"name" in resp.data.lower()


def test_view_page_not_found(client):
    resp = client.get("/view/doesnotexist")
    assert resp.status_code == 404


def test_view_page_disabled(client):
    from db.models import create_user_page, get_db
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Disabled", db_path)
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE user_pages SET active=0 WHERE public_token=%s", (pub,))
    conn.commit()
    cur.close()
    conn.close()
    resp = client.get(f"/view/{pub}")
    assert resp.status_code == 404


def test_view_page_ok(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("My Page", db_path)
    resp = client.get(f"/view/{pub}")
    assert resp.status_code == 200
    assert b"My Page" in resp.data


def test_view_page_shows_gauge(client):
    from db.models import create_user_page, get_page_by_public_token, get_or_create_noaa_gauge, link_page_gauge
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("River Watch", db_path)
    page = get_page_by_public_token(pub, db_path)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, db_path)
    link_page_gauge(page["id"], gid, db_path)
    resp = client.get(f"/view/{pub}")
    assert resp.status_code == 200
    assert b"MLUK2" in resp.data or b"McAlpine" in resp.data
    assert b"mluk2_hg.png" in resp.data


def test_edit_page_not_found(client):
    resp = client.get("/edit/doesnotexist")
    assert resp.status_code == 404


def test_edit_page_ok(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    resp = client.get(f"/edit/{edit}")
    assert resp.status_code == 200
    assert b"Test" in resp.data


def test_add_gauge_to_page(client):
    from unittest.mock import patch
    from db.models import create_user_page, get_page_by_edit_token, get_page_gauges
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    page = get_page_by_edit_token(edit, db_path)
    mock_meta = {
        "lid": "MLUK2", "station_name": "Ohio River at McAlpine Upper",
        "action_stage": 21.0, "minor_flood_stage": 23.0,
        "moderate_flood_stage": 30.0, "major_flood_stage": 38.0,
    }
    with patch("web.routes.fetch_gauge_metadata", return_value=mock_meta):
        resp = client.post(f"/edit/{edit}/gauges/add",
                           data={"lid": "MLUK2"}, follow_redirects=True)
    assert resp.status_code == 200
    gauges = get_page_gauges(page["id"], db_path)
    assert len(gauges) == 1
    assert gauges[0]["lid"] == "MLUK2"


def test_add_gauge_invalid_lid(client):
    from unittest.mock import patch
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    with patch("web.routes.fetch_gauge_metadata", return_value=None):
        resp = client.post(f"/edit/{edit}/gauges/add",
                           data={"lid": "BADLID"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"not found" in resp.data.lower() or b"invalid" in resp.data.lower()


def test_remove_gauge_from_page(client):
    from unittest.mock import patch
    from db.models import create_user_page, get_page_by_edit_token, get_page_gauges
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    page = get_page_by_edit_token(edit, db_path)
    mock_meta = {
        "lid": "MLUK2", "station_name": "Ohio River at McAlpine Upper",
        "action_stage": 21.0, "minor_flood_stage": 23.0,
        "moderate_flood_stage": 30.0, "major_flood_stage": 38.0,
    }
    with patch("web.routes.fetch_gauge_metadata", return_value=mock_meta):
        client.post(f"/edit/{edit}/gauges/add", data={"lid": "MLUK2"}, follow_redirects=True)
    gauges = get_page_gauges(page["id"], db_path)
    gauge_id = gauges[0]["id"]
    resp = client.post(f"/edit/{edit}/gauges/remove",
                       data={"gauge_id": gauge_id}, follow_redirects=True)
    assert resp.status_code == 200
    assert get_page_gauges(page["id"], db_path) == []


def test_subscribe_to_page(client):
    from db.models import create_user_page, get_page_by_edit_token, get_active_page_subscribers
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    page = get_page_by_edit_token(edit, db_path)
    resp = client.post(f"/edit/{edit}/subscribe",
                       data={"channel": "telegram", "channel_id": "12345678", "display_name": "Alice"},
                       follow_redirects=True)
    assert resp.status_code == 200
    subs = get_active_page_subscribers(page["id"], db_path)
    assert len(subs) == 1
    assert subs[0]["channel_id"] == "12345678"


def test_unsubscribe_from_page(client):
    from db.models import create_user_page, get_page_by_edit_token, add_page_subscriber, get_active_page_subscribers
    db_path = client.application.config["DB_PATH"]
    _, edit = create_user_page("Test", db_path)
    page = get_page_by_edit_token(edit, db_path)
    add_page_subscriber(page["id"], "telegram", "12345678", "Alice", db_path)
    resp = client.post(f"/edit/{edit}/unsubscribe",
                       data={"channel": "telegram", "channel_id": "12345678", "status": "unsubscribed"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert get_active_page_subscribers(page["id"], db_path) == []


def test_admin_pages_list(client):
    from db.models import create_user_page
    db_path = client.application.config["DB_PATH"]
    create_user_page("Alpha", db_path)
    create_user_page("Beta", db_path)
    resp = client.get("/admin/pages")
    assert resp.status_code == 200
    assert b"Alpha" in resp.data
    assert b"Beta" in resp.data


def test_admin_toggle_page_disables(client):
    from db.models import create_user_page, get_page_by_public_token
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Togglable", db_path)
    page = get_page_by_public_token(pub, db_path)
    resp = client.post(f"/admin/pages/{page['id']}/toggle", follow_redirects=True)
    assert resp.status_code == 200
    updated = get_page_by_public_token(pub, db_path)
    assert updated["active"] == 0


def test_admin_toggle_page_enables(client):
    from db.models import create_user_page, get_page_by_public_token, get_db
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Disabled", db_path)
    page = get_page_by_public_token(pub, db_path)
    # Disable first
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE user_pages SET active=0 WHERE id=%s", (page["id"],))
    conn.commit()
    cur.close()
    conn.close()
    # Toggle back to active
    resp = client.post(f"/admin/pages/{page['id']}/toggle", follow_redirects=True)
    assert resp.status_code == 200
    updated = get_page_by_public_token(pub, db_path)
    assert updated["active"] == 1


def test_twilio_pause_page_subscriber(client):
    from db.models import (create_user_page, get_page_by_public_token,
                           add_page_subscriber, get_db)
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Test", db_path)
    page = get_page_by_public_token(pub, db_path)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", db_path)

    resp = _twilio_post(client, "/webhook/twilio",
                        {"From": "+15025551234", "Body": "PAUSE", "To": "+18005550000"})
    assert resp.status_code == 200
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT status FROM page_subscribers WHERE channel_id=%s", ("+15025551234",)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row["status"] == "paused"


def test_twilio_resume_page_subscriber(client):
    from db.models import (create_user_page, get_page_by_public_token,
                           add_page_subscriber, set_page_subscriber_status, get_db)
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Test", db_path)
    page = get_page_by_public_token(pub, db_path)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Bob", db_path)
    set_page_subscriber_status(page["id"], "sms", "+15025551234", "paused", db_path)

    resp = _twilio_post(client, "/webhook/twilio",
                        {"From": "+15025551234", "Body": "RESUME", "To": "+18005550000"})
    assert resp.status_code == 200
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT status FROM page_subscribers WHERE channel_id=%s", ("+15025551234",)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row["status"] == "active"


def test_twilio_stop_also_updates_page_subscribers(client):
    from db.models import (create_user_page, get_page_by_public_token,
                           add_page_subscriber, get_db)
    db_path = client.application.config["DB_PATH"]
    pub, _ = create_user_page("Test", db_path)
    page = get_page_by_public_token(pub, db_path)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Carol", db_path)

    resp = _twilio_post(client, "/webhook/twilio",
                        {"From": "+15025551234", "Body": "STOP", "To": "+18005550000"})
    assert resp.status_code == 200
    conn = get_db(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT status FROM page_subscribers WHERE channel_id=%s", ("+15025551234",)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row["status"] == "unsubscribed"


# --- USGS river gauges attached to a landing page -------------------------

def _anon(client):
    """A client for the same app that sends no Authorization header.

    The /edit/<token> family is authenticated by the unguessable token in the
    URL, not by the portal's Basic auth, so these routes must work for a page
    owner who has no admin password.
    """
    return client.application.test_client()


_VALID_USGS = (True, "OHIO RIVER AT LOUISVILLE, KY", "")


def test_page_add_site_links_site_to_page_without_admin_auth(client):
    from db.models import get_page_by_edit_token, get_page_sites
    db_path = client.application.config["DB_PATH"]
    edit = _make_edit_token(client)
    page = get_page_by_edit_token(edit, db_path)
    with patch("web.routes.validate_usgs_site", return_value=_VALID_USGS):
        resp = _anon(client).post(f"/edit/{edit}/sites/add",
                                  data={"site_number": "03294500",
                                        "parameter_code": "00065"},
                                  follow_redirects=True)
    assert resp.status_code == 200
    sites = get_page_sites(page["id"], db_path)
    assert [s["site_number"] for s in sites] == ["03294500"]


def test_page_add_site_rejects_invalid_site_number(client):
    from db.models import get_page_by_edit_token, get_page_sites
    db_path = client.application.config["DB_PATH"]
    edit = _make_edit_token(client)
    page = get_page_by_edit_token(edit, db_path)
    with patch("web.routes.validate_usgs_site",
               return_value=(False, "", "Site not found")):
        resp = _anon(client).post(f"/edit/{edit}/sites/add",
                                  data={"site_number": "99999999"},
                                  follow_redirects=True)
    assert resp.status_code == 200
    assert get_page_sites(page["id"], db_path) == []


def test_page_edit_lists_linked_usgs_sites(client):
    from db.models import get_page_by_edit_token
    edit = _make_edit_token(client)
    with patch("web.routes.validate_usgs_site", return_value=_VALID_USGS):
        _anon(client).post(f"/edit/{edit}/sites/add",
                           data={"site_number": "03294500", "parameter_code": "00065"})
    body = _anon(client).get(f"/edit/{edit}").data.decode()
    assert "03294500" in body
    assert "OHIO RIVER AT LOUISVILLE, KY" in body


def test_page_remove_site_unlinks_it_without_admin_auth(client):
    from db.models import get_page_by_edit_token, get_page_sites
    db_path = client.application.config["DB_PATH"]
    edit = _make_edit_token(client)
    page = get_page_by_edit_token(edit, db_path)
    with patch("web.routes.validate_usgs_site", return_value=_VALID_USGS):
        _anon(client).post(f"/edit/{edit}/sites/add",
                           data={"site_number": "03294500", "parameter_code": "00065"})
    site_id = get_page_sites(page["id"], db_path)[0]["id"]
    resp = _anon(client).post(f"/edit/{edit}/sites/remove",
                              data={"site_id": site_id}, follow_redirects=True)
    assert resp.status_code == 200
    assert get_page_sites(page["id"], db_path) == []


def test_page_add_site_unknown_token_404s(client):
    with patch("web.routes.validate_usgs_site", return_value=_VALID_USGS):
        resp = _anon(client).post("/edit/doesnotexist/sites/add",
                                  data={"site_number": "03294500"})
    assert resp.status_code == 404


# --- flood-prediction grade ----------------------------------------------

def _page_with_gauge(client, name="River Watch", lid="MLUK2"):
    from db.models import (create_user_page, get_page_by_public_token,
                           get_or_create_noaa_gauge, link_page_gauge)
    db_path = client.application.config["DB_PATH"]
    pub, edit = create_user_page(name, db_path)
    page = get_page_by_public_token(pub, db_path)
    gid = get_or_create_noaa_gauge(lid, "Ohio River at McAlpine Upper",
                                   21.0, 23.0, 30.0, 38.0, db_path)
    link_page_gauge(page["id"], gid, db_path)
    return pub, edit


def test_view_page_shows_gauge_grade_headline_and_detail(client):
    from db.models import set_gauge_quality
    from monitor.gauge_quality import HEADLINE_SEPARATOR
    db_path = client.application.config["DB_PATH"]
    pub, _ = _page_with_gauge(client)
    set_gauge_quality(
        "MLUK2", "B",
        "Good" + HEADLINE_SEPARATOR +
        "24-hour forecasts are off by 0.82 ft on average (14 forecasts checked).",
        db_path)
    body = _anon(client).get(f"/view/{pub}").data.decode()
    assert ">B<" in body
    assert "Good" in body
    assert "off by 0.82 ft on average" in body


def test_view_page_shows_unrated_for_a_gauge_that_was_never_scored(client):
    pub, _ = _page_with_gauge(client)
    body = _anon(client).get(f"/view/{pub}").data.decode()
    assert "Unrated" in body
    assert "not yet assessed" in body.lower()


def test_edit_page_shows_gauge_grade(client):
    from db.models import set_gauge_quality
    from monitor.gauge_quality import HEADLINE_SEPARATOR
    db_path = client.application.config["DB_PATH"]
    _pub, edit = _page_with_gauge(client)
    set_gauge_quality("MLUK2", "F",
                      "Not reporting" + HEADLINE_SEPARATOR +
                      "No readings in the last 24 hours.",
                      db_path)
    body = _anon(client).get(f"/edit/{edit}").data.decode()
    assert ">F<" in body
    assert "No readings in the last 24 hours." in body


def test_admin_pages_shows_pin_owner_and_status(client, tmp_db):
    from db.models import create_pin_page, save_pin
    page = create_pin_page(4242, tmp_db)
    save_pin(page["id"], 38.28, -85.76, "Ohio River", "floods",
             [{"site_number": "03294500", "station_name": "Ohio", "parameter_code": "00065"}],
             [], tmp_db)
    body = client.get("/admin/pages").data.decode()
    assert "Ohio River" in body and "4242" in body
    assert "38.28" in body and "-85.76" in body
    assert "active" in body.lower()
