import pytest
from unittest.mock import patch
import pandas as pd
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

def _usgs_valid(*args, **kwargs):
    df = pd.DataFrame({"station_nm": ["OHIO RIVER AT LOUISVILLE, KY"], "site_no": ["03294500"]})
    return df, {}


def _usgs_not_found(*args, **kwargs):
    raise Exception("Page Not Found Error. May be the result of an empty query.")


def test_add_site_post(client):
    """Valid site number (mocked USGS) inserts the site and uses USGS station name."""
    with patch("monitor.site_validation.nwis.get_info", side_effect=_usgs_valid):
        response = client.post("/sites/add", data={
            "site_number": "03294500",
            "parameter_code": "00065",
        }, follow_redirects=True)
    assert response.status_code == 200
    assert b"03294500" in response.data


def test_add_site_rejects_invalid_usgs_number(client, tmp_db):
    """A site number that doesn't exist in USGS must be rejected — not inserted."""
    with patch("monitor.site_validation.nwis.get_info", side_effect=_usgs_not_found):
        response = client.post("/sites/add", data={
            "site_number": "10164002",
            "parameter_code": "00065",
        }, follow_redirects=True)
    assert response.status_code == 200
    conn = get_db(tmp_db)
    row = conn.execute("SELECT * FROM sites WHERE site_number='10164002'").fetchone()
    conn.close()
    assert row is None, "Invalid site must not be inserted into the database"


def test_add_site_uses_usgs_station_name_not_user_input(client, tmp_db):
    """Station name comes from USGS, not from the user's form input."""
    with patch("monitor.site_validation.nwis.get_info", side_effect=_usgs_valid):
        client.post("/sites/add", data={
            "site_number": "03294500",
            "station_name": "User Typed Wrong Name",
            "parameter_code": "00065",
        }, follow_redirects=True)
    conn = get_db(tmp_db)
    row = conn.execute("SELECT station_name FROM sites WHERE site_number='03294500'").fetchone()
    conn.close()
    assert row is not None
    assert row["station_name"] == "OHIO RIVER AT LOUISVILLE, KY"

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
