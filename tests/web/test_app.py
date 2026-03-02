import pytest
from db.models import init_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
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
    response = client.get("/settings")
    assert response.status_code == 200

def test_broadcast_page_returns_200(client):
    response = client.get("/broadcast")
    assert response.status_code == 200
