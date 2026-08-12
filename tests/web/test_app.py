import base64

import pytest
from db.models import init_db
from web.app import create_app

# The portal is behind HTTP Basic auth; these tests hit protected routes.
ADMIN_AUTH = "Basic " + base64.b64encode(b"admin:testpass").decode()

@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base["HTTP_AUTHORIZATION"] = ADMIN_AUTH
        yield c

def test_dashboard_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200

def test_subscribers_page_returns_200(client):
    response = client.get("/subscribers")
    assert response.status_code == 200

def test_sites_page_returns_200(client):
    response = client.get("/sites")
    assert response.status_code == 200

def test_settings_page_returns_200(client):
    response = client.get("/settings", follow_redirects=True)
    assert response.status_code == 200

def test_broadcast_page_returns_200(client):
    response = client.get("/broadcast")
    assert response.status_code == 200
