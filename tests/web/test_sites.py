import pytest
from unittest.mock import patch
import pandas as pd
from db.models import init_db, get_db
from web.app import create_app
from monitor import search_cache


@pytest.fixture(autouse=True)
def _clear_search_cache():
    # The paging cache is a module-global TTL store; reset it between tests so
    # one test's cached pool can't satisfy another test's mocked search.
    search_cache.clear()
    yield
    search_cache.clear()


@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_sites_page_lists_sites(client, tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('03277200', 'Salt River')")
    conn.commit()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("SELECT * FROM sites WHERE site_number='10164002'")
    row = cur.fetchone()
    cur.close()
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
    cur = conn.cursor()
    cur.execute("SELECT station_name FROM sites WHERE site_number='03294500'")
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row is not None
    assert row["station_name"] == "OHIO RIVER AT LOUISVILLE, KY"

def test_toggle_site_active(client, tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (id, site_number, active) VALUES (1, '03277200', 1)")
    conn.commit()
    cur.close()
    conn.close()
    client.post("/sites/1/toggle")
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT active FROM sites WHERE id=1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    assert row["active"] == 0


def _identity(matches, *args, **kwargs):
    return matches


def _rows(n):
    return [{"number": f"{i:08d}", "name": f"CREEK {i}", "state": "KY",
             "site_type": "Stream", "noaa_name": None, "noaa_lid": None}
            for i in range(n)]


def test_get_search_page1_lists_first_25_with_pager(client):
    pool = (_rows(30), 0, False, "")
    with patch("web.routes.combined_site_matches", return_value=pool), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=creek&page=1")
    body = resp.data.decode()
    assert resp.status_code == 200
    assert "CREEK 0" in body and "CREEK 24" in body
    assert "CREEK 25" not in body            # page 2 content
    assert "Page 1 of 2" in body
    assert "30 matches" in body


def test_get_search_page2_shows_next_rows(client):
    pool = (_rows(30), 0, False, "")
    with patch("web.routes.combined_site_matches", return_value=pool), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=creek&page=2")
    body = resp.data.decode()
    assert "CREEK 25" in body and "CREEK 29" in body
    assert "Page 2 of 2" in body


def test_get_search_renders_noaa_tag(client):
    rows = [{"number": "03293551", "name": "OHIO R US OF MCALPINE DAM",
             "state": "KY", "site_type": "Stream",
             "noaa_name": "McAlpine Upper", "noaa_lid": "MLUK2"}]
    with patch("web.routes.combined_site_matches", return_value=(rows, 2, False, "")), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=mcalpine+upper")
    body = resp.data.decode()
    assert "McAlpine Upper" in body                  # NOAA tag
    assert "2 NOAA-only" in body                     # noaa_only note


def test_get_search_no_matches_flashes(client):
    with patch("web.routes.combined_site_matches", return_value=([], 0, False, "")):
        resp = client.get("/sites/search?q=zzzz", follow_redirects=True)
    assert b"No gauges found" in resp.data


def test_get_search_empty_query_flashes_without_calling_search(client):
    with patch("web.routes.combined_site_matches") as m:
        resp = client.get("/sites/search?q=%20%20", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Enter a gauge name" in resp.data
    m.assert_not_called()


def test_get_search_error_flashes(client):
    with patch("web.routes.combined_site_matches",
               return_value=([], 0, False, "USGS search failed. Please try again later.")):
        resp = client.get("/sites/search?q=x", follow_redirects=True)
    assert b"USGS search failed" in resp.data


def test_get_search_renders_liveness_badges(client):
    rows = [{"number": "03294500", "name": "OHIO RIVER AT LOUISVILLE, KY",
             "state": "KY", "site_type": "Stream", "noaa_name": None, "noaa_lid": None}]
    def _live(ms, *a, **k):
        for m in ms:
            m.update(live=True, last_value=63000.0, last_time="t")
        return ms
    with patch("web.routes.combined_site_matches", return_value=(rows, 0, False, "")), \
         patch("web.routes.annotate_liveness", side_effect=_live), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        resp = client.get("/sites/search?q=ohio")
    assert b"Reporting" in resp.data


