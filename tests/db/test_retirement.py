from db.models import create_pin_page, get_db, save_pin, set_page_status
from monitor.retirement import sweep

USGS = [{"site_number": "03294500", "station_name": "Ohio", "parameter_code": "00065"}]
NOAA = [{"lid": "MLUK2", "station_name": "McAlpine", "action_stage": 21.0,
         "minor_flood_stage": None, "moderate_flood_stage": None,
         "major_flood_stage": None}]


def _active(tmp_db, table, key, value):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(f"SELECT active FROM {table} WHERE {key}=%s", (value,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["active"] if row else None


def _exec(tmp_db, sql, params=()):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    cur.close()
    conn.close()


def _backdate_site(tmp_db, site_number, hours=1):
    """Age a site's `added_at` so it clears the sweep's 10-minute grace window."""
    _exec(tmp_db,
          "UPDATE sites SET added_at=(NOW() - (%s * INTERVAL '1 hour'))::TEXT "
          "WHERE site_number=%s",
          (hours, site_number))


def test_user_sources_with_no_live_page_are_deactivated(tmp_db):
    page = create_pin_page(1, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, NOAA, tmp_db)
    set_page_status(page["id"], "stopped", tmp_db)
    _backdate_site(tmp_db, "03294500")
    result = sweep(tmp_db)
    assert result["sites"] == 1 and result["gauges"] == 1
    assert _active(tmp_db, "sites", "site_number", "03294500") == 0
    assert _active(tmp_db, "noaa_gauges", "lid", "MLUK2") == 0


def test_recently_provisioned_site_is_not_retired_even_if_unreferenced(tmp_db):
    page = create_pin_page(1, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    set_page_status(page["id"], "stopped", tmp_db)
    _backdate_site(tmp_db, "03294500", hours=1 / 60)   # added ~1 minute ago
    result = sweep(tmp_db)
    assert result["sites"] == 0
    assert _active(tmp_db, "sites", "site_number", "03294500") == 1


def test_paused_page_keeps_its_sources(tmp_db):
    page = create_pin_page(1, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, NOAA, tmp_db)
    set_page_status(page["id"], "paused", tmp_db)
    sweep(tmp_db)
    assert _active(tmp_db, "sites", "site_number", "03294500") == 1
    assert _active(tmp_db, "noaa_gauges", "lid", "MLUK2") == 1


def test_admin_sources_are_never_touched(tmp_db):
    _exec(tmp_db, "INSERT INTO sites (site_number, station_name) VALUES ('11111111', 'Admin')")
    _exec(tmp_db, "INSERT INTO noaa_gauges (lid, station_name) VALUES ('ADMN1', 'Admin')")
    result = sweep(tmp_db)
    assert result == {"sites": 0, "gauges": 0, "pending_pages": 0}
    assert _active(tmp_db, "sites", "site_number", "11111111") == 1
    assert _active(tmp_db, "noaa_gauges", "lid", "ADMN1") == 1


def test_source_shared_with_a_live_page_survives(tmp_db):
    a = create_pin_page(1, tmp_db)
    b = create_pin_page(2, tmp_db)
    save_pin(a["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    save_pin(b["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    set_page_status(a["id"], "stopped", tmp_db)
    sweep(tmp_db)
    assert _active(tmp_db, "sites", "site_number", "03294500") == 1


def test_stale_pending_page_is_deleted_but_fresh_one_kept(tmp_db):
    stale = create_pin_page(1, tmp_db)
    fresh = create_pin_page(2, tmp_db)
    _exec(tmp_db,
          "UPDATE user_pages SET created_at=(NOW() - INTERVAL '25 hours')::TEXT WHERE id=%s",
          (stale["id"],))
    result = sweep(tmp_db)
    assert result["pending_pages"] == 1
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT id FROM user_pages ORDER BY id")
    ids = [r["id"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    assert ids == [fresh["id"]]


def test_stale_pending_page_with_a_pin_is_kept(tmp_db):
    page = create_pin_page(None, tmp_db)          # web-first, never bound
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    _exec(tmp_db,
          "UPDATE user_pages SET created_at=(NOW() - INTERVAL '25 hours')::TEXT WHERE id=%s",
          (page["id"],))
    assert sweep(tmp_db)["pending_pages"] == 0


def test_pinned_pending_page_keeps_its_sources(tmp_db):
    page = create_pin_page(None, tmp_db)          # web-first, not yet bound
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, NOAA, tmp_db)
    result = sweep(tmp_db)
    assert result == {"sites": 0, "gauges": 0, "pending_pages": 0}
    assert _active(tmp_db, "sites", "site_number", "03294500") == 1
    assert _active(tmp_db, "noaa_gauges", "lid", "MLUK2") == 1


def test_pinned_pending_page_is_deleted_after_a_week(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    _exec(tmp_db,
          "UPDATE user_pages SET created_at=(NOW() - INTERVAL '8 days')::TEXT WHERE id=%s",
          (page["id"],))
    _backdate_site(tmp_db, "03294500")
    first = sweep(tmp_db)
    assert first["pending_pages"] == 1
    second = sweep(tmp_db)                       # page gone → source now unreferenced
    assert second["sites"] == 1
    assert _active(tmp_db, "sites", "site_number", "03294500") == 0


def test_pinned_pending_page_younger_than_a_week_is_kept(tmp_db):
    page = create_pin_page(None, tmp_db)
    save_pin(page["id"], 38.0, -85.0, "Ohio", "unusual", USGS, [], tmp_db)
    _exec(tmp_db,
          "UPDATE user_pages SET created_at=(NOW() - INTERVAL '6 days')::TEXT WHERE id=%s",
          (page["id"],))
    assert sweep(tmp_db)["pending_pages"] == 0
