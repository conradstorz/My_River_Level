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

def test_settings_redirects_to_first_group(client):
    response = client.get("/settings")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/monitoring")


def test_monitoring_page_shows_fields_and_all_tabs(client):
    response = client.get("/settings/monitoring")
    assert response.status_code == 200
    body = response.data.decode()
    assert "Poll Interval" in body
    # tab nav lists all three groups
    assert "Monitoring" in body
    assert "Alert Thresholds" in body
    assert "Notification Channels" in body


def test_channels_page_shows_provider_subtitles(client):
    body = client.get("/settings/channels").data.decode()
    assert "Telegram" in body
    assert "Twilio" in body
    assert "Facebook" in body


def test_unknown_group_is_404(client):
    assert client.get("/settings/bogus").status_code == 404


def test_post_group_saves_only_its_fields(client, tmp_db):
    response = client.post("/settings/monitoring", data={
        "poll_interval_minutes": "30",
        "historical_start_year": "1980",
        "search_radius_miles": "25",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert get_setting("poll_interval_minutes", tmp_db) == "30"


def test_saving_one_group_leaves_other_groups_untouched(client, tmp_db):
    # Seed a monitoring value, then save the channels group.
    from db.models import set_setting
    set_setting("poll_interval_minutes", "42", tmp_db)
    client.post("/settings/channels", data={
        "telegram_bot_token": "tok",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_sms_number": "",
        "twilio_whatsapp_number": "",
        "facebook_page_token": "",
        "facebook_verify_token": "",
    }, follow_redirects=True)
    assert get_setting("poll_interval_minutes", tmp_db) == "42"  # untouched
    assert get_setting("telegram_bot_token", tmp_db) == "tok"
