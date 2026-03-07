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
