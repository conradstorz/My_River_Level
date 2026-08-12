"""Notification adapter for the Telegram Bot API.

The thread is a *supervisor*: it waits for `telegram_bot_token` to appear in the
settings table, runs the bot with it, and restarts the bot when the token
changes. Entering a token in the Settings page therefore takes effect without a
container restart.

Command handlers keep their blocking psycopg2 work out of the event loop by
delegating to the module-level helpers below via `asyncio.to_thread`.
"""

import asyncio
import threading
import logging

from db.models import (
    add_page_subscriber,
    get_db,
    get_page_by_public_token,
    get_setting,
    set_page_subscriber_status,
)

logger = logging.getLogger(__name__)

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


CHANNEL = "telegram"

PAGE_NOT_FOUND_REPLY = (
    "I couldn't find a page with that code. "
    "Check the link your page owner sent you."
)

GLOBAL_SUBSCRIBE_REPLY = (
    "✓ Subscribed to broadcast announcements. To get alerts for a "
    "specific river page, send /subscribe <page code> — find the code on "
    "your page's web address."
)


# ── Module-level DB helpers (synchronous, testable without a bot) ─────────────

def subscribe_chat_globally(chat_id, display_name, db_path=None):
    """Activate the broadcast-only subscriber row for `chat_id`; return the reply text."""
    chat_id = str(chat_id)
    name = display_name or "Telegram User"
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO subscribers (display_name, channel, channel_id)
               VALUES (%s, 'telegram', %s)
               ON CONFLICT (channel, channel_id) DO NOTHING""",
            (name, chat_id)
        )
        cur.execute(
            "UPDATE subscribers SET active=1, display_name=%s WHERE channel='telegram' AND channel_id=%s",
            (name, chat_id)
        )
        cur.execute(
            "DELETE FROM pending_registrations WHERE channel='telegram' AND channel_id=%s",
            (chat_id,)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return GLOBAL_SUBSCRIBE_REPLY


def subscribe_chat_to_page(chat_id, display_name, page_token, db_path=None):
    """Subscribe `chat_id` to the page with `page_token`. Returns (ok, reply text)."""
    page = _active_page(page_token, db_path)
    if page is None:
        return False, PAGE_NOT_FOUND_REPLY
    add_page_subscriber(
        page["id"], CHANNEL, str(chat_id), display_name or "Telegram User", db_path
    )
    return True, f"✓ You'll get alerts for {page['page_name']}."


def unsubscribe_chat_from_page(chat_id, page_token, db_path=None):
    """Unsubscribe `chat_id` from one page. Returns (ok, reply text)."""
    page = _active_page(page_token, db_path)
    if page is None:
        return False, PAGE_NOT_FOUND_REPLY
    set_page_subscriber_status(
        page["id"], CHANNEL, str(chat_id), "unsubscribed", db_path
    )
    return True, f"You'll no longer get alerts for {page['page_name']}."


def unsubscribe_chat_everywhere(chat_id, db_path=None):
    """Deactivate the broadcast subscriber and every page subscription. Returns reply text."""
    chat_id = str(chat_id)
    pages = _chat_page_rows(chat_id, db_path)
    for row in pages:
        set_page_subscriber_status(
            row["page_id"], CHANNEL, chat_id, "unsubscribed", db_path
        )
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE subscribers SET active=0 WHERE channel='telegram' AND channel_id=%s",
            (chat_id,)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()
    if pages:
        names = ", ".join(row["page_name"] for row in pages)
        return (
            "You have been unsubscribed from broadcast announcements and "
            f"from: {names}."
        )
    return "You have been unsubscribed."


def list_chat_pages(chat_id, db_path=None):
    """Return the names of the pages `chat_id` is actively subscribed to."""
    return [row["page_name"] for row in _chat_page_rows(str(chat_id), db_path)]


def _active_page(page_token, db_path=None):
    """Return the active user_pages row for `page_token`, or None."""
    if not page_token:
        return None
    page = get_page_by_public_token(str(page_token).strip(), db_path)
    if not page or not page.get("active"):
        return None
    return page


def _chat_page_rows(chat_id, db_path=None):
    """Return [{page_id, page_name}] for every page this chat actively subscribes to."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ps.page_id AS page_id, up.page_name AS page_name
               FROM page_subscribers ps
               JOIN user_pages up ON up.id = ps.page_id
               WHERE ps.channel='telegram' AND ps.channel_id=%s AND ps.status='active'
               ORDER BY up.page_name, ps.page_id""",
            (str(chat_id),)
        )
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()
    return [dict(r) for r in rows]


def _record_pending_registration(chat_id, db_path=None):
    """Record a /start so the portal can see who has met the bot."""
    conn = get_db(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO pending_registrations (channel, channel_id)
               VALUES ('telegram', %s)
               ON CONFLICT (channel, channel_id) DO NOTHING""",
            (str(chat_id),)
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


class TelegramAdapter(threading.Thread):
    """Runs a Telegram bot in its own thread, handling inbound commands and outbound alerts."""

    channel = "telegram"

    #: How often the supervisor re-reads `telegram_bot_token`.
    TOKEN_POLL_SECONDS = 30
    #: How long to wait before restarting a bot that returned or crashed.
    RESTART_BACKOFF_SECONDS = 5

    def __init__(self, db_path=None, stop_event=None):
        """Initialize the adapter with an optional DB path and shared stop event."""
        super().__init__(name="TelegramAdapter", daemon=True)
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()
        self._app = None
        self._loop = None
        self._watch_stop = threading.Event()

    # ── Outbound ──────────────────────────────────────────────────────────────

    def send(self, chat_id, message):
        """Send a message to a chat_id. Called from NotificationDispatcher thread."""
        if self._loop is None or self._app is None:
            logger.warning("Telegram adapter not ready")
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._app.bot.send_message(chat_id=chat_id, text=message),
            self._loop
        )
        try:
            future.result(timeout=10)
            return True
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    # ── Inbound (bot handlers) ────────────────────────────────────────────────

    async def _handle_start(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        """Handle /start: record a pending registration and explain the commands."""
        chat_id = str(update.effective_chat.id)
        await asyncio.to_thread(_record_pending_registration, chat_id, self.db_path)
        await update.message.reply_text(
            "Welcome to the River Level Monitor!\n"
            "Send /subscribe <page code> to get alerts for a specific river page "
            "— the code is in your page's web address.\n"
            "Send /subscribe on its own for broadcast announcements only.\n"
            "Other commands: /mypages, /unsubscribe"
        )

    async def _handle_subscribe(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        """Handle /subscribe [page code]: subscribe to one page, or to broadcasts."""
        chat_id = str(update.effective_chat.id)
        name = update.effective_user.full_name or "Telegram User"
        args = getattr(context, "args", None) or []
        if args:
            _, reply = await asyncio.to_thread(
                subscribe_chat_to_page, chat_id, name, args[0], self.db_path
            )
        else:
            reply = await asyncio.to_thread(
                subscribe_chat_globally, chat_id, name, self.db_path
            )
        await update.message.reply_text(reply)

    async def _handle_unsubscribe(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        """Handle /unsubscribe [page code]: leave one page, or everything."""
        chat_id = str(update.effective_chat.id)
        args = getattr(context, "args", None) or []
        if args:
            _, reply = await asyncio.to_thread(
                unsubscribe_chat_from_page, chat_id, args[0], self.db_path
            )
        else:
            reply = await asyncio.to_thread(
                unsubscribe_chat_everywhere, chat_id, self.db_path
            )
        await update.message.reply_text(reply)

    async def _handle_mypages(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        """Handle /mypages: list the pages this chat receives alerts for."""
        chat_id = str(update.effective_chat.id)
        names = await asyncio.to_thread(list_chat_pages, chat_id, self.db_path)
        if names:
            listing = "\n".join(f"• {n}" for n in names)
            reply = f"You get alerts for:\n{listing}"
        else:
            reply = (
                "You aren't subscribed to any river pages yet. "
                "Send /subscribe <page code> to add one."
            )
        await update.message.reply_text(reply)

    # ── Thread entry point ────────────────────────────────────────────────────

    def run(self):
        """Wait for a bot token, run the bot, and restart it whenever the token changes."""
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed")
            return

        logger.info("TelegramAdapter supervisor started")
        waiting_logged = False
        while not self.stop_event.is_set():
            try:
                token = self._current_token()
                if not token:
                    if not waiting_logged:
                        logger.warning(
                            "Telegram bot token not configured — waiting for it "
                            "to be set in Settings"
                        )
                        waiting_logged = True
                    self.stop_event.wait(timeout=self.TOKEN_POLL_SECONDS)
                    continue
                waiting_logged = False
                self._run_bot(token)
            except Exception:
                logger.exception("Telegram bot failed — restarting")
            if self.stop_event.is_set():
                break
            self.stop_event.wait(timeout=self.RESTART_BACKOFF_SECONDS)
        logger.info("TelegramAdapter supervisor stopped")

    def _current_token(self):
        """Return the configured bot token, or '' when unset."""
        return (get_setting("telegram_bot_token", self.db_path) or "").strip()

    def _run_bot(self, token):
        """Build and run the bot for `token`, returning when it should be restarted."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._app = (
            Application.builder()
            .token(token)
            .build()
        )
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("subscribe", self._handle_subscribe))
        self._app.add_handler(CommandHandler("unsubscribe", self._handle_unsubscribe))
        self._app.add_handler(CommandHandler("mypages", self._handle_mypages))

        self._watch_stop = threading.Event()
        watcher = threading.Thread(
            target=self._watch_token,
            args=(token,),
            name="TelegramTokenWatcher",
            daemon=True,
        )
        watcher.start()

        logger.info("TelegramAdapter started polling")
        try:
            self._app.run_polling(stop_signals=None)
        finally:
            self._watch_stop.set()
            self._app = None
            self._loop = None
            logger.info("TelegramAdapter stopped polling")

    def _watch_token(self, token):
        """Stop the running bot once the token changes or the thread is asked to stop."""
        while not self._watch_stop.wait(self.TOKEN_POLL_SECONDS):
            try:
                stopping = self.stop_event.is_set() or self._current_token() != token
            except Exception:
                logger.exception("Could not re-read the Telegram bot token")
                continue
            if stopping:
                logger.info("Telegram bot token changed or shutdown requested — stopping bot")
                self._stop_running_app()
                return

    def _stop_running_app(self):
        """Ask the running Application to return from run_polling()."""
        app = self._app
        if app is None:
            return
        try:
            app.stop_running()
        except Exception:
            logger.exception("Failed to stop the Telegram Application")

    def stop(self):
        """Signal the supervisor and any running bot to shut down."""
        self.stop_event.set()
        self._watch_stop.set()
        self._stop_running_app()
