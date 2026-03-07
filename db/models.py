import sqlite3
import os
import uuid

# Default database path — overridden by service.py
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "river_monitor.db")

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
    # Channel credentials — empty until configured via portal
    "telegram_bot_token": "",
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "twilio_sms_number": "",
    "twilio_whatsapp_number": "",
    "facebook_page_token": "",
    "facebook_verify_token": "",
}

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_number TEXT NOT NULL UNIQUE,
    station_name TEXT NOT NULL DEFAULT '',
    parameter_code TEXT NOT NULL DEFAULT '00060',
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS site_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id),
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    current_value REAL,
    unit TEXT NOT NULL DEFAULT 'cfs',
    percentile REAL,
    severity TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    opted_in_at TEXT NOT NULL DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(channel, channel_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id INTEGER REFERENCES subscribers(id),
    site_id INTEGER REFERENCES sites(id),
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    channel TEXT NOT NULL,
    message_text TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    error_msg TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pending_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel, channel_id)
);

CREATE TABLE IF NOT EXISTS user_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_token TEXT NOT NULL UNIQUE,
    edit_token TEXT NOT NULL UNIQUE,
    page_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS noaa_gauges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lid TEXT NOT NULL UNIQUE,
    station_name TEXT NOT NULL DEFAULT '',
    current_stage REAL,
    action_stage REAL,
    minor_flood_stage REAL,
    moderate_flood_stage REAL,
    major_flood_stage REAL,
    severity TEXT NOT NULL DEFAULT 'Unknown' CHECK(severity IN ('Unknown', 'Normal', 'Action', 'Minor', 'Moderate', 'Major')),
    last_polled_at TEXT
);

CREATE TABLE IF NOT EXISTS page_noaa_gauges (
    page_id INTEGER NOT NULL REFERENCES user_pages(id),
    noaa_gauge_id INTEGER NOT NULL REFERENCES noaa_gauges(id),
    PRIMARY KEY (page_id, noaa_gauge_id)
);

CREATE TABLE IF NOT EXISTS page_subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL REFERENCES user_pages(id),
    channel TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'unsubscribed')),
    opted_in_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(page_id, channel, channel_id)
);
"""


def get_db(db_path=None):
    """Return a new SQLite connection. Caller is responsible for closing it."""
    path = db_path or DEFAULT_DB
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    """Create all tables and seed default settings."""
    conn = get_db(db_path)
    conn.executescript(SCHEMA)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    conn.commit()
    conn.close()


def get_setting(key, db_path=None, default=None):
    conn = get_db(db_path)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row is None:
        return default
    return row["value"]


def set_setting(key, value, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()
    conn.close()


def create_user_page(page_name, db_path=None):
    """Create a new user page. Returns (public_token, edit_token)."""
    public_token = str(uuid.uuid4())
    edit_token = str(uuid.uuid4())
    conn = get_db(db_path)
    conn.execute(
        "INSERT INTO user_pages (public_token, edit_token, page_name) VALUES (?, ?, ?)",
        (public_token, edit_token, page_name)
    )
    conn.commit()
    conn.close()
    return public_token, edit_token


def get_page_by_public_token(token, db_path=None):
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM user_pages WHERE public_token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_page_by_edit_token(token, db_path=None):
    conn = get_db(db_path)
    row = conn.execute("SELECT * FROM user_pages WHERE edit_token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_or_create_noaa_gauge(lid, station_name, action_stage, minor_stage,
                              moderate_stage, major_stage, db_path=None):
    """Insert gauge if not present; return its id."""
    conn = get_db(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO noaa_gauges
           (lid, station_name, action_stage, minor_flood_stage, moderate_flood_stage, major_flood_stage)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (lid, station_name, action_stage, minor_stage, moderate_stage, major_stage)
    )
    conn.commit()
    row = conn.execute("SELECT id FROM noaa_gauges WHERE lid=?", (lid,)).fetchone()
    conn.close()
    return row["id"]


def update_noaa_gauge_condition(lid, current_stage, severity, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """UPDATE noaa_gauges SET current_stage=?, severity=?, last_polled_at=datetime('now')
           WHERE lid=?""",
        (current_stage, severity, lid)
    )
    conn.commit()
    conn.close()


def get_all_noaa_gauges(db_path=None):
    conn = get_db(db_path)
    rows = conn.execute("SELECT * FROM noaa_gauges").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def link_page_gauge(page_id, gauge_id, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO page_noaa_gauges (page_id, noaa_gauge_id) VALUES (?, ?)",
        (page_id, gauge_id)
    )
    conn.commit()
    conn.close()


def unlink_page_gauge(page_id, gauge_id, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        "DELETE FROM page_noaa_gauges WHERE page_id=? AND noaa_gauge_id=?",
        (page_id, gauge_id)
    )
    conn.commit()
    conn.close()


def get_page_gauges(page_id, db_path=None):
    """Return all noaa_gauges linked to a page."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT ng.* FROM noaa_gauges ng
           JOIN page_noaa_gauges png ON png.noaa_gauge_id = ng.id
           WHERE png.page_id=?""",
        (page_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pages_for_noaa_gauge(gauge_id, db_path=None):
    """Return all active pages that include this gauge."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT up.* FROM user_pages up
           JOIN page_noaa_gauges png ON png.page_id = up.id
           WHERE png.noaa_gauge_id=? AND up.active=1""",
        (gauge_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_page_subscriber(page_id, channel, channel_id, display_name, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO page_subscribers
           (page_id, channel, channel_id, display_name, status)
           VALUES (?, ?, ?, ?, 'active')""",
        (page_id, channel, channel_id, display_name)
    )
    conn.commit()
    conn.close()


def set_page_subscriber_status(page_id, channel, channel_id, status, db_path=None):
    conn = get_db(db_path)
    conn.execute(
        """UPDATE page_subscribers SET status=?
           WHERE page_id=? AND channel=? AND channel_id=?""",
        (status, page_id, channel, channel_id)
    )
    conn.commit()
    conn.close()


def get_active_page_subscribers(page_id, db_path=None):
    conn = get_db(db_path)
    rows = conn.execute(
        "SELECT * FROM page_subscribers WHERE page_id=? AND status='active'",
        (page_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_page_subscribers_for_gauge(gauge_id, db_path=None):
    """Return all active page_subscribers for every page linked to this gauge."""
    conn = get_db(db_path)
    rows = conn.execute(
        """SELECT ps.* FROM page_subscribers ps
           JOIN page_noaa_gauges png ON png.page_id = ps.page_id
           WHERE png.noaa_gauge_id=? AND ps.status='active'""",
        (gauge_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
