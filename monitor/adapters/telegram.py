import asyncio
import threading
import logging

from db.models import get_db, get_setting

logger = logging.getLogger(__name__)

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramAdapter(threading.Thread):
    channel = "telegram"

    def __init__(self, db_path=None, stop_event=None):
        super().__init__(name="TelegramAdapter", daemon=True)
        self.db_path = db_path
        self.stop_event = stop_event or threading.Event()
        self._app = None
        self._loop = None

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
        chat_id = str(update.effective_chat.id)
        conn = get_db(self.db_path)
        conn.execute(
            "INSERT OR IGNORE INTO pending_registrations (channel, channel_id) VALUES ('telegram', ?)",
            (chat_id,)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            "Welcome to the River Level Monitor!\n"
            "Type /subscribe to receive river condition alerts."
        )

    async def _handle_subscribe(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        chat_id = str(update.effective_chat.id)
        name = update.effective_user.full_name or "Telegram User"
        conn = get_db(self.db_path)
        conn.execute(
            "INSERT OR IGNORE INTO subscribers (display_name, channel, channel_id) VALUES (?, 'telegram', ?)",
            (name, chat_id)
        )
        conn.execute(
            "UPDATE subscribers SET active=1, display_name=? WHERE channel='telegram' AND channel_id=?",
            (name, chat_id)
        )
        conn.execute(
            "DELETE FROM pending_registrations WHERE channel='telegram' AND channel_id=?",
            (chat_id,)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text("✓ You are now subscribed to river level alerts.")

    async def _handle_unsubscribe(self, update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
        chat_id = str(update.effective_chat.id)
        conn = get_db(self.db_path)
        conn.execute(
            "UPDATE subscribers SET active=0 WHERE channel='telegram' AND channel_id=?",
            (chat_id,)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text("You have been unsubscribed.")

    # ── Thread entry point ────────────────────────────────────────────────────

    def run(self):
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed")
            return

        token = get_setting("telegram_bot_token", self.db_path)
        if not token:
            logger.warning("Telegram bot token not configured — adapter disabled")
            return

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

        logger.info("TelegramAdapter started polling")
        self._app.run_polling(stop_signals=None)
        logger.info("TelegramAdapter stopped")

    def stop(self):
        if self._app and self._loop:
            asyncio.run_coroutine_threadsafe(self._app.stop(), self._loop)
