import pytest

from db.models import (
    ChatHasLivePage, PageNotBindable, bind_page_to_chat, create_pin_page, create_user_page,
    get_active_page_subscribers, get_db, get_page_by_edit_token,
    get_page_for_chat, get_page_gauges, get_page_sites,
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


def test_rebind_does_not_unpause_a_paused_page(tmp_db):
    page = create_pin_page(42, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    set_page_status(page["id"], "paused", tmp_db)
    bound = bind_page_to_chat(page["edit_token"], 42, "Ann", tmp_db)
    assert bound["status"] == "paused"


def test_binding_a_new_page_replaces_a_pinless_pending_page(tmp_db):
    first = create_pin_page(42, tmp_db)              # bare /start, never pinned
    second = create_pin_page(None, tmp_db)          # web-first, unbound
    bound = bind_page_to_chat(second["edit_token"], 42, "Ann", tmp_db)
    assert bound["owner_chat_id"] == 42
    assert get_page_by_edit_token(first["edit_token"], tmp_db)["status"] == "stopped"
    assert get_page_for_chat(42, tmp_db)["id"] == second["id"]


@pytest.mark.parametrize("status", ["active", "paused"])
def test_binding_refuses_to_replace_a_live_page(tmp_db, status):
    first = create_pin_page(42, tmp_db)
    save_pin(first["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    set_page_status(first["id"], status, tmp_db)
    second = create_pin_page(None, tmp_db)
    with pytest.raises(ChatHasLivePage) as excinfo:
        bind_page_to_chat(second["edit_token"], 42, "Ann", tmp_db)
    assert excinfo.value.page["id"] == first["id"]
    assert get_page_by_edit_token(first["edit_token"], tmp_db)["status"] == status
    unbound = get_page_by_edit_token(second["edit_token"], tmp_db)
    assert unbound["owner_chat_id"] is None
    assert get_active_page_subscribers(second["id"], tmp_db) == []


def test_binding_after_stop_replaces_normally(tmp_db):
    first = create_pin_page(42, tmp_db)
    save_pin(first["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    set_page_status(first["id"], "active", tmp_db)
    set_page_status(first["id"], "stopped", tmp_db)
    second = create_pin_page(None, tmp_db)
    bound = bind_page_to_chat(second["edit_token"], 42, "Ann", tmp_db)
    assert bound["owner_chat_id"] == 42


def test_bind_refuses_unknown_token_and_foreign_page(tmp_db):
    assert bind_page_to_chat("nope", 42, "Ann", tmp_db) is None
    page = create_pin_page(1, tmp_db)
    assert bind_page_to_chat(page["edit_token"], 2, "Bob", tmp_db) is None
    assert bind_page_to_chat(page["edit_token"], 1, "Ann", tmp_db)["owner_chat_id"] == 1


@pytest.mark.parametrize("status", ["active", "paused"])
def test_bind_refuses_an_admin_created_page_without_side_effects(tmp_db, status):
    """An admin page's edit token must never turn it into a Pin page."""
    pinless = create_pin_page(42, tmp_db)            # would be stopped by a bind
    _pub, edit = create_user_page("Curated River", tmp_db)
    admin_page = get_page_by_edit_token(edit, tmp_db)
    set_page_status(admin_page["id"], status, tmp_db)
    with pytest.raises(PageNotBindable):
        bind_page_to_chat(edit, 42, "Ann", tmp_db)
    after = get_page_by_edit_token(edit, tmp_db)
    assert after["owner_chat_id"] is None and after["status"] == status
    assert get_active_page_subscribers(admin_page["id"], tmp_db) == []
    assert get_page_by_edit_token(pinless["edit_token"], tmp_db)["status"] == "pending"


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


def test_save_pin_never_reenables_admin_sources_switched_off(tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name, active) "
                "VALUES ('03294500', 'Admin', 0)")
    cur.execute("INSERT INTO noaa_gauges (lid, station_name, active) "
                "VALUES ('MLUK2', 'Admin', 0)")
    conn.commit()
    cur.close()
    conn.close()
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, NOAA, tmp_db)
    site = _row(tmp_db, "sites", site_number="03294500")
    gauge = _row(tmp_db, "noaa_gauges", lid="MLUK2")
    assert (site["origin"], site["active"]) == ("admin", 0)
    assert (gauge["origin"], gauge["active"]) == ("admin", 0)
    assert len(get_page_sites(page["id"], tmp_db)) == 1      # still linked


def test_save_pin_enforces_the_source_cap_in_its_transaction(tmp_db):
    from db.models import MAX_SOURCES_PER_PAGE
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    too_many = [{"site_number": f"9{i:07d}", "station_name": "S", "parameter_code": "00065"}
                for i in range(MAX_SOURCES_PER_PAGE + 1)]
    with pytest.raises(ValueError):
        save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", too_many, [], tmp_db)
    assert [s["site_number"] for s in get_page_sites(page["id"], tmp_db)] == ["03294500"]


def test_save_pin_stays_pending_without_owner(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "pending"


def test_save_pin_keeps_admin_page_active(tmp_db):
    public_token, edit_token = create_user_page("Admin River", tmp_db)
    page = get_page_by_edit_token(edit_token, tmp_db)
    assert page["status"] == "active"
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(edit_token, tmp_db)["status"] == "active"


def test_save_pin_does_not_unpause_a_paused_page(tmp_db):
    page = create_pin_page(7, tmp_db)
    save_pin(page["id"], 38.25, -85.75, "Ohio River", "unusual", USGS, [], tmp_db)
    set_page_status(page["id"], "paused", tmp_db)
    save_pin(page["id"], 38.30, -85.80, "Ohio River", "unusual", USGS, [], tmp_db)
    assert get_page_by_edit_token(page["edit_token"], tmp_db)["status"] == "paused"


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
