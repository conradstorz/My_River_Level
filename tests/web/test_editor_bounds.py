"""Bounds on the public Page editor: user-origin provisioning, per-Page cap,
per-visitor rate limits (#16)."""

from unittest.mock import patch

import pytest

from db.models import (create_user_page, get_db, get_or_create_noaa_gauge,
                       get_page_by_edit_token, get_page_gauges, get_page_sites,
                       init_db, link_page_gauge, link_page_site)
from monitor import retirement, search_cache
from web import routes as routes_mod


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    search_cache.clear()
    from web.app import create_app
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c          # no admin auth: the editor is public, token-guarded
    search_cache.clear()


@pytest.fixture
def page(tmp_db):
    _pub, edit = create_user_page("Editor Bounds", tmp_db)
    return get_page_by_edit_token(edit, tmp_db)


def _meta(lid):
    return {"lid": lid, "station_name": f"Gauge {lid}", "action_stage": 21.0,
            "minor_flood_stage": 23.0, "moderate_flood_stage": 30.0,
            "major_flood_stage": 38.0}


def _row(tmp_db, sql, params):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        conn.commit()
        return cur.fetchone() if cur.description else None
    finally:
        cur.close()
        conn.close()


def _count(tmp_db, table):
    return _row(tmp_db, f"SELECT COUNT(*) AS n FROM {table}", ())["n"]


def _add_gauge(client, page, lid):
    with patch("web.routes.fetch_gauge_metadata", side_effect=lambda i, timeout=10: _meta(lid)):
        return client.post(f"/edit/{page['edit_token']}/gauges/add", data={"lid": lid})


def _add_site(client, page, number):
    with patch("web.routes.validate_usgs_site", return_value=(True, f"SITE {number}", "")):
        return client.post(f"/edit/{page['edit_token']}/sites/add",
                           data={"site_number": number, "parameter_code": "00065"})


# --- origin ---------------------------------------------------------------

def test_editor_added_gauge_is_user_origin_and_retires_once_unreferenced(client, tmp_db, page):
    _add_gauge(client, page, "MLUK2")
    gauge = _row(tmp_db, "SELECT id, origin, active FROM noaa_gauges WHERE lid=%s", ("MLUK2",))
    assert gauge["origin"] == "user" and gauge["active"] == 1

    client.post(f"/edit/{page['edit_token']}/gauges/remove", data={"gauge_id": gauge["id"]})
    retirement.sweep(tmp_db)
    assert _row(tmp_db, "SELECT active FROM noaa_gauges WHERE lid=%s", ("MLUK2",))["active"] == 0


def test_editor_added_site_is_user_origin_and_retires_once_unreferenced(client, tmp_db, page):
    _add_site(client, page, "03294500")
    site = _row(tmp_db, "SELECT id, origin, active FROM sites WHERE site_number=%s", ("03294500",))
    assert site["origin"] == "user" and site["active"] == 1

    client.post(f"/edit/{page['edit_token']}/sites/remove", data={"site_id": site["id"]})
    _row(tmp_db, "UPDATE sites SET added_at=(NOW() - INTERVAL '1 hour')::TEXT "
                 "WHERE site_number=%s", ("03294500",))   # past the sweep's grace window
    retirement.sweep(tmp_db)
    assert _row(tmp_db, "SELECT active FROM sites WHERE site_number=%s", ("03294500",))["active"] == 0


def test_editor_add_keeps_an_existing_admin_gauge_admin(client, tmp_db, page):
    get_or_create_noaa_gauge("MLUK2", "Admin gauge", 1, 2, 3, 4, tmp_db)   # admin origin
    _add_gauge(client, page, "MLUK2")
    gauge = _row(tmp_db, "SELECT id, origin FROM noaa_gauges WHERE lid=%s", ("MLUK2",))
    assert gauge["origin"] == "admin"
    assert [g["lid"] for g in get_page_gauges(page["id"], tmp_db)] == ["MLUK2"]


def test_editor_add_keeps_an_existing_admin_site_admin(client, tmp_db, page):
    _row(tmp_db, "INSERT INTO sites (site_number, station_name, parameter_code) "
                 "VALUES (%s, %s, %s)", ("03294500", "Admin site", "00065"))
    _add_site(client, page, "03294500")
    site = _row(tmp_db, "SELECT origin FROM sites WHERE site_number=%s", ("03294500",))
    assert site["origin"] == "admin"
    assert [s["site_number"] for s in get_page_sites(page["id"], tmp_db)] == ["03294500"]


def test_editor_readd_reactivates_a_retired_user_site(client, tmp_db, page):
    _row(tmp_db, "INSERT INTO sites (site_number, station_name, parameter_code, origin, active) "
                 "VALUES (%s, %s, %s, 'user', 0)", ("03294500", "Retired", "00065"))
    _add_site(client, page, "03294500")
    site = _row(tmp_db, "SELECT origin, active FROM sites WHERE site_number=%s", ("03294500",))
    assert (site["origin"], site["active"]) == ("user", 1)


def test_editor_add_does_not_reenable_an_admin_site_switched_off(client, tmp_db, page):
    _row(tmp_db, "INSERT INTO sites (site_number, station_name, parameter_code, active) "
                 "VALUES (%s, %s, %s, 0)", ("03294500", "Admin off", "00065"))
    _add_site(client, page, "03294500")
    site = _row(tmp_db, "SELECT origin, active FROM sites WHERE site_number=%s", ("03294500",))
    assert (site["origin"], site["active"]) == ("admin", 0)


# --- per-Page cap ---------------------------------------------------------

def _fill_page(tmp_db, page, n):
    """Link n sources (half sites, half gauges) to the page directly."""
    for i in range(n):
        if i % 2:
            gid = get_or_create_noaa_gauge(f"CAP{i}", f"Cap {i}", 1, 2, 3, 4, tmp_db)
            link_page_gauge(page["id"], gid, tmp_db)
        else:
            row = _row(tmp_db, "INSERT INTO sites (site_number, station_name, parameter_code) "
                               "VALUES (%s, %s, %s) RETURNING id", (f"9{i:07d}", "Cap", "00065"))
            link_page_site(page["id"], row["id"], tmp_db)


def test_gauge_add_beyond_cap_is_refused_with_flash_and_inserts_nothing(client, tmp_db, page):
    _fill_page(tmp_db, page, routes_mod.MAX_SOURCES_PER_PAGE)
    gauges_before = _count(tmp_db, "noaa_gauges")
    with patch("web.routes.fetch_gauge_metadata", side_effect=lambda i, timeout=10: _meta("NEW1")) as m:
        resp = client.post(f"/edit/{page['edit_token']}/gauges/add",
                           data={"lid": "NEW1"}, follow_redirects=True)
    assert resp.status_code == 200
    assert f"maximum of {routes_mod.MAX_SOURCES_PER_PAGE}" in resp.data.decode()
    assert _count(tmp_db, "noaa_gauges") == gauges_before
    assert _count(tmp_db, "page_noaa_gauges") + _count(tmp_db, "page_sites") == \
        routes_mod.MAX_SOURCES_PER_PAGE
    m.assert_not_called()      # refused before any upstream lookup


def test_site_add_beyond_cap_is_refused_with_flash_and_inserts_nothing(client, tmp_db, page):
    _fill_page(tmp_db, page, routes_mod.MAX_SOURCES_PER_PAGE)
    sites_before = _count(tmp_db, "sites")
    with patch("web.routes.validate_usgs_site", return_value=(True, "NEW", "")) as v:
        resp = client.post(f"/edit/{page['edit_token']}/sites/add",
                           data={"site_number": "03294500"}, follow_redirects=True)
    assert resp.status_code == 200
    assert f"maximum of {routes_mod.MAX_SOURCES_PER_PAGE}" in resp.data.decode()
    assert _count(tmp_db, "sites") == sites_before
    assert len(get_page_sites(page["id"], tmp_db)) == (routes_mod.MAX_SOURCES_PER_PAGE + 1) // 2
    v.assert_not_called()


def test_add_just_under_cap_succeeds(client, tmp_db, page):
    _fill_page(tmp_db, page, routes_mod.MAX_SOURCES_PER_PAGE - 1)
    _add_gauge(client, page, "LAST1")
    assert "LAST1" in [g["lid"] for g in get_page_gauges(page["id"], tmp_db)]


# --- per-visitor rate limits ----------------------------------------------

def test_editor_writes_are_throttled_per_visitor(client, tmp_db, page):
    limit = routes_mod.EDITOR_WRITE_VISITOR_LIMITER.limit
    url = f"/edit/{page['edit_token']}/sensitivity"
    a = {"REMOTE_ADDR": "203.0.113.7"}
    for _ in range(limit):
        assert client.post(url, data={"sensitivity": "floods"}, environ_base=a).status_code == 302
    assert client.post(url, data={"sensitivity": "floods"}, environ_base=a).status_code == 429
    # a different visitor is unaffected
    assert client.post(url, data={"sensitivity": "floods"},
                       environ_base={"REMOTE_ADDR": "203.0.113.8"}).status_code == 302


def test_throttled_add_inserts_nothing(client, tmp_db, page):
    a = {"REMOTE_ADDR": "203.0.113.9"}
    for _ in range(routes_mod.EDITOR_WRITE_VISITOR_LIMITER.limit):
        client.post(f"/edit/{page['edit_token']}/sensitivity",
                    data={"sensitivity": "floods"}, environ_base=a)
    with patch("web.routes.fetch_gauge_metadata", side_effect=lambda i, timeout=10: _meta("NEW2")) as m:
        resp = client.post(f"/edit/{page['edit_token']}/gauges/add",
                           data={"lid": "NEW2"}, environ_base=a)
    assert resp.status_code == 429
    assert _count(tmp_db, "noaa_gauges") == 0
    m.assert_not_called()


def test_editor_limit_spans_tokens(client, tmp_db, page):
    """Minting more pages does not reset a visitor's editor budget."""
    _pub, other = create_user_page("Other", tmp_db)
    a = {"REMOTE_ADDR": "203.0.113.10"}
    for _ in range(routes_mod.EDITOR_WRITE_VISITOR_LIMITER.limit):
        client.post(f"/edit/{page['edit_token']}/sensitivity",
                    data={"sensitivity": "floods"}, environ_base=a)
    assert client.post(f"/edit/{other}/sensitivity", data={"sensitivity": "floods"},
                       environ_base=a).status_code == 429


def test_gauge_search_is_throttled_per_visitor(client, page):
    limit = routes_mod.GAUGE_SEARCH_VISITOR_LIMITER.limit
    url = f"/edit/{page['edit_token']}/gauges/search?q=ohio"
    a = {"REMOTE_ADDR": "198.51.100.4"}
    with patch("web.routes.search_noaa_gauges_by_name", return_value=([], False, "")):
        for _ in range(limit):
            assert client.get(url, environ_base=a).status_code == 200
        assert client.get(url, environ_base=a).status_code == 429
        assert client.get(url, environ_base={"REMOTE_ADDR": "198.51.100.5"}).status_code == 200

