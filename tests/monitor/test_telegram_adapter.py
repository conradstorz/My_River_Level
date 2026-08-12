"""Tests for the Telegram adapter's page-subscription helpers and supervisor loop.

None of these tests touch the network or start a real bot: the DB-facing logic
lives in module-level helpers and the supervisor calls a ``_run_bot(token)``
seam that is patched out.
"""

import threading
import time

from db.models import (
    create_user_page,
    get_active_page_subscribers,
    get_db,
    get_page_by_public_token,
    set_setting,
)
from monitor.adapters import telegram as telegram_mod
from monitor.adapters.telegram import (
    TelegramAdapter,
    list_chat_pages,
    subscribe_chat_to_page,
    unsubscribe_chat_everywhere,
    unsubscribe_chat_from_page,
)


# -- Page subscription helpers -----------------------------------------------

def test_subscribe_with_page_token_adds_page_subscriber(tmp_db):
    public, _ = create_user_page("Ohio at Louisville", tmp_db)
    ok, reply = subscribe_chat_to_page("555", "Ann", public, tmp_db)
    assert ok is True
    page = get_page_by_public_token(public, tmp_db)
    assert [s["channel_id"] for s in get_active_page_subscribers(page["id"], tmp_db)] == ["555"]
    assert "Ohio at Louisville" in reply


def test_subscribe_with_unknown_token_reports_failure(tmp_db):
    ok, reply = subscribe_chat_to_page("555", "Ann", "not-a-real-token", tmp_db)
    assert ok is False
    assert "couldn't find" in reply


def test_subscribe_to_inactive_page_reports_failure(tmp_db):
    public, _ = create_user_page("Closed page", tmp_db)
    page = get_page_by_public_token(public, tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("UPDATE user_pages SET active=0 WHERE id=%s", (page["id"],))
    conn.commit()
    cur.close()
    conn.close()
    ok, reply = subscribe_chat_to_page("555", "Ann", public, tmp_db)
    assert ok is False
    assert "couldn't find" in reply


def test_subscribe_again_reactivates_rather_than_duplicating(tmp_db):
    public, _ = create_user_page("P", tmp_db)
    subscribe_chat_to_page("555", "Ann", public, tmp_db)
    unsubscribe_chat_from_page("555", public, tmp_db)
    ok, _ = subscribe_chat_to_page("555", "Ann", public, tmp_db)
    assert ok is True
    page = get_page_by_public_token(public, tmp_db)
    assert [s["channel_id"] for s in get_active_page_subscribers(page["id"], tmp_db)] == ["555"]


def test_unsubscribe_from_one_page_leaves_the_other(tmp_db):
    first, _ = create_user_page("First", tmp_db)
    second, _ = create_user_page("Second", tmp_db)
    subscribe_chat_to_page("555", "Ann", first, tmp_db)
    subscribe_chat_to_page("555", "Ann", second, tmp_db)
    ok, reply = unsubscribe_chat_from_page("555", first, tmp_db)
    assert ok is True
    assert "First" in reply
    assert get_active_page_subscribers(get_page_by_public_token(first, tmp_db)["id"], tmp_db) == []
    remaining = get_active_page_subscribers(get_page_by_public_token(second, tmp_db)["id"], tmp_db)
    assert [s["channel_id"] for s in remaining] == ["555"]


def test_unsubscribe_from_unknown_page_reports_failure(tmp_db):
    ok, reply = unsubscribe_chat_from_page("555", "not-a-real-token", tmp_db)
    assert ok is False
    assert "couldn't find" in reply


def test_unsubscribe_all_clears_every_page(tmp_db):
    public, _ = create_user_page("P", tmp_db)
    subscribe_chat_to_page("555", "Ann", public, tmp_db)
    unsubscribe_chat_everywhere("555", tmp_db)
    page = get_page_by_public_token(public, tmp_db)
    assert get_active_page_subscribers(page["id"], tmp_db) == []


def test_unsubscribe_all_deactivates_the_global_subscriber(tmp_db):
    telegram_mod.subscribe_chat_globally("555", "Ann", tmp_db)
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT active FROM subscribers WHERE channel='telegram' AND channel_id='555'")
    assert cur.fetchone()["active"] == 1
    cur.close()
    conn.close()

    unsubscribe_chat_everywhere("555", tmp_db)

    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT active FROM subscribers WHERE channel='telegram' AND channel_id='555'")
    assert cur.fetchone()["active"] == 0
    cur.close()
    conn.close()


def test_unsubscribe_all_leaves_other_chats_alone(tmp_db):
    public, _ = create_user_page("P", tmp_db)
    subscribe_chat_to_page("555", "Ann", public, tmp_db)
    subscribe_chat_to_page("666", "Bob", public, tmp_db)
    unsubscribe_chat_everywhere("555", tmp_db)
    page = get_page_by_public_token(public, tmp_db)
    assert [s["channel_id"] for s in get_active_page_subscribers(page["id"], tmp_db)] == ["666"]


def test_list_chat_pages_returns_subscribed_page_names(tmp_db):
    first, _ = create_user_page("Alpha", tmp_db)
    second, _ = create_user_page("Beta", tmp_db)
    create_user_page("Gamma", tmp_db)
    subscribe_chat_to_page("555", "Ann", first, tmp_db)
    subscribe_chat_to_page("555", "Ann", second, tmp_db)
    assert list_chat_pages("555", tmp_db) == ["Alpha", "Beta"]


def test_list_chat_pages_empty_when_not_subscribed(tmp_db):
    create_user_page("Alpha", tmp_db)
    assert list_chat_pages("555", tmp_db) == []


def test_global_subscribe_reply_explains_page_codes(tmp_db):
    reply = telegram_mod.subscribe_chat_globally("555", "Ann", tmp_db)
    assert "broadcast announcements" in reply
    assert "/subscribe <page code>" in reply


# -- Supervisor loop ---------------------------------------------------------

def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_adapter_waits_for_token_instead_of_exiting(tmp_db, monkeypatch):
    monkeypatch.setattr(telegram_mod, "TELEGRAM_AVAILABLE", True)
    adapter = TelegramAdapter(db_path=tmp_db)
    adapter.TOKEN_POLL_SECONDS = 0.01
    started = []
    monkeypatch.setattr(adapter, "_run_bot", lambda token: started.append(token))
    t = threading.Thread(target=adapter.run, daemon=True)
    t.start()
    try:
        # With no token configured the thread must stay alive, not return.
        time.sleep(0.1)
        assert t.is_alive()
        assert started == []

        set_setting("telegram_bot_token", "abc123", tmp_db)
        assert _wait_for(lambda: bool(started))
    finally:
        adapter.stop_event.set()
        t.join(timeout=5)
    assert t.is_alive() is False
    assert started == ["abc123"]


def test_adapter_restarts_bot_with_the_new_token(tmp_db, monkeypatch):
    monkeypatch.setattr(telegram_mod, "TELEGRAM_AVAILABLE", True)
    set_setting("telegram_bot_token", "first", tmp_db)
    adapter = TelegramAdapter(db_path=tmp_db)
    adapter.TOKEN_POLL_SECONDS = 0.01
    adapter.RESTART_BACKOFF_SECONDS = 0.01
    started = []
    monkeypatch.setattr(adapter, "_run_bot", lambda token: started.append(token))
    t = threading.Thread(target=adapter.run, daemon=True)
    t.start()
    try:
        assert _wait_for(lambda: bool(started) and started[0] == "first")
        set_setting("telegram_bot_token", "second", tmp_db)
        assert _wait_for(lambda: "second" in started)
    finally:
        adapter.stop_event.set()
        t.join(timeout=5)
    assert t.is_alive() is False


def test_adapter_survives_a_crashing_bot(tmp_db, monkeypatch):
    monkeypatch.setattr(telegram_mod, "TELEGRAM_AVAILABLE", True)
    set_setting("telegram_bot_token", "abc123", tmp_db)
    adapter = TelegramAdapter(db_path=tmp_db)
    adapter.TOKEN_POLL_SECONDS = 0.01
    adapter.RESTART_BACKOFF_SECONDS = 0.01
    calls = []

    def boom(token):
        calls.append(token)
        raise RuntimeError("bot died")

    monkeypatch.setattr(adapter, "_run_bot", boom)
    t = threading.Thread(target=adapter.run, daemon=True)
    t.start()
    try:
        assert _wait_for(lambda: len(calls) >= 2)
        assert t.is_alive()
    finally:
        adapter.stop_event.set()
        t.join(timeout=5)
    assert t.is_alive() is False


def test_token_watcher_stops_the_bot_when_the_token_changes(tmp_db):
    set_setting("telegram_bot_token", "first", tmp_db)
    adapter = TelegramAdapter(db_path=tmp_db)
    adapter.TOKEN_POLL_SECONDS = 0.01
    stopped = []

    class FakeApp:
        def stop_running(self):
            stopped.append(True)

    adapter._app = FakeApp()
    watcher = threading.Thread(target=adapter._watch_token, args=("first",), daemon=True)
    watcher.start()
    try:
        time.sleep(0.1)
        assert stopped == []
        set_setting("telegram_bot_token", "second", tmp_db)
        assert _wait_for(lambda: bool(stopped))
    finally:
        adapter._watch_stop.set()
        adapter.stop_event.set()
        watcher.join(timeout=5)


def test_token_watcher_stops_the_bot_when_stop_event_is_set(tmp_db):
    set_setting("telegram_bot_token", "first", tmp_db)
    adapter = TelegramAdapter(db_path=tmp_db)
    adapter.TOKEN_POLL_SECONDS = 0.01
    stopped = []

    class FakeApp:
        def stop_running(self):
            stopped.append(True)

    adapter._app = FakeApp()
    watcher = threading.Thread(target=adapter._watch_token, args=("first",), daemon=True)
    watcher.start()
    try:
        adapter.stop_event.set()
        assert _wait_for(lambda: bool(stopped))
    finally:
        adapter._watch_stop.set()
        watcher.join(timeout=5)
