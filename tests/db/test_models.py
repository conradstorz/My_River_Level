import pytest
import sqlite3
from db.models import init_db, get_setting, set_setting, get_db

def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    conn = sqlite3.connect(tmp_db)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()
    expected = {"sites", "settings", "site_conditions", "subscribers",
                "notifications", "pending_registrations",
                "user_pages", "noaa_gauges", "page_noaa_gauges", "page_subscribers"}
    assert expected.issubset(tables)

def test_set_and_get_setting(tmp_db):
    init_db(tmp_db)
    set_setting("poll_interval_minutes", "15", tmp_db)
    assert get_setting("poll_interval_minutes", tmp_db) == "15"

def test_get_setting_returns_default_when_missing(tmp_db):
    init_db(tmp_db)
    assert get_setting("nonexistent_key", tmp_db, default="42") == "42"

def test_init_db_seeds_default_settings(tmp_db):
    init_db(tmp_db)
    assert get_setting("poll_interval_minutes", tmp_db) == "15"
    assert get_setting("low_percentile", tmp_db) == "10"
    assert get_setting("high_percentile", tmp_db) == "90"
    assert get_setting("very_low_percentile", tmp_db) == "5"
    assert get_setting("very_high_percentile", tmp_db) == "95"
    assert get_setting("reminder_low_high_hours", tmp_db) == "24"
    assert get_setting("reminder_severe_hours", tmp_db) == "4"
    assert get_setting("historical_start_year", tmp_db) == "1980"

def test_new_tables_created(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "user_pages" in tables
    assert "noaa_gauges" in tables
    assert "page_noaa_gauges" in tables
    assert "page_subscribers" in tables


def test_create_user_page(tmp_db):
    from db.models import create_user_page, get_page_by_public_token, get_page_by_edit_token
    init_db(tmp_db)
    pub, edit = create_user_page("My Page", tmp_db)
    assert len(pub) == 36   # UUID format
    assert len(edit) == 36
    assert pub != edit
    page = get_page_by_public_token(pub, tmp_db)
    assert page["page_name"] == "My Page"
    assert page["active"] == 1
    page2 = get_page_by_edit_token(edit, tmp_db)
    assert page2["id"] == page["id"]


def test_noaa_gauge_helpers(tmp_db):
    from db.models import get_or_create_noaa_gauge, get_all_noaa_gauges, update_noaa_gauge_condition
    init_db(tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    assert isinstance(gid, int)
    # Calling again returns same id
    gid2 = get_or_create_noaa_gauge("MLUK2", "Ohio River at McAlpine Upper", 21.0, 23.0, 30.0, 38.0, tmp_db)
    assert gid == gid2
    update_noaa_gauge_condition("MLUK2", 17.5, "Normal", tmp_db)
    gauges = get_all_noaa_gauges(tmp_db)
    assert len(gauges) == 1
    assert gauges[0]["severity"] == "Normal"


def test_page_gauge_link(tmp_db):
    from db.models import create_user_page, get_page_by_public_token, get_or_create_noaa_gauge, link_page_gauge, unlink_page_gauge, get_page_gauges
    init_db(tmp_db)
    pub, edit = create_user_page("Test", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gid, tmp_db)
    gauges = get_page_gauges(page["id"], tmp_db)
    assert len(gauges) == 1
    assert gauges[0]["lid"] == "MLUK2"
    unlink_page_gauge(page["id"], gid, tmp_db)
    assert get_page_gauges(page["id"], tmp_db) == []


def test_page_subscriber_helpers(tmp_db):
    from db.models import create_user_page, get_page_by_public_token, add_page_subscriber, set_page_subscriber_status, get_active_page_subscribers
    init_db(tmp_db)
    pub, _ = create_user_page("Test", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", tmp_db)
    subs = get_active_page_subscribers(page["id"], tmp_db)
    assert len(subs) == 1
    assert subs[0]["channel_id"] == "+15025551234"
    set_page_subscriber_status(page["id"], "sms", "+15025551234", "paused", tmp_db)
    assert get_active_page_subscribers(page["id"], tmp_db) == []


def test_get_pages_for_noaa_gauge(tmp_db):
    from db.models import create_user_page, get_page_by_public_token, get_or_create_noaa_gauge, link_page_gauge, get_pages_for_noaa_gauge
    init_db(tmp_db)
    pub1, _ = create_user_page("Page 1", tmp_db)
    pub2, _ = create_user_page("Page 2", tmp_db)
    p1 = get_page_by_public_token(pub1, tmp_db)
    p2 = get_page_by_public_token(pub2, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(p1["id"], gid, tmp_db)
    link_page_gauge(p2["id"], gid, tmp_db)
    pages = get_pages_for_noaa_gauge(gid, tmp_db)
    assert len(pages) == 2
    # Disable one page and verify it's excluded
    from db.models import get_db
    conn = get_db(tmp_db)
    conn.execute("UPDATE user_pages SET active=0 WHERE id=?", (p1["id"],))
    conn.commit()
    conn.close()
    active_pages = get_pages_for_noaa_gauge(gid, tmp_db)
    assert len(active_pages) == 1
    assert active_pages[0]["id"] == p2["id"]


def test_get_page_subscribers_for_gauge(tmp_db):
    from db.models import (create_user_page, get_page_by_public_token,
                           get_or_create_noaa_gauge, link_page_gauge,
                           add_page_subscriber, get_page_subscribers_for_gauge,
                           set_page_subscriber_status)
    init_db(tmp_db)
    pub, _ = create_user_page("Test", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gid, tmp_db)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", tmp_db)
    add_page_subscriber(page["id"], "telegram", "12345", "Bob", tmp_db)
    subs = get_page_subscribers_for_gauge(gid, tmp_db)
    assert len(subs) == 2
    channels = {s["channel"] for s in subs}
    assert channels == {"sms", "telegram"}
    # Pause one subscriber and verify they are excluded
    set_page_subscriber_status(page["id"], "sms", "+15025551234", "paused", tmp_db)
    active_subs = get_page_subscribers_for_gauge(gid, tmp_db)
    assert len(active_subs) == 1
    assert active_subs[0]["channel"] == "telegram"
