import psycopg2
import pytest

from db.models import (get_db, get_setting, get_or_create_noaa_gauge,
                       get_all_noaa_gauges)


def _columns(tmp_db, table):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        "SELECT column_name, column_default FROM information_schema.columns "
        "WHERE table_name=%s", (table,))
    rows = {r["column_name"]: r["column_default"] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return rows


def test_user_pages_gains_pin_columns(tmp_db):
    cols = _columns(tmp_db, "user_pages")
    for name in ("owner_chat_id", "pin_lat", "pin_lon", "river_name",
                 "sensitivity", "status"):
        assert name in cols
    assert "'unusual'" in cols["sensitivity"]
    assert "'active'" in cols["status"]


def test_sources_gain_origin_and_noaa_gains_active(tmp_db):
    assert "'admin'" in _columns(tmp_db, "sites")["origin"]
    noaa = _columns(tmp_db, "noaa_gauges")
    assert "'admin'" in noaa["origin"]
    assert noaa["active"] == "1"


def test_sensitivity_is_constrained(tmp_db):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute(
                "INSERT INTO user_pages (public_token, edit_token, sensitivity) "
                "VALUES ('a', 'b', 'loud')")
    finally:
        cur.close()
        conn.close()


def test_new_settings_seeded(tmp_db):
    assert get_setting("discovery_reach_km", tmp_db) == "50"
    assert get_setting("public_base_url", tmp_db) == ""
    assert get_setting("telegram_bot_username", tmp_db) == ""


def test_get_or_create_noaa_gauge_records_origin(tmp_db):
    get_or_create_noaa_gauge("MLUK2", "McAlpine", 21.0, 23.0, 30.0, 38.0,
                             tmp_db, origin="user")
    gauge = get_all_noaa_gauges(tmp_db)[0]
    assert gauge["origin"] == "user"
    assert gauge["active"] == 1


def test_get_or_create_reactivates_a_retired_gauge(tmp_db):
    get_or_create_noaa_gauge("MLUK2", "McAlpine", None, None, None, None, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE noaa_gauges SET active=0 WHERE lid='MLUK2'")
    conn.commit()
    cur.close()
    conn.close()
    get_or_create_noaa_gauge("MLUK2", "McAlpine", None, None, None, None, tmp_db)
    assert get_all_noaa_gauges(tmp_db)[0]["active"] == 1


def test_get_all_noaa_gauges_active_only_skips_inactive(tmp_db):
    get_or_create_noaa_gauge("AAAA1", "A", None, None, None, None, tmp_db)
    get_or_create_noaa_gauge("BBBB1", "B", None, None, None, None, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE noaa_gauges SET active=0 WHERE lid='BBBB1'")
    conn.commit()
    cur.close()
    conn.close()
    assert {g["lid"] for g in get_all_noaa_gauges(tmp_db)} == {"AAAA1", "BBBB1"}
    assert [g["lid"] for g in get_all_noaa_gauges(tmp_db, active_only=True)] == ["AAAA1"]
