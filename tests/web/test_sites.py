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


def _search_result(*args, **kwargs):
    matches = [{
        "number": "03294500",
        "name": "OHIO RIVER AT LOUISVILLE, KY",
        "state": "Kentucky",
        "site_type": "Stream",
    }]
    return matches, False, ""


def _identity(matches, *args, **kwargs):
    return matches


def test_search_renders_matches_dropdown(client):
    with patch("web.routes.search_sites_by_name", side_effect=_search_result), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        response = client.post("/sites/search", data={"gauge_name": "ohio river"})
    assert response.status_code == 200
    assert b"OHIO RIVER AT LOUISVILLE, KY" in response.data
    assert b"03294500" in response.data
    assert b'name="site_number"' in response.data


def test_search_no_matches_flashes(client):
    with patch("web.routes.search_sites_by_name", return_value=([], False, "")):
        response = client.post(
            "/sites/search", data={"gauge_name": "zzzznotagauge"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert b"No gauges found" in response.data


def test_search_api_error_flashes(client):
    with patch("web.routes.search_sites_by_name",
               return_value=([], False, "USGS search failed. Please try again later.")):
        response = client.post(
            "/sites/search", data={"gauge_name": "ohio"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    assert b"USGS search failed" in response.data


def test_search_empty_query_flashes(client):
    response = client.post(
        "/sites/search", data={"gauge_name": "   "},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Enter a gauge name" in response.data


def test_search_truncated_shows_hint(client):
    with patch("web.routes.search_sites_by_name",
               return_value=(_search_result()[0], True, "")), \
         patch("web.routes.annotate_liveness", side_effect=_identity), \
         patch("web.routes.annotate_noaa", side_effect=_identity):
        response = client.post("/sites/search", data={"gauge_name": "creek"})
    assert b"25 best matches" in response.data


def _enriched_search(*args, **kwargs):
    matches = [
        {"number": "03293551", "name": "OHIO RIVER AT MCALPINE",
         "state": "Kentucky", "site_type": "Stream"},
        {"number": "09999999", "name": "DEAD CREEK",
         "state": "Kentucky", "site_type": "Stream"},
    ]
    return matches, False, ""


def _add_liveness(matches, *a, **k):
    for m in matches:
        if m["number"] == "03293551":
            m.update(live=True, last_value=12.8, last_time="2026-07-19T10:15")
        else:
            m.update(live=False, last_value=None, last_time=None)
    return matches


def _add_noaa(matches, *a, **k):
    for m in matches:
        if m["number"] == "03293551":
            m.update(noaa_lid="MLUK2", noaa_has_flood=True)
        else:
            m.update(noaa_lid=None, noaa_has_flood=False)
    return matches


def test_search_renders_liveness_and_noaa_badges(client):
    with patch("web.routes.search_sites_by_name", side_effect=_enriched_search), \
         patch("web.routes.annotate_liveness", side_effect=_add_liveness), \
         patch("web.routes.annotate_noaa", side_effect=_add_noaa):
        response = client.post("/sites/search", data={"gauge_name": "ohio"})
    assert response.status_code == 200
    body = response.data.decode()
    assert "OHIO RIVER AT MCALPINE" in body
    assert "Reporting" in body          # live badge
    assert "No recent data" in body     # dead badge
    assert "NOAA flood forecast" in body
    # Live gauge is sorted before the dead one.
    assert body.index("03293551") < body.index("09999999")


def test_search_survives_enrichment_failure(client):
    with patch("web.routes.search_sites_by_name", side_effect=_enriched_search), \
         patch("web.routes.annotate_liveness", side_effect=Exception("boom")), \
         patch("web.routes.annotate_noaa", side_effect=Exception("boom")):
        response = client.post("/sites/search", data={"gauge_name": "ohio"})
    assert response.status_code == 200
    assert b"OHIO RIVER AT MCALPINE" in response.data  # still renders
