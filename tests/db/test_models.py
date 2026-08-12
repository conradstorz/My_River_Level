import pytest
from db.models import init_db, get_setting, set_setting, get_db

def test_init_db_creates_all_tables(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    tables = {row["table_name"] for row in cur.fetchall()}
    cur.close()
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
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    tables = {row["table_name"] for row in cur.fetchall()}
    cur.close()
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
    cur = conn.cursor()
    cur.execute("UPDATE user_pages SET active=0 WHERE id=%s", (p1["id"],))
    conn.commit()
    cur.close()
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


def test_link_and_get_page_sites(tmp_db):
    from db.models import create_user_page, get_page_by_edit_token, link_page_site, get_page_sites
    _, edit = create_user_page("Test", tmp_db)
    page = get_page_by_edit_token(edit, tmp_db)
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    link_page_site(page["id"], site_id, tmp_db)
    rows = get_page_sites(page["id"], tmp_db)
    assert [r["site_number"] for r in rows] == ["123"]


def test_get_page_subscribers_for_site_only_returns_active(tmp_db):
    from db.models import (create_user_page, get_page_by_edit_token, link_page_site,
                           add_page_subscriber, set_page_subscriber_status,
                           get_page_subscribers_for_site)
    _, edit = create_user_page("Test", tmp_db)
    page = get_page_by_edit_token(edit, tmp_db)
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    link_page_site(page["id"], site_id, tmp_db)
    add_page_subscriber(page["id"], "telegram", "111", "Yes", tmp_db)
    add_page_subscriber(page["id"], "telegram", "222", "No", tmp_db)
    set_page_subscriber_status(page["id"], "telegram", "222", "unsubscribed", tmp_db)
    subs = get_page_subscribers_for_site(site_id, tmp_db)
    assert [s["channel_id"] for s in subs] == ["111"]


def test_site_health_marks_stale_when_never_polled(tmp_db):
    from db.models import get_sites_with_health
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A')")
    conn.commit(); cur.close(); conn.close()
    rows = get_sites_with_health(tmp_db)
    assert rows[0]["stale"] is True


def test_record_site_fetch_success_clears_stale(tmp_db):
    from db.models import record_site_fetch_success, get_sites_with_health
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    record_site_fetch_success(site_id, tmp_db)
    rows = get_sites_with_health(tmp_db)
    assert rows[0]["stale"] is False


def test_record_forecast_points_ignores_duplicates(tmp_db):
    from datetime import datetime, timezone, timedelta
    from db.models import record_forecast_points, get_forecast_points
    issued = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    pts = [{"valid_at": issued + timedelta(hours=24), "stage": 15.0}]
    assert record_forecast_points("abcd1", issued, pts, tmp_db) == 1
    assert record_forecast_points("abcd1", issued, pts, tmp_db) == 0
    assert len(get_forecast_points("abcd1", tmp_db)) == 1


def test_gauge_quality_roundtrip(tmp_db):
    from db.models import get_or_create_noaa_gauge, set_gauge_quality, get_gauge_quality
    get_or_create_noaa_gauge("abcd1", "Test", 10.0, 12.0, 14.0, 16.0, tmp_db)
    assert get_gauge_quality("abcd1", tmp_db) is None
    set_gauge_quality("abcd1", "B", "MAE 0.8 ft at 24 h", tmp_db)
    q = get_gauge_quality("abcd1", tmp_db)
    assert q["grade"] == "B"


def test_unlink_page_site_removes_link(tmp_db):
    from db.models import (create_user_page, get_page_by_edit_token,
                           link_page_site, unlink_page_site, get_page_sites)
    _, edit = create_user_page("Test", tmp_db)
    page = get_page_by_edit_token(edit, tmp_db)
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    link_page_site(page["id"], site_id, tmp_db)
    link_page_site(page["id"], site_id, tmp_db)  # idempotent
    assert len(get_page_sites(page["id"], tmp_db)) == 1
    unlink_page_site(page["id"], site_id, tmp_db)
    assert get_page_sites(page["id"], tmp_db) == []


def test_get_pages_for_site_excludes_inactive_pages(tmp_db):
    from db.models import (create_user_page, get_page_by_edit_token,
                           link_page_site, get_pages_for_site)
    _, edit1 = create_user_page("Page 1", tmp_db)
    _, edit2 = create_user_page("Page 2", tmp_db)
    p1 = get_page_by_edit_token(edit1, tmp_db)
    p2 = get_page_by_edit_token(edit2, tmp_db)
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    link_page_site(p1["id"], site_id, tmp_db)
    link_page_site(p2["id"], site_id, tmp_db)
    assert len(get_pages_for_site(site_id, tmp_db)) == 2
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("UPDATE user_pages SET active=0 WHERE id=%s", (p1["id"],))
    conn.commit(); cur.close(); conn.close()
    pages = get_pages_for_site(site_id, tmp_db)
    assert [p["id"] for p in pages] == [p2["id"]]


def test_record_site_fetch_error_records_message_and_keeps_stale(tmp_db):
    from db.models import record_site_fetch_error, get_sites_with_health
    conn = get_db(tmp_db); cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number, station_name) VALUES ('123', 'A') RETURNING id")
    site_id = cur.fetchone()["id"]; conn.commit(); cur.close(); conn.close()
    record_site_fetch_error(site_id, "no matching parameter column", tmp_db)
    row = get_sites_with_health(tmp_db)[0]
    assert row["last_error"] == "no matching parameter column"
    assert row["last_error_at"] is not None
    assert row["stale"] is True


def test_noaa_observation_roundtrip_ignores_duplicates(tmp_db):
    from datetime import datetime, timezone
    from db.models import record_noaa_observation, get_noaa_observations
    t1 = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 1, 18, tzinfo=timezone.utc)
    record_noaa_observation("abcd1", 15.0, t2, tmp_db)
    record_noaa_observation("abcd1", 14.0, t1, tmp_db)
    record_noaa_observation("abcd1", 99.0, t1, tmp_db)  # duplicate, ignored
    rows = get_noaa_observations("abcd1", tmp_db)
    assert [r["stage"] for r in rows] == [14.0, 15.0]
    assert rows[0]["lid"] == "abcd1"
    assert rows[0]["observed_at"] == t1


def test_record_noaa_observation_defaults_to_now(tmp_db):
    from db.models import record_noaa_observation, get_noaa_observations
    record_noaa_observation("abcd1", 12.5, None, tmp_db)
    rows = get_noaa_observations("abcd1", tmp_db)
    assert len(rows) == 1
    assert rows[0]["observed_at"] is not None


def test_init_db_seeds_new_hardening_settings(tmp_db):
    assert get_setting("facebook_app_secret", tmp_db) == ""
    assert get_setting("rate_change_threshold_ft", tmp_db) == "2.0"
    assert get_setting("rate_change_threshold_pct", tmp_db) == "25"
    assert get_setting("rate_change_window_hours", tmp_db) == "6"
    assert get_setting("rate_change_min_interval_hours", tmp_db) == "6"
    assert get_setting("site_stale_hours", tmp_db) == "6"
    assert get_setting("forecast_poll_hours", tmp_db) == "6"


def test_init_db_is_idempotent_and_creates_new_tables(tmp_db):
    from db.models import init_db
    init_db(tmp_db)
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
    """)
    tables = {row["table_name"] for row in cur.fetchall()}
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'sites'
    """)
    site_cols = {row["column_name"] for row in cur.fetchall()}
    cur.close()
    conn.close()
    assert {"page_sites", "noaa_observations", "gauge_forecasts"}.issubset(tables)
    assert {"last_success_at", "last_error", "last_error_at"}.issubset(site_cols)
