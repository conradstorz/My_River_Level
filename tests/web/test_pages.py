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
