"""Telegram commands for pin pages: the chat is the account.

Each chat owns at most one live page. The synchronous helpers below do all
the database work and return the reply text; the async handlers in
:class:`PinCommands` are thin wrappers that run them in a thread so psycopg2
never blocks the bot's event loop. Keeping the helpers synchronous is what
lets the tests exercise every command without starting a bot.
"""

import asyncio
import logging

from db.models import (
    SENSITIVITY_LEVELS,
    bind_page_to_chat,
    create_pin_page,
    get_page_for_chat,
    get_page_gauges,
    get_page_sites,
    get_setting,
    set_page_sensitivity,
    set_page_status,
    unlink_page_gauge,
    unlink_page_site,
)

logger = logging.getLogger(__name__)

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import CallbackQueryHandler, CommandHandler
    TELEGRAM_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the package
    TELEGRAM_AVAILABLE = False

NO_BASE_URL = ("The site address isn't configured yet, so I can't send a map link. "
               "Ask the site owner to set the public base URL.")
NO_PAGE = "You don't have a river set up yet. Send /start to pick one on the map."
TOKEN_NOT_FOUND = ("I couldn't find that page. Open the map link again and tap "
                   "\"Send alerts to Telegram\".")

SENSITIVITY_TEXT = {
    "floods": "Floods only",
    "unusual": "Floods and unusual levels",
    "all": "Everything, including rapid changes",
}


def _base_url(db_path):
    return (get_setting("public_base_url", db_path, default="") or "").strip().rstrip("/")


def map_url(edit_token, db_path=None):
    """Absolute link to the map screen for `edit_token`, or None when unconfigured."""
    base = _base_url(db_path)
    return f"{base}/pin/{edit_token}" if base else None


def edit_url(edit_token, db_path=None):
    base = _base_url(db_path)
    return f"{base}/edit/{edit_token}" if base else None


def _link_or_explain(url):
    return url if url else NO_BASE_URL


def start_chat(chat_id, display_name, arg, db_path=None):
    """Handle /start [token]: bind a web-first page, or hand out the map link."""
    arg = (arg or "").strip()
    if arg:
        page = bind_page_to_chat(arg, chat_id, display_name, db_path)
        if page is None:
            return TOKEN_NOT_FOUND
        if page["status"] == "active":
            river = page["river_name"] or "your river"
            return (f"✓ Connected. You'll get alerts for {river} here. "
                    "Send /settings any time to change gauges or sensitivity.")
        return ("✓ Connected. Finish picking your spot on the map: "
                f"{_link_or_explain(map_url(page['edit_token'], db_path))}")
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        page = create_pin_page(int(chat_id), db_path)
        bind_page_to_chat(page["edit_token"], chat_id, display_name, db_path)
    link = _link_or_explain(map_url(page["edit_token"], db_path))
    if page["status"] == "pending":
        return ("Welcome! Pick the spot on the river you care about and I'll "
                f"watch the gauges there:\n{link}")
    return (f"You're already set up for {page['river_name'] or 'your river'}. "
            f"Change it here:\n{link}\nOther commands: /settings /sensitivity "
            "/sources /pause /resume /stop")


def settings_reply(chat_id, db_path=None):
    """Handle /settings: link to the page editor."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE
    return ("Manage your gauges and sensitivity here:\n"
            f"{_link_or_explain(edit_url(page['edit_token'], db_path))}")


def sensitivity_keyboard(chat_id, db_path=None):
    """Handle /sensitivity: (text, rows of (label, callback_data))."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE, []
    current = SENSITIVITY_TEXT.get(page["sensitivity"], page["sensitivity"])
    rows = [[(SENSITIVITY_TEXT[level], f"sens:{level}")] for level in SENSITIVITY_LEVELS]
    return f"How much do you want to hear? Now: {current}", rows


def sources_keyboard(chat_id, db_path=None):
    """Handle /sources: list the page's gauges with a Remove button each."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE, []
    lines, rows = [], []
    for site in get_page_sites(page["id"], db_path):
        name = site["station_name"] or site["site_number"]
        lines.append(f"• {name} (USGS {site['site_number']})")
        rows.append([(f"Remove {name}"[:60], f"rm:usgs:{site['id']}")])
    for gauge in get_page_gauges(page["id"], db_path):
        lines.append(f"• {gauge['station_name']} (NOAA {gauge['lid']})")
        rows.append([(f"Remove {gauge['station_name']}"[:60], f"rm:noaa:{gauge['id']}")])
    if not lines:
        return "No gauges yet. Send /start to pick a spot on the map.", []
    return "You're watching:\n" + "\n".join(lines), rows


def apply_callback(chat_id, data, db_path=None):
    """Handle an inline-button press; returns the reply text."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE
    parts = (data or "").split(":")
    if parts[0] == "sens" and len(parts) == 2 and parts[1] in SENSITIVITY_LEVELS:
        set_page_sensitivity(page["id"], parts[1], db_path)
        return f"✓ Sensitivity set to: {SENSITIVITY_TEXT[parts[1]]}"
    if parts[0] == "rm" and len(parts) == 3 and parts[2].isdigit():
        row_id = int(parts[2])
        if parts[1] == "usgs":
            unlink_page_site(page["id"], row_id, db_path)
        elif parts[1] == "noaa":
            unlink_page_gauge(page["id"], row_id, db_path)
        else:
            return "I didn't understand that button."
        text, _ = sources_keyboard(chat_id, db_path)
        return "✓ Removed.\n" + text
    return "I didn't understand that button."


def set_status_reply(chat_id, status, db_path=None):
    """Handle /pause, /resume, /stop."""
    page = get_page_for_chat(chat_id, db_path)
    if page is None:
        return NO_PAGE
    set_page_status(page["id"], status, db_path)
    if status == "paused":
        return "⏸ Alerts paused. Send /resume when you want them back."
    if status == "stopped":
        return ("Stopped. Your page is closed and its gauges released. "
                "Send /start whenever you want to set up a new one.")
    return "▶️ Alerts resumed."


class PinCommands:
    """Registers the pin-page command handlers on a python-telegram-bot Application."""

    def __init__(self, db_path=None):
        self.db_path = db_path

    def register(self, app):
        app.add_handler(CommandHandler("settings", self._settings))
        app.add_handler(CommandHandler("sensitivity", self._sensitivity))
        app.add_handler(CommandHandler("sources", self._sources))
        app.add_handler(CommandHandler("pause", self._pause))
        app.add_handler(CommandHandler("resume", self._resume))
        app.add_handler(CommandHandler("stop", self._stop))
        app.add_handler(CallbackQueryHandler(self._callback, pattern=r"^(sens|rm):"))

    @staticmethod
    def _markup(rows):
        if not rows:
            return None
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
             for row in rows])

    async def _settings(self, update, context):
        reply = await asyncio.to_thread(settings_reply, update.effective_chat.id, self.db_path)
        await update.message.reply_text(reply)

    async def _sensitivity(self, update, context):
        text, rows = await asyncio.to_thread(
            sensitivity_keyboard, update.effective_chat.id, self.db_path)
        await update.message.reply_text(text, reply_markup=self._markup(rows))

    async def _sources(self, update, context):
        text, rows = await asyncio.to_thread(
            sources_keyboard, update.effective_chat.id, self.db_path)
        await update.message.reply_text(text, reply_markup=self._markup(rows))

    async def _pause(self, update, context):
        reply = await asyncio.to_thread(
            set_status_reply, update.effective_chat.id, "paused", self.db_path)
        await update.message.reply_text(reply)

    async def _resume(self, update, context):
        reply = await asyncio.to_thread(
            set_status_reply, update.effective_chat.id, "active", self.db_path)
        await update.message.reply_text(reply)

    async def _stop(self, update, context):
        reply = await asyncio.to_thread(
            set_status_reply, update.effective_chat.id, "stopped", self.db_path)
        await update.message.reply_text(reply)

    async def _callback(self, update, context):
        query = update.callback_query
        await query.answer()
        reply = await asyncio.to_thread(
            apply_callback, query.message.chat.id, query.data, self.db_path)
        await query.edit_message_text(reply)
