import pytest
from db.models import init_db, get_db
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_dashboard_shows_no_sites_message_when_empty(client):
    response = client.get("/")
    assert b"No sites" in response.data or b"no sites" in response.data.lower()

def test_dashboard_shows_site_condition(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number, station_name, active) VALUES (1, '03277200', 'Test Creek', 1)")
    conn.execute("INSERT INTO site_conditions (site_id, current_value, unit, percentile, severity) VALUES (1, 500.0, 'cfs', 45.0, 'NORMAL')")
    conn.commit()
    conn.close()
    response = client.get("/")
    assert b"Test Creek" in response.data
    assert b"NORMAL" in response.data

def test_dashboard_shows_recent_notifications(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO sites (id, site_number, station_name) VALUES (1, '03277200', 'Test Creek')")
    conn.execute("INSERT INTO subscribers (id, channel, channel_id) VALUES (1, 'telegram', 'abc')")
    conn.execute("""INSERT INTO notifications (subscriber_id, site_id, channel, message_text, trigger_type)
                    VALUES (1, 1, 'telegram', 'Test alert message', 'transition')""")
    conn.commit()
    conn.close()
    response = client.get("/")
    assert b"Test alert message" in response.data


def test_dashboard_shows_version(client):
    response = client.get("/")
    assert b"1.0.0" in response.data


def test_dashboard_shows_release_date(client):
    response = client.get("/")
    assert b"2026-03-08" in response.data
