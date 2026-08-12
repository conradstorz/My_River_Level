import base64
import pytest
import queue
from db.models import init_db, get_db
from web.app import create_app

# The portal is behind HTTP Basic auth; these tests hit protected routes.
ADMIN_AUTH = "Basic " + base64.b64encode(b"admin:testpass").decode()

@pytest.fixture
def client_with_queue(tmp_db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpass")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    init_db(tmp_db)
    q = queue.Queue()
    app = create_app(db_path=tmp_db, notification_queue=q)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base["HTTP_AUTHORIZATION"] = ADMIN_AUTH
        yield c, q

def test_broadcast_get_returns_200(client_with_queue):
    c, _ = client_with_queue
    response = c.get("/broadcast")
    assert response.status_code == 200

def test_broadcast_post_puts_item_on_queue(client_with_queue, tmp_db):
    c, q = client_with_queue
    conn = get_db(tmp_db)
    cur = conn.cursor()
    cur.execute("INSERT INTO subscribers (channel, channel_id, active) VALUES ('telegram', 'chat1', 1)")
    conn.commit()
    cur.close()
    conn.close()
    response = c.post("/broadcast", data={
        "message": "Test broadcast message",
        "channels": ["telegram"],
    }, follow_redirects=True)
    assert response.status_code == 200
    assert not q.empty()
    item = q.get_nowait()
    assert item["type"] == "broadcast"
    assert "Test broadcast message" in item["data"]["message"]
