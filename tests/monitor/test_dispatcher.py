import pytest
import queue
from unittest.mock import MagicMock
from db.models import init_db, get_db
from monitor.dispatcher import NotificationDispatcher, format_transition_message, format_reminder_message

def test_format_transition_message_normal_to_high():
    data = {
        "station_name": "Test Creek",
        "site_number": "12345678",
        "previous_severity": "NORMAL",
        "new_severity": "HIGH",
        "current_value": 1500.0,
        "unit": "cfs",
        "percentile": 91.2,
    }
    msg = format_transition_message(data)
    assert "Test Creek" in msg
    assert "HIGH" in msg
    assert "NORMAL" in msg
    assert "1500" in msg

def test_format_reminder_message():
    data = {
        "station_name": "Test Creek",
        "site_number": "12345678",
        "severity": "SEVERE HIGH",
        "current_value": 9000.0,
        "unit": "cfs",
        "percentile": 97.1,
    }
    msg = format_reminder_message(data)
    assert "Test Creek" in msg
    assert "SEVERE HIGH" in msg

def test_dispatcher_calls_adapter_for_each_subscriber(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (id, site_number) VALUES (1, '12345678')")
    cur.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'chat1', 1)")
    conn.commit()
    cur.close()
    conn.close()

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "transition", "data": {
        "site_id": 1, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": "HIGH",
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
    }})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_called_once()
    args = mock_adapter.send.call_args[0]
    assert args[0] == "chat1"


def test_noaa_transition_dispatched(tmp_db):
    import queue
    from unittest.mock import MagicMock
    from db.models import (init_db, create_user_page, get_page_by_public_token,
                           get_or_create_noaa_gauge, link_page_gauge, add_page_subscriber)
    from monitor.dispatcher import NotificationDispatcher

    init_db(tmp_db)
    pub, _ = create_user_page("Test Page", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    gid = get_or_create_noaa_gauge("MLUK2", "Ohio River", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gid, tmp_db)
    add_page_subscriber(page["id"], "sms", "+15025551234", "Alice", tmp_db)

    mock_adapter = MagicMock()
    mock_adapter.channel = "sms"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({
        "type": "noaa_transition",
        "data": {
            "gauge_id": gid,
            "lid": "MLUK2",
            "station_name": "Ohio River at McAlpine Upper",
            "previous_severity": "Normal",
            "new_severity": "Action",
            "current_stage": 21.5,
        }
    })

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_called_once()
    args = mock_adapter.send.call_args[0]
    assert args[0] == "+15025551234"
    assert "MLUK2" in args[1] or "McAlpine" in args[1]
    assert "Action" in args[1]
