import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from db.models import init_db, get_db
from monitor.scheduler import is_reminder_due, get_reminder_interval_hours

def test_get_reminder_interval_hours_low(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("LOW", tmp_db) == 24.0

def test_get_reminder_interval_hours_high(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("HIGH", tmp_db) == 24.0

def test_get_reminder_interval_hours_severe_low(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("SEVERE LOW", tmp_db) == 4.0

def test_get_reminder_interval_hours_severe_high(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("SEVERE HIGH", tmp_db) == 4.0

def test_get_reminder_interval_returns_none_for_normal(tmp_db):
    init_db(tmp_db)
    assert get_reminder_interval_hours("NORMAL", tmp_db) is None

def test_is_reminder_due_when_no_previous_notification(tmp_db):
    init_db(tmp_db)
    # No prior notification — reminder is always due
    assert is_reminder_due(site_id=1, severity="HIGH", db_path=tmp_db) is True

def test_is_reminder_due_when_recent_notification(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number) VALUES ('00000001')")
    cur.execute("INSERT INTO subscribers (channel, channel_id) VALUES ('telegram', 'abc')")
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    cur.execute(
        """INSERT INTO notifications
           (subscriber_id, site_id, sent_at, channel, message_text, trigger_type)
           VALUES (1, 1, %s, 'telegram', 'test', 'reminder')""",
        (one_hour_ago,)
    )
    conn.commit()
    cur.close()
    conn.close()
    # 1 hour ago — not due yet (interval is 4h for SEVERE)
    assert is_reminder_due(site_id=1, severity="SEVERE HIGH", db_path=tmp_db) is False

def test_is_reminder_due_when_old_notification(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number) VALUES ('00000001')")
    cur.execute("INSERT INTO subscribers (channel, channel_id) VALUES ('telegram', 'abc')")
    twenty_five_hours_ago = (datetime.utcnow() - timedelta(hours=25)).isoformat()
    cur.execute(
        """INSERT INTO notifications
           (subscriber_id, site_id, sent_at, channel, message_text, trigger_type)
           VALUES (1, 1, %s, 'telegram', 'test', 'reminder')""",
        (twenty_five_hours_ago,)
    )
    conn.commit()
    cur.close()
    conn.close()
    # 25 hours ago — due (interval is 24h for HIGH)
    assert is_reminder_due(site_id=1, severity="HIGH", db_path=tmp_db) is True
