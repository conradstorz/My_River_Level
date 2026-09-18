import pytest

from db.models import (
    bind_page_to_chat, create_pin_page, get_active_page_subscribers, get_db,
    get_page_by_edit_token, get_page_for_chat, get_page_gauges, get_page_sites,
    save_pin, set_page_sensitivity, set_page_status,
)

USGS = [{"site_number": "03294500", "station_name": "OHIO RIVER AT LOUISVILLE",
         "parameter_code": "00065"}]
NOAA = [{"lid": "MLUK2", "station_name": "McAlpine Upper", "action_stage": 21.0,
         "minor_flood_stage": 23.0, "moderate_flood_stage": 30.0,
         "major_flood_stage": 38.0}]


def _row(tmp_db, table, **where):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    clause = " AND ".join(f"{k}=%s" for k in where)
    cur.execute(f"SELECT * FROM {table} WHERE {clause}", tuple(where.values()))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def test_create_pin_page_is_pending_and_owned(tmp_db):
    page = create_pin_page(123, tmp_db)
    assert page["status"] == "pending"
    assert page["owner_chat_id"] == 123
    assert page["sensitivity"] == "unusual"
    assert page["edit_token"] and page["public_token"]


def test_create_pin_page_without_owner(tmp_db):
    page = create_pin_page(None, tmp_db)
    assert page["owner_chat_id"] is None


def test_get_page_for_chat_ignores_stopped_pages(tmp_db):
    old = create_pin_page(5, tmp_db)
    set_page_status(old["id"], "stopped", tmp_db)
    assert get_page_for_chat(5, tmp_db) is None
    new = create_pin_page(5, tmp_db)
    assert get_page_for_chat(5, tmp_db)["id"] == new["id"]


def test_bind_page_to_chat_sets_owner_and_subscribes(tmp_db):
    page = create_pin_page(None, tmp_db)
    bound = bind_page_to_chat(page["edit_token"], 42, "Ann", tmp_db)
    assert bound["owner_chat_id"] == 42
    assert bound["status"] == "pending"          # no pin yet
    subs = get_active_page_subscribers(page["id"], tmp_db)
    assert [(s["channel"], s["channel_id"]) for s in subs] == [("telegram", "42")]


def test_bind_activates_a_page_that_already_has_a_pin(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "pending"
    bound = bind_page_to_chat(page["edit_token"], 42, "Ann", tmp_db)
    assert bound["status"] == "active"


def test_bind_refuses_unknown_token_and_foreign_page(tmp_db):
    assert bind_page_to_chat("nope", 42, "Ann", tmp_db) is None
    page = create_pin_page(1, tmp_db)
    assert bind_page_to_chat(page["edit_token"], 2, "Bob", tmp_db) is None
    assert bind_page_to_chat(page["edit_token"], 1, "Ann", tmp_db)["owner_chat_id"] == 1


def test_save_pin_provisions_sources_with_user_origin(tmp_db):
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "floods", USGS, NOAA, tmp_db)
    row = get_page_by_edit_token(page["edit_token"], tmp_db)
    assert (row["pin_lat"], row["pin_lon"]) == (38.25, -85.75)
    assert row["river_name"] == "Ohio River"
    assert row["sensitivity"] == "floods"
    assert row["status"] == "active"
    site = _row(tmp_db, "sites", site_number="03294500")
    assert site["origin"] == "user" and site["active"] == 1
    assert site["parameter_code"] == "00065"
    gauge = _row(tmp_db, "noaa_gauges", lid="MLUK2")
    assert gauge["origin"] == "user" and gauge["active"] == 1
    assert [s["id"] for s in get_page_sites(page["id"], tmp_db)] == [site["id"]]
    assert [g["id"] for g in get_page_gauges(page["id"], tmp_db)] == [gauge["id"]]


def test_save_pin_replaces_previous_links_and_reactivates(tmp_db):
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, NOAA, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE sites SET active=0")
    conn.commit()
    cur.close()
    conn.close()
    other = [{"site_number": "03293551", "station_name": "OHIO R US OF MCALPINE",
              "parameter_code": "00060"}]
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS + other, [], tmp_db)
    numbers = sorted(s["site_number"] for s in get_page_sites(page["id"], tmp_db))
    assert numbers == ["03293551", "03294500"]
    assert get_page_gauges(page["id"], tmp_db) == []
    assert _row(tmp_db, "sites", site_number="03294500")["active"] == 1


def test_save_pin_keeps_admin_origin_on_existing_site(tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('03294500', 'Admin')")
    conn.commit()
    cur.close()
    conn.close()
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert _row(tmp_db, "sites", site_number="03294500")["origin"] == "admin"


def test_save_pin_stays_pending_without_owner(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "pending"


def test_set_sensitivity_and_status_validate(tmp_db):
    page = create_pin_page(7, tmp_db)
    set_page_sensitivity(page["id"], "all", tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["sensitivity"] == "all"
    with pytest.raises(ValueError):
        set_page_sensitivity(page["id"], "loud", tmp_db)
    set_page_status(page["id"], "paused", tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "paused"
    with pytest.raises(ValueError):
        set_page_status(page["id"], "asleep", tmp_db)
