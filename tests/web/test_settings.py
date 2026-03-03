import pytest
from db.models import init_db, get_db, get_setting
from web.app import create_app

@pytest.fixture
def client(tmp_db):
    init_db(tmp_db)
    app = create_app(db_path=tmp_db)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_settings_page_shows_current_values(client):
    response = client.get("/settings")
    assert b"poll_interval" in response.data or b"Poll Interval" in response.data

def test_settings_post_updates_value(client, tmp_db):
    response = client.post("/settings", data={
        "poll_interval_minutes": "30",
        "low_percentile": "10",
        "high_percentile": "90",
        "very_low_percentile": "5",
        "very_high_percentile": "95",
        "reminder_low_high_hours": "24",
        "reminder_severe_hours": "4",
        "historical_start_year": "1980",
        "search_radius_miles": "25",
        "telegram_bot_token": "",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_sms_number": "",
        "twilio_whatsapp_number": "",
        "facebook_page_token": "",
        "facebook_verify_token": "",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert get_setting("poll_interval_minutes", tmp_db) == "30"
