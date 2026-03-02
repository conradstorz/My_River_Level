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
                "notifications", "pending_registrations"}
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
