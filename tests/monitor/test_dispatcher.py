import pytest
import queue
from unittest.mock import MagicMock
from db.models import init_db, get_db
from monitor.dispatcher import (NotificationDispatcher, format_transition_message,
                                format_reminder_message, format_trend_message)


def _page_with_site(tmp_db, page_name="Test Page", site_number="12345678",
                    channel="telegram", channel_id="chat1"):
    """Create a page linked to a new site with one active subscriber.

    Returns (page_id, site_id).
    """
    from db.models import (create_user_page, get_page_by_public_token,
                           link_page_site, add_page_subscriber)
    pub, _ = create_user_page(page_name, tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sites (site_number, station_name) VALUES (%s, 'Test') RETURNING id",
        (site_number,),
    )
    site_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    link_page_site(page["id"], site_id, tmp_db)
    add_page_subscriber(page["id"], channel, channel_id, "Sub", tmp_db)
    return page["id"], site_id


def test_format_transition_message_normal_to_high():
    data = {
        "station_name": "Test Creek",
        "site_number": "12345678",
        "previous_severity": "NORMAL",
        "new_severity": "HIGH",
        "current_value": 1500.0,
        "unit": "cfs",
        "percentile": 91.2,
        "direction": "RISING",
    }
    msg = format_transition_message(data)
    assert "Test Creek" in msg
    assert "HIGH" in msg
    assert "NORMAL" in msg
    assert "1500" in msg
    # The first line says which way the river is moving.
    assert msg.splitlines()[0].startswith("📈 RISING")


def test_format_transition_message_direction_symbols():
    base = {
        "station_name": "Test Creek", "site_number": "12345678",
        "previous_severity": "HIGH", "new_severity": "NORMAL",
        "current_value": 500.0, "unit": "cfs", "percentile": 40.0,
    }
    falling = format_transition_message({**base, "direction": "FALLING"})
    steady = format_transition_message({**base, "direction": "STEADY"})
    assert falling.splitlines()[0].startswith("📉 FALLING")
    assert steady.splitlines()[0].startswith("➡️ STEADY")


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


def test_format_trend_message_states_direction_and_delta():
    data = {
        "station_name": "Ohio River at Louisville",
        "site_number": "03294500",
        "direction": "RISING",
        "delta": 4.25,
        "start_value": 20.0,
        "end_value": 24.25,
        "hours": 5.5,
        "unit": "ft",
    }
    msg = format_trend_message(data)
    assert "📈" in msg
    assert "Rising" in msg
    assert "risen 4.25 ft" in msg
    assert "5.5 hours" in msg
    assert "24.25 ft" in msg
    assert "20.00" in msg


def test_format_trend_message_for_a_falling_river():
    data = {
        "station_name": "Ohio River at Louisville",
        "site_number": "03294500",
        "direction": "FALLING",
        "delta": -3.0,
        "start_value": 24.0,
        "end_value": 21.0,
        "hours": 6.0,
        "unit": "ft",
    }
    msg = format_trend_message(data)
    assert "📉" in msg
    assert "fallen 3.00 ft" in msg


def test_dispatcher_sends_transition_to_page_subscribers_of_that_site(tmp_db):
    init_db(tmp_db)
    _, site_id = _page_with_site(tmp_db)

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "transition", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": "HIGH",
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
        "direction": "RISING",
    }})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_called_once()
    args = mock_adapter.send.call_args[0]
    assert args[0] == "chat1"


def test_dispatcher_does_not_send_transition_to_unrelated_page_subscribers(tmp_db):
    """A user watching one river must not get alerts about another."""
    init_db(tmp_db)
    _, site_a = _page_with_site(tmp_db, "Page A", "11111111", channel_id="watcher-a")
    _page_with_site(tmp_db, "Page B", "22222222", channel_id="watcher-b")

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "transition", "data": {
        "site_id": site_a, "site_number": "11111111", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": "HIGH",
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
        "direction": "RISING",
    }})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    recipients = [c[0][0] for c in mock_adapter.send.call_args_list]
    assert recipients == ["watcher-a"]


def test_dispatcher_does_not_send_transition_to_global_subscribers(tmp_db):
    """The `subscribers` table is broadcast-only now, not a USGS alert list."""
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO sites (site_number) VALUES ('12345678') RETURNING id")
    site_id = cur.fetchone()["id"]
    cur.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'global1', 1)")
    conn.commit()
    cur.close()
    conn.close()

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "transition", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": "HIGH",
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
        "direction": "RISING",
    }})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_not_called()


def test_dispatcher_sends_reminder_to_page_subscribers_of_that_site(tmp_db):
    init_db(tmp_db)
    _, site_id = _page_with_site(tmp_db, channel_id="reminder-sub")

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "reminder", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "severity": "HIGH", "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
    }})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    assert mock_adapter.send.call_args[0][0] == "reminder-sub"


def test_dispatcher_sends_trend_alert_and_logs_it(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db, channel_id="trend-sub")
    # Trend alerts require the 'all' sensitivity dial; set it explicitly so
    # the test does not depend on the column default.
    _set_page(tmp_db, page_id, sensitivity="all")

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "trend", "data": {
        "site_id": site_id, "site_number": "12345678",
        "station_name": "Test River", "direction": "RISING",
        "delta": 4.0, "start_value": 6.0, "end_value": 10.0,
        "hours": 4.0, "unit": "ft",
    }})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_called_once()
    assert mock_adapter.send.call_args[0][0] == "trend-sub"
    assert "risen" in mock_adapter.send.call_args[0][1]

    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT * FROM notifications WHERE trigger_type='trend'")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["site_id"] == site_id
    assert rows[0]["subscriber_id"] is None


def test_broadcast_still_goes_to_global_subscribers(tmp_db):
    init_db(tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'global1', 1)")
    conn.commit()
    cur.close()
    conn.close()

    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True

    q = queue.Queue()
    q.put({"type": "broadcast", "data": {"message": "hello"}})

    dispatcher = NotificationDispatcher(q, adapters=[mock_adapter], db_path=tmp_db)
    dispatcher.run_once()

    mock_adapter.send.assert_called_once_with("global1", "hello")


def test_dispatcher_run_survives_a_failing_run_once(tmp_db):
    """A crash inside run_once must not kill the dispatcher thread."""
    import threading
    import time

    stop_event = threading.Event()
    dispatcher = NotificationDispatcher(queue.Queue(), db_path=tmp_db, stop_event=stop_event)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        time.sleep(0.01)

    dispatcher.run_once = flaky
    dispatcher.start()
    deadline = time.time() + 5
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert dispatcher.is_alive()
    assert len(calls) >= 2
    stop_event.set()
    dispatcher.join(timeout=5)
    assert not dispatcher.is_alive()


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


def _set_page(tmp_db, page_id, **fields):
    conn = get_db(tmp_db)
    cur = conn.cursor()
    for key, value in fields.items():
        cur.execute(f"UPDATE user_pages SET {key}=%s WHERE id=%s", (value, page_id))
    conn.commit()
    cur.close()
    conn.close()


def _transition_item(site_id, new_severity):
    return {"type": "transition", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "NORMAL", "new_severity": new_severity,
        "current_value": 1500.0, "unit": "cfs", "percentile": 91.0,
        "direction": "RISING",
    }}


def _adapter():
    mock_adapter = MagicMock()
    mock_adapter.channel = "telegram"
    mock_adapter.send.return_value = True
    return mock_adapter


def test_floods_page_does_not_receive_a_high_transition(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, sensitivity="floods")
    adapter = _adapter()
    q = queue.Queue()
    q.put(_transition_item(site_id, "HIGH"))
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()


def test_floods_page_receives_a_severe_high_transition(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, sensitivity="floods")
    adapter = _adapter()
    q = queue.Queue()
    q.put(_transition_item(site_id, "SEVERE HIGH"))
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once()


def test_floods_page_receives_the_all_clear_transition(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, sensitivity="floods")
    adapter = _adapter()
    q = queue.Queue()
    q.put({"type": "transition", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "previous_severity": "SEVERE HIGH", "new_severity": "NORMAL",
        "current_value": 500.0, "unit": "cfs", "percentile": 40.0,
        "direction": "FALLING",
    }})
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once()


def test_unusual_page_skips_trend_but_all_page_gets_it(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, sensitivity="unusual")
    trend = {"type": "trend", "data": {
        "site_id": site_id, "site_number": "12345678", "station_name": "Test",
        "unit": "ft", "direction": "RISING", "delta": 2.5, "hours": 6.0,
        "start_value": 10.0, "end_value": 12.5,
    }}
    adapter = _adapter()
    q = queue.Queue()
    q.put(trend)
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()
    _set_page(tmp_db, page_id, sensitivity="all")
    q.put(trend)
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once()


def test_paused_page_receives_nothing(tmp_db):
    init_db(tmp_db)
    page_id, site_id = _page_with_site(tmp_db)
    _set_page(tmp_db, page_id, status="paused")
    adapter = _adapter()
    q = queue.Queue()
    q.put(_transition_item(site_id, "SEVERE HIGH"))
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()


def test_paused_page_receives_no_noaa_transition(tmp_db):
    init_db(tmp_db)
    from db.models import (create_user_page, get_page_by_public_token,
                           get_or_create_noaa_gauge, link_page_gauge,
                           add_page_subscriber)
    pub, _ = create_user_page("P", tmp_db)
    page = get_page_by_public_token(pub, tmp_db)
    gauge_id = get_or_create_noaa_gauge("MLUK2", "M", 21.0, 23.0, 30.0, 38.0, tmp_db)
    link_page_gauge(page["id"], gauge_id, tmp_db)
    add_page_subscriber(page["id"], "telegram", "chat9", "S", tmp_db)
    _set_page(tmp_db, page["id"], status="paused")
    adapter = _adapter()
    q = queue.Queue()
    q.put({"type": "noaa_transition", "data": {
        "gauge_id": gauge_id, "lid": "MLUK2", "station_name": "M",
        "previous_severity": "Normal", "new_severity": "Action",
        "current_stage": 22.0}})
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_not_called()


def test_direct_item_sends_to_one_chat_without_subscribers(tmp_db):
    init_db(tmp_db)
    adapter = _adapter()
    q = queue.Queue()
    q.put({"type": "direct", "data": {
        "channel": "telegram", "channel_id": "777", "message": "hello"}})
    NotificationDispatcher(q, adapters=[adapter], db_path=tmp_db).run_once()
    adapter.send.assert_called_once_with("777", "hello")
