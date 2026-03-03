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

def test_subscribers_page_lists_subscribers(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO subscribers (display_name, channel, channel_id) VALUES ('Alice', 'telegram', '1234')")
    conn.commit()
    conn.close()
    response = client.get("/subscribers")
    assert b"Alice" in response.data

def test_add_subscriber_post(client):
    response = client.post("/subscribers/add", data={
        "display_name": "Bob",
        "channel": "sms",
        "channel_id": "+15551234567"
    }, follow_redirects=True)
    assert response.status_code == 200

def test_remove_subscriber(client, tmp_db):
    conn = get_db(tmp_db)
    conn.execute("INSERT INTO subscribers (id, display_name, channel, channel_id) VALUES (1, 'Alice', 'telegram', '1234')")
    conn.commit()
    conn.close()
    response = client.post("/subscribers/1/remove", follow_redirects=True)
    assert response.status_code == 200
    conn = get_db(tmp_db)
    row = conn.execute("SELECT active FROM subscribers WHERE id=1").fetchone()
    conn.close()
    assert row["active"] == 0

def test_twilio_webhook_registers_sms_subscriber(client):
    response = client.post("/webhook/twilio", data={
        "From": "+15559876543",
        "Body": "JOIN",
        "To": "+15550000000",
    })
    assert response.status_code == 200

def test_facebook_webhook_verification(client, tmp_db):
    from db.models import set_setting
    set_setting("facebook_verify_token", "mytoken", tmp_db)
    response = client.get("/webhook/facebook", query_string={
        "hub.mode": "subscribe",
        "hub.verify_token": "mytoken",
        "hub.challenge": "abc123"
    })
    assert response.status_code == 200
    assert b"abc123" in response.data
