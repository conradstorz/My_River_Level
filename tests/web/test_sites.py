import pytest
from db.models import init_db, get_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_sites_page_lists_sites(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number, station_name) VALUES ('03277200', 'Salt River')")
    conn.commit()
    conn.close()
    response = client.get("/sites")
    assert b"Salt River" in response.data

def test_add_site_post(client):
    response = client.post("/sites/add", data={
        "site_number": "03277200",
        "station_name": "Salt River",
        "parameter_code": "00065",
    }, follow_redirects=True)
    assert response.status_code == 200

def test_toggle_site_active(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number, active) VALUES (1, '03277200', 1)")
    conn.commit()
    conn.close()
    client.post("/sites/1/toggle")
    conn = get_db(tmp_db)
    row = conn.execute("SELECT active FROM sites WHERE id=1").fetchone()
    conn.close()
    assert row["active"] == 0
