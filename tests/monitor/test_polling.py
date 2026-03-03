import pytest
from unittest.mock import patch, MagicMock
from db.models import init_db, get_db
from monitor.polling import detect_transition, record_condition, get_active_sites

def test_detect_transition_returns_none_when_same_severity():
    assert detect_transition("HIGH", "HIGH") is None

def test_detect_transition_returns_tuple_when_different():
    result = detect_transition("NORMAL", "HIGH")
    assert result == ("NORMAL", "HIGH")

def test_detect_transition_normal_to_severe():
    result = detect_transition("NORMAL", "SEVERE HIGH")
    assert result == ("NORMAL", "SEVERE HIGH")

def test_record_condition_inserts_row(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number) VALUES ('12345678')")
    conn.commit()
    site_id = conn.execute("SELECT id FROM sites WHERE site_number='12345678'").fetchone()["id"]
    conn.close()

    record_condition(site_id, 500.0, "cfs", 45.2, "NORMAL", tmp_db)

    conn = get_db(tmp_db)
    row = conn.execute("SELECT * FROM site_conditions WHERE site_id=?", (site_id,)).fetchone()
    conn.close()
    assert row["current_value"] == 500.0
    assert row["severity"] == "NORMAL"
    assert row["percentile"] == pytest.approx(45.2)

def test_get_active_sites_returns_only_active(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (site_number, active) VALUES ('11111111', 1)")
    conn.execute("INSERT INTO sites (site_number, active) VALUES ('22222222', 0)")
    conn.commit()
    conn.close()

    sites = get_active_sites(tmp_db)
    site_numbers = [s["site_number"] for s in sites]
    assert "11111111" in site_numbers
    assert "22222222" not in site_numbers
