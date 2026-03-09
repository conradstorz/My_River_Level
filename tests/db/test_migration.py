import pytest
import sys
import types
from db.models import init_db, get_setting, get_db
from db.migration import migrate_from_config

def _make_config(sites=None, lat=None, lon=None, param="00060", start_year=1985):
    mod = types.SimpleNamespace()
    mod.MONITORING_SITES = sites or []
    mod.LOCATION = {"latitude": lat, "longitude": lon}
    mod.PARAMETER_CODE = param
    mod.HISTORICAL_START_YEAR = start_year
    mod.LOW_FLOW_PERCENTILE = 10
    mod.HIGH_FLOW_PERCENTILE = 90
    mod.VERY_LOW_PERCENTILE = 5
    mod.VERY_HIGH_PERCENTILE = 95
    mod.SEARCH_RADIUS_MILES = 30
    return mod

def test_migrate_seeds_sites(tmp_db):
    init_db(tmp_db)
    config = _make_config(sites=["03277200", "03292470"])
    migrate_from_config(config, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT site_number FROM sites")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    site_numbers = [r["site_number"] for r in rows]
    assert "03277200" in site_numbers
    assert "03292470" in site_numbers

def test_migrate_seeds_settings(tmp_db):
    init_db(tmp_db)
    config = _make_config(start_year=1985)
    migrate_from_config(config, tmp_db)
    assert get_setting("historical_start_year", tmp_db) == "1985"

def test_migrate_skips_duplicate_sites(tmp_db):
    init_db(tmp_db)
    config = _make_config(sites=["03277200"])
    migrate_from_config(config, tmp_db)
    migrate_from_config(config, tmp_db)  # run twice
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM sites")
    count = cur.fetchone()["cnt"]
    cur.close()
    conn.close()
    assert count == 1
