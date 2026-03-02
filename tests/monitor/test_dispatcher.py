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
    conn.execute("INSERT INTO sites (id, site_number) VALUES (1, '12345678')")
    conn.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'chat1', 1)")
    conn.commit()
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
