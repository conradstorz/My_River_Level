import pytest
from db.models import init_db


@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    from web.app import create_app
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


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
    conn.execute("UPDATE user_pages SET active=0 WHERE public_token=?", (pub,))
    conn.commit()
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
        "station_name": "Ohio River at McAlpine Upper",
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
        "station_name": "Ohio River at McAlpine Upper",
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
