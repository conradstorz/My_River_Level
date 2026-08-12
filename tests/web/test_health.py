import threading

import pytest
from db.models import init_db
from web.app import create_app


@pytest.fixture
def app(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    init_db(tmp_db)
    application = create_app(db_path=tmp_db)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def live_thread():
    """A thread that stays alive until the test finishes."""
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="LiveThread", daemon=True)
    t.start()
    yield t
    stop.set()
    t.join(timeout=2)


@pytest.fixture
def dead_thread():
    """A thread that has already finished, so is_alive() is False."""
    t = threading.Thread(target=lambda: None, name="DeadThread", daemon=True)
    t.start()
    t.join(timeout=2)
    return t


def test_healthz_ok_without_registry(app):
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "threads": {}}


def test_healthz_reports_ok_when_all_threads_alive(app, live_thread):
    app.config["THREAD_REGISTRY"] = {"LiveThread": live_thread}
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["threads"] == {"LiveThread": True}


def test_healthz_reports_degraded_when_a_thread_is_dead(app, live_thread, dead_thread):
    app.config["THREAD_REGISTRY"] = {"LiveThread": live_thread, "DeadThread": dead_thread}
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["threads"] == {"LiveThread": True, "DeadThread": False}


def test_create_app_accepts_thread_registry(tmp_db, monkeypatch, live_thread):
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    init_db(tmp_db)
    application = create_app(db_path=tmp_db,
                             thread_registry={"LiveThread": live_thread})
    application.config["TESTING"] = True
    resp = application.test_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["threads"] == {"LiveThread": True}
