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
]


def get_conn(db_url=None):
    """Return a new psycopg2 connection with RealDictCursor."""
    url = db_url or DATABASE_URL
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def get_db(db_path=None):
    """Alias for get_conn — db_path is treated as a PostgreSQL URL."""
    return get_conn(db_path)


def init_db(db_path=None):
    """Create all tables and seed default settings."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        for stmt in SCHEMA_STATEMENTS:
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
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM user_pages WHERE edit_token=%s", (token,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return dict(row) if row else None


def get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage,
                              moderate_stage, major_stage, db_path=None):
    """Insert gauge if not present; return its id."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO noaa_gauges
               (lid, station_name, action_stage, minor_flood_stage, moderate_flood_stage, major_flood_stage)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (lid) DO NOTHING""",
            (lid, station_name, action_stage, minor_stage, moderate_stage, major_stage)
        )
        conn.commit()
        cur.execute("SELECT id FROM noaa_gauges WHERE lid=%s", (lid,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    return row["id"]


def update_noaa_gauge_condition(lid, current_stage, severity, db_path=None):
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


def get_all_noaa_gauges(db_path=None):
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM noaa_gauges")
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def link_page_gauge(page_id, gauge_id, db_path=None):
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
    """Return all active page_subscribers for every page linked to this gauge."""
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ps.* FROM page_subscribers ps
               JOIN page_noaa_gauges png ON png.page_id = ps.page_id
               WHERE png.noaa_gauge_id=%s AND ps.status='active'""",
            (gauge_id,)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]
