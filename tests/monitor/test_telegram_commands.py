from db.models import (create_pin_page, get_page_by_edit_token, get_page_for_chat,
                       get_page_gauges, get_page_sites, save_pin, set_setting)
from monitor.adapters.telegram_commands import (
    NO_BASE_URL, NO_PAGE, apply_callback, map_url, sensitivity_keyboard,
    set_status_reply, settings_reply, sources_keyboard, start_chat,
)

USGS = [{"site_number": "03294500", "station_name": "Ohio at Louisville",
         "parameter_code": "00065"}]
NOAA = [{"lid": "MLUK2", "station_name": "McAlpine Upper", "action_stage": 21.0,
         "minor_flood_stage": None, "moderate_flood_stage": None,
         "major_flood_stage": None}]


def _base(tmp_db):
    set_setting("public_base_url", "https://river.example.com/", tmp_db)


def test_map_url_uses_base_url_without_double_slash(tmp_db):
    _base(tmp_db)
    assert map_url("tok", tmp_db) == "https://river.example.com/pin/tok"
    set_setting("public_base_url", "", tmp_db)
    assert map_url("tok", tmp_db) is None


def test_start_without_arg_creates_owned_pending_page_and_links_map(tmp_db):
    _base(tmp_db)
    reply = start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    assert page["status"] == "pending"
    assert f"/pin/{page['edit_token']}" in reply


def test_start_twice_returns_the_same_page(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    first = get_page_for_chat(42, tmp_db)
    reply = start_chat(42, "Ann", "", tmp_db)
    assert get_page_for_chat(42, tmp_db)["id"] == first["id"]
    assert first["edit_token"] in reply


def test_start_without_base_url_explains(tmp_db):
    reply = start_chat(42, "Ann", "", tmp_db)
    assert NO_BASE_URL in reply
    assert get_page_for_chat(42, tmp_db) is not None   # page still created


def test_start_with_token_binds_web_first_page(tmp_db):
    _base(tmp_db)
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, [], tmp_db)
    reply = start_chat(42, "Ann", page["edit_token"], tmp_db)
    row = get_page_by_edit_token(page["edit_token"], tmp_db)
    assert row["owner_chat_id"] == 42 and row["status"] == "active"
    assert "Ohio River" in reply


def test_start_with_bad_token_reports_it(tmp_db):
    _base(tmp_db)
    reply = start_chat(42, "Ann", "nope", tmp_db)
    assert "couldn't find" in reply.lower()


def test_settings_reply_links_editor_or_explains(tmp_db):
    _base(tmp_db)
    assert settings_reply(42, tmp_db) == NO_PAGE
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    assert f"/edit/{page['edit_token']}" in settings_reply(42, tmp_db)


def test_sensitivity_keyboard_and_callback(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    text, rows = sensitivity_keyboard(42, tmp_db)
    assert [cb for row in rows for _, cb in row] == ["sens:floods", "sens:unusual", "sens:all"]
    reply = apply_callback(42, "sens:all", tmp_db)
    assert get_page_for_chat(42, tmp_db)["sensitivity"] == "all"
    assert "Everything" in reply


def test_sources_keyboard_lists_and_removes(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, NOAA, tmp_db)
    text, rows = sources_keyboard(42, tmp_db)
    assert "Ohio at Louisville" in text and "McAlpine Upper" in text
    callbacks = [cb for row in rows for _, cb in row]
    site_id = get_page_sites(page["id"], tmp_db)[0]["id"]
    gauge_id = get_page_gauges(page["id"], tmp_db)[0]["id"]
    assert callbacks == [f"rm:usgs:{site_id}", f"rm:noaa:{gauge_id}"]
    apply_callback(42, f"rm:usgs:{site_id}", tmp_db)
    assert get_page_sites(page["id"], tmp_db) == []
    assert len(get_page_gauges(page["id"], tmp_db)) == 1


def test_callback_for_another_chats_page_is_ignored(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, [], tmp_db)
    site_id = get_page_sites(page["id"], tmp_db)[0]["id"]
    reply = apply_callback(7, f"rm:usgs:{site_id}", tmp_db)   # chat 7 owns nothing
    assert reply == NO_PAGE
    assert len(get_page_sites(page["id"], tmp_db)) == 1


def test_pause_resume_stop(tmp_db):
    _base(tmp_db)
    start_chat(42, "Ann", "", tmp_db)
    page = get_page_for_chat(42, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio River", "unusual", USGS, [], tmp_db)
    set_status_reply(42, "paused", tmp_db)
    assert get_page_for_chat(42, tmp_db)["status"] == "paused"
    set_status_reply(42, "active", tmp_db)
    assert get_page_for_chat(42, tmp_db)["status"] == "active"
    set_status_reply(42, "stopped", tmp_db)
    assert get_page_for_chat(42, tmp_db) is None
    assert set_status_reply(42, "paused", tmp_db) == NO_PAGE
