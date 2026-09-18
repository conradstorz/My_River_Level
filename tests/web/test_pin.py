import json
from unittest.mock import patch

import pytest

from db.models import (create_pin_page, get_db, get_page_by_edit_token,
                       get_page_gauges, get_page_sites, init_db, set_setting)
from monitor.pin_discovery import Candidate, Discovery
from web import routes as routes_mod


@pytest.fixture
def client(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    routes_mod.PIN_CREATE_LIMITER.reset()
    routes_mod.DISCOVER_TOKEN_LIMITER.reset()
    routes_mod.DISCOVER_IP_LIMITER.reset()
    from web.app import create_app
    import queue
    app = create_app(db_path=tmp_db, notification_queue=queue.Queue())
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c          # deliberately NO admin auth header: these routes are public


DISCOVERY = Discovery(river_name="Ohio River", snap="on_network", candidates=[
    Candidate(kind="usgs", id="03294500", name="OHIO RIVER AT LOUISVILLE",
              distance_km=3.2, tag="downstream", parameter_code="00065"),
    Candidate(kind="noaa", id="MLUK2", name="McAlpine Upper", distance_km=1.1,
              tag="upstream", usgs_id="03293551"),
])

SAVE_BODY = {
    "lat": 38.28, "lon": -85.76, "river_name": "Ohio River", "sensitivity": "floods",
    "sources": [{"kind": "usgs", "id": "03294500", "parameter_code": "00065"},
                {"kind": "noaa", "id": "MLUK2"}],
}


def _valid_site(site_number, parameter_code="00060"):
    return True, "OHIO RIVER AT LOUISVILLE", ""


def _meta(identifier, timeout=10):
    return {"lid": "MLUK2", "station_name": "McAlpine Upper", "usgs_id": "03293551",
            "action_stage": 21.0, "minor_flood_stage": 23.0,
            "moderate_flood_stage": 30.0, "major_flood_stage": 38.0}


def test_get_pin_creates_pending_page_and_redirects(client, tmp_db):
    resp = client.get("/pin")
    assert resp.status_code == 302
    token = resp.headers["Location"].rsplit("/", 1)[-1]
    page = get_page_by_edit_token(token, tmp_db)
    assert page["status"] == "pending" and page["owner_chat_id"] is None


def test_get_pin_is_rate_limited_per_ip(client):
    for _ in range(10):
        assert client.get("/pin").status_code == 302
    assert client.get("/pin").status_code == 429


def test_map_page_renders_without_auth(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    resp = client.get(f"/pin/{page['edit_token']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "leaflet" in body.lower()
    assert "Find gauges" in body
    assert "Floods only" in body


def test_map_page_404_unknown_and_410_stopped(client, tmp_db):
    assert client.get("/pin/nope").status_code == 404
    page = create_pin_page(1, tmp_db)
    from db.models import set_page_status
    set_page_status(page["id"], "stopped", tmp_db)
    assert client.get(f"/pin/{page['edit_token']}").status_code == 410


def test_discover_returns_candidates_as_json(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    with patch("web.routes.discover", return_value=DISCOVERY) as d:
        resp = client.post(f"/pin/{page['edit_token']}/discover",
                           json={"lat": 38.28, "lon": -85.76})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["river_name"] == "Ohio River"
    assert [c["id"] for c in data["candidates"]] == ["03294500", "MLUK2"]
    kwargs = d.call_args.kwargs
    assert kwargs["reach_km"] == 50.0 and kwargs["fallback_radius_miles"] == 25.0


def test_discover_rejects_bad_coordinates(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    resp = client.post(f"/pin/{page['edit_token']}/discover", json={"lat": 95, "lon": 0})
    assert resp.status_code == 400
    resp = client.post(f"/pin/{page['edit_token']}/discover", json={"lat": "x"})
    assert resp.status_code == 400


def test_discover_rate_limited_per_token(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    with patch("web.routes.discover", return_value=DISCOVERY):
        for _ in range(20):
            assert client.post(f"/pin/{page['edit_token']}/discover",
                               json={"lat": 38.28, "lon": -85.76}).status_code == 200
        resp = client.post(f"/pin/{page['edit_token']}/discover",
                           json={"lat": 38.28, "lon": -85.76})
    assert resp.status_code == 429


def test_save_provisions_sources_and_activates_owned_page(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    set_setting("telegram_bot_username", "RiverBot", tmp_db)
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.status_code == 200, resp.data
    data = resp.get_json()
    assert data["ok"] is True and data["status"] == "active" and data["skipped"] == []
    assert data["bot_link"] == f"https://t.me/RiverBot?start={page['edit_token']}"
    assert data["edit_url"].endswith(f"/edit/{page['edit_token']}")
    row = get_page_by_edit_token(page["edit_token"], tmp_db)
    assert row["sensitivity"] == "floods" and row["river_name"] == "Ohio River"
    sites = get_page_sites(page["id"], tmp_db)
    assert [(s["site_number"], s["origin"], s["parameter_code"]) for s in sites] == \
        [("03294500", "user", "00065")]
    assert [g["lid"] for g in get_page_gauges(page["id"], tmp_db)] == ["MLUK2"]


def test_save_queues_confirmation_to_owner(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    q = client.application.config["NOTIFICATION_QUEUE"]
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    item = q.get_nowait()
    assert item["type"] == "direct"
    assert item["data"]["channel"] == "telegram" and item["data"]["channel_id"] == "99"
    assert "Ohio River" in item["data"]["message"] and "/settings" in item["data"]["message"]


def test_save_unowned_page_stays_pending_and_queues_nothing(client, tmp_db):
    page = create_pin_page(None, tmp_db)
    q = client.application.config["NOTIFICATION_QUEUE"]
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.get_json()["status"] == "pending"
    assert q.empty()


def test_save_tolerates_malformed_source_entries(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    body = {**SAVE_BODY, "sources": ["usgs", 123, None, {"kind": "usgs"},
                                     {"kind": "usgs", "id": "03294500", "parameter_code": "00065"}]}
    with patch("web.routes.validate_usgs_site", side_effect=_valid_site),          patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=body)
    assert resp.status_code == 200, resp.data
    assert resp.get_json()["skipped"] == ["invalid"] * 4
    resp = client.post(f"/pin/{page['edit_token']}/save",
                       json={**SAVE_BODY, "sources": ["usgs", 7]})
    assert resp.status_code == 400


def test_save_rejects_zero_sources_and_bad_sensitivity(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    resp = client.post(f"/pin/{page['edit_token']}/save", json={**SAVE_BODY, "sources": []})
    assert resp.status_code == 400 and "source" in resp.get_json()["error"].lower()
    resp = client.post(f"/pin/{page['edit_token']}/save",
                       json={**SAVE_BODY, "sensitivity": "loud"})
    assert resp.status_code == 400


def test_save_skips_invalid_sources_but_fails_if_none_remain(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    with patch("web.routes.validate_usgs_site", return_value=(False, "", "not found")), \
         patch("web.routes.fetch_gauge_metadata", side_effect=_meta):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.status_code == 200
    assert resp.get_json()["skipped"] == ["usgs:03294500"]
    with patch("web.routes.validate_usgs_site", return_value=(False, "", "not found")), \
         patch("web.routes.fetch_gauge_metadata", return_value=None):
        resp = client.post(f"/pin/{page['edit_token']}/save", json=SAVE_BODY)
    assert resp.status_code == 400


def test_pin_routes_do_not_require_admin_auth(client, tmp_db):
    # The fixture sends no Authorization header; a 401 here would mean the
    # endpoint was left out of PUBLIC_ENDPOINTS.
    page = create_pin_page(None, tmp_db)
    assert client.get(f"/pin/{page['edit_token']}").status_code == 200
    assert client.get("/pin").status_code == 302


ADMIN_AUTH = "Basic " + __import__("base64").b64encode(b"admin:testpass").decode()


def test_edit_page_shows_sensitivity_and_move_pin_link(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    resp = client.get(f"/edit/{page['edit_token']}")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'name="sensitivity"' in body
    assert f"/pin/{page['edit_token']}" in body


def test_edit_page_sensitivity_post_updates_dial(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    resp = client.post(f"/edit/{page['edit_token']}/sensitivity",
                       data={"sensitivity": "all"}, follow_redirects=True)
    assert resp.status_code == 200
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["sensitivity"] == "all"


def test_edit_page_sensitivity_rejects_unknown_value(client, tmp_db):
    page = create_pin_page(99, tmp_db)
    client.post(f"/edit/{page['edit_token']}/sensitivity", data={"sensitivity": "loud"})
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["sensitivity"] == "unusual"
    assert client.post("/edit/nope/sensitivity", data={"sensitivity": "all"}).status_code == 404
