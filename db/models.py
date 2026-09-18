"""PostgreSQL schema, initialization, and data-access helpers for River Monitor.

All persistence goes through this module (psycopg2 with RealDictCursor). It
defines the table schema and default settings, and provides the helper
functions used to read and write sites, settings, subscribers, user pages, and
NOAA gauges. Each helper opens and closes its own connection; the optional
``db_path`` argument is treated as a PostgreSQL URL (defaults to ``DATABASE_URL``).
"""
import os
import uuid
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://river:river@db:5432/rivermonitor"
)

DEFAULT_SETTINGS = {
    "poll_interval_minutes": "15",
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
    "facebook_app_secret": "",
    "rate_change_threshold_ft": "2.0",
    "rate_change_threshold_pct": "25",
    "rate_change_window_hours": "6",
    "rate_change_min_interval_hours": "6",
    "site_stale_hours": "6",
    "forecast_poll_hours": "6",
    # Pin onboarding
    "discovery_reach_km": "50",
    "public_base_url": "",
    "telegram_bot_username": "",
}

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS sites (
        id SERIAL PRIMARY KEY,
        site_number TEXT NOT NULL UNIQUE,
        station_name TEXT NOT NULL DEFAULT '',
        parameter_code TEXT NOT NULL DEFAULT '00060',
        active INTEGER NOT NULL DEFAULT 1,
        added_at TEXT NOT NULL DEFAULT (NOW()::TEXT)
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS site_conditions (
        id SERIAL PRIMARY KEY,
        site_id INTEGER NOT NULL REFERENCES sites(id),
        checked_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        current_value DOUBLE PRECISION,
        unit TEXT NOT NULL DEFAULT 'cfs',
        percentile DOUBLE PRECISION,
        severity TEXT NOT NULL DEFAULT 'UNKNOWN'
    )""",
    """CREATE TABLE IF NOT EXISTS subscribers (
        id SERIAL PRIMARY KEY,
        display_name TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        opted_in_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        active INTEGER NOT NULL DEFAULT 1,
        UNIQUE(channel, channel_id)
    )""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id SERIAL PRIMARY KEY,
        subscriber_id INTEGER REFERENCES subscribers(id),
        site_id INTEGER REFERENCES sites(id),
        sent_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        channel TEXT NOT NULL,
        message_text TEXT NOT NULL,
        trigger_type TEXT NOT NULL,
        success INTEGER NOT NULL DEFAULT 1,
        error_msg TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS pending_registrations (
        id SERIAL PRIMARY KEY,
        channel TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        started_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        UNIQUE(channel, channel_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_pages (
        id SERIAL PRIMARY KEY,
        public_token TEXT NOT NULL UNIQUE,
        edit_token TEXT NOT NULL UNIQUE,
        page_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        active INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS noaa_gauges (
        id SERIAL PRIMARY KEY,
        lid TEXT NOT NULL UNIQUE,
        station_name TEXT NOT NULL DEFAULT '',
        current_stage DOUBLE PRECISION,
        action_stage DOUBLE PRECISION,
        minor_flood_stage DOUBLE PRECISION,
        moderate_flood_stage DOUBLE PRECISION,
        major_flood_stage DOUBLE PRECISION,
        severity TEXT NOT NULL DEFAULT 'Normal'
            CHECK(severity IN ('Unknown', 'Normal', 'Action', 'Minor', 'Moderate', 'Major')),
        last_polled_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS page_noaa_gauges (
        page_id INTEGER NOT NULL REFERENCES user_pages(id),
        noaa_gauge_id INTEGER NOT NULL REFERENCES noaa_gauges(id),
        PRIMARY KEY (page_id, noaa_gauge_id)
    )""",
    """CREATE TABLE IF NOT EXISTS page_subscribers (
        id SERIAL PRIMARY KEY,
        page_id INTEGER NOT NULL REFERENCES user_pages(id),
        channel TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'paused', 'unsubscribed')),
        opted_in_at TEXT NOT NULL DEFAULT (NOW()::TEXT),
        UNIQUE(page_id, channel, channel_id)
    )""",
    """CREATE TABLE IF NOT EXISTS page_sites (
        page_id INTEGER NOT NULL REFERENCES user_pages(id),
        site_id INTEGER NOT NULL REFERENCES sites(id),
        PRIMARY KEY (page_id, site_id)
    )""",
    """CREATE TABLE IF NOT EXISTS noaa_observations (
        id SERIAL PRIMARY KEY,
        lid TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        stage DOUBLE PRECISION NOT NULL,
        UNIQUE (lid, observed_at)
    )""",
    """CREATE TABLE IF NOT EXISTS gauge_forecasts (
        id SERIAL PRIMARY KEY,
        lid TEXT NOT NULL,
        issued_at TIMESTAMPTZ NOT NULL,
        valid_at TIMESTAMPTZ NOT NULL,
        predicted_stage DOUBLE PRECISION NOT NULL,
        UNIQUE (lid, issued_at, valid_at)
    )""",
]

# Additive-only changes applied to tables that already exist in production.
# Every statement must be safe to re-run and must never drop or rewrite an
# existing column; init_db runs these after SCHEMA_STATEMENTS on every start.
MIGRATION_STATEMENTS = [
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS last_error_at TIMESTAMPTZ",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS quality_grade TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS quality_detail TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS quality_checked_at TIMESTAMPTZ",
    # Tri-state on purpose: TRUE = NOAA publishes a forecast, FALSE = NOAA
    # confirmed it does not, NULL = we have never successfully checked. Never
    # collapse NULL into FALSE — that would tell users a gauge has no forecast
    # when all we really had was a failed HTTP request.
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS has_forecast BOOLEAN",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS forecast_checked_at TIMESTAMPTZ",
    "CREATE INDEX IF NOT EXISTS idx_site_conditions_site_id ON site_conditions (site_id, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_site_trigger ON notifications (site_id, trigger_type, id DESC)",
    # Pin-on-a-map onboarding (spec 2026-09-17). A page owned by a Telegram
    # chat carries its pin, river, sensitivity dial and lifecycle status;
    # sources provisioned by users are marked so the retirement sweep can
    # deactivate them once nobody references them, and never touch admin rows.
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS owner_chat_id BIGINT",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS pin_lat DOUBLE PRECISION",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS pin_lon DOUBLE PRECISION",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS river_name TEXT",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'unusual' "
    "CHECK (sensitivity IN ('floods', 'unusual', 'all'))",
    "ALTER TABLE user_pages ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active' "
    "CHECK (status IN ('pending', 'active', 'paused', 'stopped'))",
    "ALTER TABLE sites ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'admin' "
    "CHECK (origin IN ('admin', 'user'))",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'admin' "
    "CHECK (origin IN ('admin', 'user'))",
    "ALTER TABLE noaa_gauges ADD COLUMN IF NOT EXISTS active INTEGER NOT NULL DEFAULT 1",
    "CREATE INDEX IF NOT EXISTS idx_user_pages_owner_chat ON user_pages (owner_chat_id)",
]


def get_conn(db_url=None):
    """Return a new psycopg2 connection with RealDictCursor."""
    url = db_url or DATABASE_URL
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def get_db(db_path=None):
    """Alias for get_conn — db_path is treated as a PostgreSQL URL."""
    return get_conn(db_path)


def init_db(db_path=None):
    """Create all tables, apply additive migrations, and seed default settings."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        for stmt in MIGRATION_STATEMENTS:
            cur.execute(stmt)
        for key, value in DEFAULT_SETTINGS.items():
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                (key, value)
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_setting(key, db_path=None, default=None):
    """Return the setting value for `key` as a str, or `default` if unset."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if row is None:
        return default
    return row["value"]


def set_setting(key, value, db_path=None):
    """Upsert `key` with the string form of `value` into the settings table."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, str(value))
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def create_user_page(page_name, db_path=None):
    """Create a new user page. Returns (public_token, edit_token)."""
    public_token = str(uuid.uuid4())
    edit_token = str(uuid.uuid4())
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO user_pages (public_token, edit_token, page_name) VALUES (%s, %s, %s)",
            (public_token, edit_token, page_name)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return public_token, edit_token


def get_page_by_public_token(token, db_path=None):
    """Return the user_pages row for `public_token` as a dict, or None if not found."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE public_token=%s", (token,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


def get_page_by_edit_token(token, db_path=None):
    """Return the user_pages row for `edit_token` as a dict, or None if not found."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE edit_token=%s", (token,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


SENSITIVITY_LEVELS = ("floods", "unusual", "all")
PAGE_STATUSES = ("pending", "active", "paused", "stopped")
PIN_PAGE_NAME = "My river"


def create_pin_page(owner_chat_id, db_path=None):
    """Create a pending pin page, optionally owned by a Telegram chat; return its row."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO user_pages
               (public_token, edit_token, page_name, owner_chat_id, status)
               VALUES (%s, %s, %s, %s, 'pending') RETURNING *""",
            (str(uuid.uuid4()), str(uuid.uuid4()), PIN_PAGE_NAME, owner_chat_id)
        )
        row = cur.fetchone()
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return dict(row)


def get_page_for_chat(chat_id, db_path=None):
    """Return the newest non-stopped page owned by `chat_id`, or None."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT * FROM user_pages
               WHERE owner_chat_id=%s AND status <> 'stopped'
               ORDER BY id DESC LIMIT 1""",
            (int(chat_id),)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


def bind_page_to_chat(edit_token, chat_id, display_name, db_path=None):
    """Make `chat_id` the owner and an active subscriber of the page.

    Returns the updated row, or None when the token is unknown, the page is
    stopped, or another chat already owns it. A page that already has its
    pin becomes active; one still waiting for a pin stays pending.
    """
    chat_id = int(chat_id)
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE edit_token=%s", (edit_token,))
        page = cur.fetchone()
        if page is None or page["status"] == "stopped":
            return None
        if page["owner_chat_id"] is not None and page["owner_chat_id"] != chat_id:
            return None
        # Only a pending page is promoted: re-binding a paused page (the
        # owner tapping the deep link again) must not silently undo the pause.
        if page["status"] == "pending" and page["pin_lat"] is not None:
            new_status = "active"
        else:
            new_status = page["status"]
        cur.execute(
            "UPDATE user_pages SET owner_chat_id=%s, status=%s WHERE id=%s RETURNING *",
            (chat_id, new_status, page["id"])
        )
        updated = cur.fetchone()
        cur.execute(
            """INSERT INTO page_subscribers
               (page_id, channel, channel_id, display_name, status)
               VALUES (%s, 'telegram', %s, %s, 'active')
               ON CONFLICT (page_id, channel, channel_id)
               DO UPDATE SET display_name = EXCLUDED.display_name, status = 'active'""",
            (page["id"], str(chat_id), display_name or "Telegram User")
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return dict(updated)


def save_pin(page_id, lat, lon, river_name, sensitivity, usgs_sites, noaa_gauges,
             db_path=None):
    """Store a page's pin and replace its sources, provisioning any new ones.

    Everything happens in one transaction so a failure part-way leaves the
    page as it was. Sources are inserted with origin='user'; an existing row
    keeps its origin but is reactivated, so a gauge the retirement sweep
    switched off comes back the moment someone selects it again.
    """
    if sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError(f"unknown sensitivity {sensitivity!r}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE user_pages
               SET pin_lat=%s, pin_lon=%s, river_name=%s, sensitivity=%s,
                   status = CASE WHEN owner_chat_id IS NULL THEN 'pending' ELSE 'active' END
               WHERE id=%s""",
            (lat, lon, river_name, sensitivity, page_id)
        )
        cur.execute("DELETE FROM page_sites WHERE page_id=%s", (page_id,))
        cur.execute("DELETE FROM page_noaa_gauges WHERE page_id=%s", (page_id,))
        for site in usgs_sites:
            cur.execute(
                """INSERT INTO sites (site_number, station_name, parameter_code, origin, active)
                   VALUES (%s, %s, %s, 'user', 1)
                   ON CONFLICT (site_number) DO UPDATE SET active = 1
                   RETURNING id""",
                (site["site_number"], site.get("station_name", ""),
                 site.get("parameter_code", "00065"))
            )
            site_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO page_sites (page_id, site_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (page_id, site_id)
            )
        for gauge in noaa_gauges:
            cur.execute(
                """INSERT INTO noaa_gauges
                   (lid, station_name, action_stage, minor_flood_stage,
                    moderate_flood_stage, major_flood_stage, origin, active)
                   VALUES (%s, %s, %s, %s, %s, %s, 'user', 1)
                   ON CONFLICT (lid) DO UPDATE SET active = 1
                   RETURNING id""",
                (gauge["lid"], gauge.get("station_name", ""),
                 gauge.get("action_stage"), gauge.get("minor_flood_stage"),
                 gauge.get("moderate_flood_stage"), gauge.get("major_flood_stage"))
            )
            gauge_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO page_noaa_gauges (page_id, noaa_gauge_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                (page_id, gauge_id)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def set_page_sensitivity(page_id, sensitivity, db_path=None):
    """Set the page's sensitivity dial; ValueError on an unknown level."""
    if sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError(f"unknown sensitivity {sensitivity!r}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE user_pages SET sensitivity=%s WHERE id=%s", (sensitivity, page_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def set_page_status(page_id, status, db_path=None):
    """Set the page's lifecycle status; ValueError on an unknown status."""
    if status not in PAGE_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("UPDATE user_pages SET status=%s WHERE id=%s", (status, page_id))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage,
                              moderate_stage, major_stage, db_path=None,
                              origin="admin"):
    """Insert gauge if not present; return its id.

    An existing gauge keeps its original origin but is reactivated: a user
    re-selecting a gauge the retirement sweep switched off must get polling
    back without admin help.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO noaa_gauges
               (lid, station_name, action_stage, minor_flood_stage,
                moderate_flood_stage, major_flood_stage, origin)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (lid) DO UPDATE SET active = 1""",
            (lid, station_name, action_stage, minor_stage, moderate_stage,
             major_stage, origin)
        )
        conn.commit()
        cur.execute("SELECT id FROM noaa_gauges WHERE lid=%s", (lid,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return row["id"]


def update_noaa_gauge_condition(lid, current_stage, severity, db_path=None):
    """Update the current stage, severity, and last-polled timestamp for the gauge with `lid`."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE noaa_gauges SET current_stage=%s, severity=%s, last_polled_at=(NOW()::TEXT)
               WHERE lid=%s""",
            (current_stage, severity, lid)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_all_noaa_gauges(db_path=None, active_only=False):
    """Return noaa_gauges rows as dicts; `active_only` skips retired gauges."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if active_only:
            cur.execute("SELECT * FROM noaa_gauges WHERE active=1 ORDER BY id")
        else:
            cur.execute("SELECT * FROM noaa_gauges ORDER BY id")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def link_page_gauge(page_id, gauge_id, db_path=None):
    """Link a gauge to a page (insert into page_noaa_gauges); a no-op if already linked."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO page_noaa_gauges (page_id, noaa_gauge_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (page_id, gauge_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def unlink_page_gauge(page_id, gauge_id, db_path=None):
    """Remove the link between a page and a gauge (delete from page_noaa_gauges)."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM page_noaa_gauges WHERE page_id=%s AND noaa_gauge_id=%s",
            (page_id, gauge_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_page_gauges(page_id, db_path=None):
    """Return all noaa_gauges linked to a page."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ng.* FROM noaa_gauges ng
               JOIN page_noaa_gauges png ON png.noaa_gauge_id = ng.id
               WHERE png.page_id=%s""",
            (page_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_pages_for_noaa_gauge(gauge_id, db_path=None):
    """Return all active pages that include this gauge."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT up.* FROM user_pages up
               JOIN page_noaa_gauges png ON png.page_id = up.id
               WHERE png.noaa_gauge_id=%s AND up.active=1""",
            (gauge_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def add_page_subscriber(page_id, channel, channel_id, display_name, db_path=None):
    """Upsert a page subscriber, setting status to 'active'.

    On conflict for an existing (page_id, channel, channel_id) the display name
    is refreshed and the subscriber is reactivated.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO page_subscribers
               (page_id, channel, channel_id, display_name, status)
               VALUES (%s, %s, %s, %s, 'active')
               ON CONFLICT (page_id, channel, channel_id)
               DO UPDATE SET display_name = EXCLUDED.display_name, status = 'active'""",
            (page_id, channel, channel_id, display_name)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def set_page_subscriber_status(page_id, channel, channel_id, status, db_path=None):
    """Update the status ('active', 'paused', or 'unsubscribed') of a page subscriber."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE page_subscribers SET status=%s
               WHERE page_id=%s AND channel=%s AND channel_id=%s""",
            (status, page_id, channel, channel_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_active_page_subscribers(page_id, db_path=None):
    """Return all active page_subscribers rows for `page_id` as a list of dicts."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM page_subscribers WHERE page_id=%s AND status='active'",
            (page_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_page_subscribers_for_gauge(gauge_id, db_path=None):
    """Return active page_subscribers (plus the page's sensitivity) for every
    live page linked to this gauge.

    A page is live when the admin flag `active` is set AND its lifecycle
    `status` is 'active' — paused, stopped and pending pages are silent.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ps.*, up.sensitivity FROM page_subscribers ps
               JOIN page_noaa_gauges png ON png.page_id = ps.page_id
               JOIN user_pages up ON up.id = ps.page_id
               WHERE png.noaa_gauge_id=%s AND ps.status='active'
                 AND up.active=1 AND up.status='active'""",
            (gauge_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def link_page_site(page_id, site_id, db_path=None):
    """Link a USGS site to a page (insert into page_sites); a no-op if already linked."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO page_sites (page_id, site_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (page_id, site_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def unlink_page_site(page_id, site_id, db_path=None):
    """Remove the link between a page and a USGS site (delete from page_sites)."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM page_sites WHERE page_id=%s AND site_id=%s",
            (page_id, site_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_page_sites(page_id, db_path=None):
    """Return all sites rows linked to a page, as a list of dicts."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT s.* FROM sites s
               JOIN page_sites ps ON ps.site_id = s.id
               WHERE ps.page_id=%s
               ORDER BY s.id""",
            (page_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_pages_for_site(site_id, db_path=None):
    """Return all active pages that include this USGS site."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT up.* FROM user_pages up
               JOIN page_sites ps ON ps.page_id = up.id
               WHERE ps.site_id=%s AND up.active=1
               ORDER BY up.id""",
            (site_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def get_page_subscribers_for_site(site_id, db_path=None):
    """Return active page_subscribers (plus the page's sensitivity) for every
    live page linked to this site.

    A page is live when the admin flag `active` is set AND its lifecycle
    `status` is 'active' — paused, stopped and pending pages are silent.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ps.*, up.sensitivity FROM page_subscribers ps
               JOIN page_sites pst ON pst.page_id = ps.page_id
               JOIN user_pages up ON up.id = ps.page_id
               WHERE pst.site_id=%s AND ps.status='active'
                 AND up.active=1 AND up.status='active'
               ORDER BY ps.id""",
            (site_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def record_site_fetch_success(site_id, db_path=None):
    """Stamp a successful USGS fetch for `site_id` and clear the stored error."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE sites SET last_success_at=NOW(), last_error='', last_error_at=NULL WHERE id=%s",
            (site_id,)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def record_site_fetch_error(site_id, message, db_path=None):
    """Record why the most recent USGS fetch for `site_id` produced no reading."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE sites SET last_error=%s, last_error_at=NOW() WHERE id=%s",
            (str(message), site_id)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_sites_with_health(db_path=None):
    """Return every site with last_success_at/last_error plus a `stale` flag.

    `stale` is True when the site has never reported successfully or its last
    successful fetch is older than the `site_stale_hours` setting.
    """
    stale_hours = float(get_setting("site_stale_hours", db_path, default="6"))
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT *,
                      (last_success_at IS NULL
                       OR last_success_at < NOW() - (%s * INTERVAL '1 hour')) AS stale
               FROM sites ORDER BY id""",
            (stale_hours,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def record_noaa_observation(lid, stage, observed_at=None, db_path=None):
    """Store an observed NOAA stage; a duplicate (lid, observed_at) is ignored."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if observed_at is None:
            cur.execute(
                """INSERT INTO noaa_observations (lid, stage) VALUES (%s, %s)
                   ON CONFLICT (lid, observed_at) DO NOTHING""",
                (lid, stage)
            )
        else:
            cur.execute(
                """INSERT INTO noaa_observations (lid, observed_at, stage)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (lid, observed_at) DO NOTHING""",
                (lid, observed_at, stage)
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_noaa_observations(lid, db_path=None):
    """Return every stored observation for `lid`, oldest first."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT lid, observed_at, stage FROM noaa_observations
               WHERE lid=%s ORDER BY observed_at""",
            (lid,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def record_forecast_points(lid, issued_at, points, db_path=None):
    """Archive forecast points for `lid` issued at `issued_at`.

    `points` is a list of dicts with keys "valid_at" (datetime) and "stage"
    (float). Returns the number of rows actually inserted; points already
    archived for the same (lid, issued_at, valid_at) are ignored.
    """
    inserted = 0
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        for point in points:
            cur.execute(
                """INSERT INTO gauge_forecasts (lid, issued_at, valid_at, predicted_stage)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (lid, issued_at, valid_at) DO NOTHING""",
                (lid, issued_at, point["valid_at"], point["stage"])
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return inserted


def get_forecast_points(lid, db_path=None):
    """Return every archived forecast point for `lid`, oldest issue first."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT lid, issued_at, valid_at, predicted_stage FROM gauge_forecasts
               WHERE lid=%s ORDER BY issued_at, valid_at""",
            (lid,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def set_gauge_forecast_availability(lid, has_forecast, db_path=None):
    """Record whether NOAA publishes a forecast for `lid`.

    Pass True when a forecast was retrieved and False only when NOAA
    positively answered that there is none. Never call this after a failed
    request — leaving the column NULL is what lets the portal say "not yet
    assessed" instead of wrongly claiming the gauge has no forecast.
    """
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE noaa_gauges
               SET has_forecast=%s, forecast_checked_at=NOW()
               WHERE lid=%s""",
            (bool(has_forecast), lid)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def set_gauge_quality(lid, grade, detail, db_path=None):
    """Store the flood-prediction quality grade and explanation for a NOAA gauge."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE noaa_gauges
               SET quality_grade=%s, quality_detail=%s, quality_checked_at=NOW()
               WHERE lid=%s""",
            (grade, detail, lid)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_gauge_quality(lid, db_path=None):
    """Return {grade, detail, checked_at} for a gauge, or None if never scored."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT quality_grade, quality_detail, quality_checked_at FROM noaa_gauges WHERE lid=%s",
            (lid,)
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if row is None or not row["quality_grade"]:
        return None
    return {
        "grade": row["quality_grade"],
        "detail": row["quality_detail"],
        "checked_at": str(row["quality_checked_at"]) if row["quality_checked_at"] else "",
    }
