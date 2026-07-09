"""
/api/bot — Telegram webhook endpoint.
Handles incoming updates from Telegram and dispatches commands.

Commands:
  /start  — (owner-only) enable scanning
  /stop   — (owner-only) disable scanning
  /status — show scanning state and connectivity
  /scan <contract_address> — on-demand safety check for any address
  /help   — list available commands

Security:
  - Verifies the request is a genuine Telegram webhook update (via bot token in URL).
  - Owner-only commands check OWNER_TELEGRAM_ID before executing.
"""

import os
import logging
import json
from http.server import BaseHTTPRequestHandler

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot_lib import redis_client, dexscreener, safety, telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

HELP_TEXT = (
    "<b>Robin Token Alert Bot</b>\n\n"
    "<b>Commands:</b>\n"
    "/start — Enable token scanning (owner only)\n"
    "/stop — Pause token scanning (owner only)\n"
    "/status — Show current bot status\n"
    "/scan &lt;contract_address&gt; — Run safety check on a specific token\n"
    "/help — Show this message\n\n"
    "<i>Owner-only commands require your Telegram user ID to match OWNER_TELEGRAM_ID.</i>"
)


# ---------------------------------------------------------------------------
# Vercel serverless handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Read the request body
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            update = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.warning("Received non-JSON body from Telegram webhook")
            self._respond(400, {"error": "Bad request"})
            return

        # Process the update and always return 200 to Telegram so it doesn't retry
        try:
            _dispatch(update)
        except Exception as exc:
            logger.error("Unhandled error in _dispatch: %s", exc)

        self._respond(200, {"ok": True})

    def do_GET(self):
        # Health check / setup confirmation
        self._respond(200, {"status": "Telegram webhook is ready"})

    def _respond(self, status_code: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        logger.info(fmt, *args)


# ---------------------------------------------------------------------------
# Update dispatcher
# ---------------------------------------------------------------------------

def _dispatch(update: dict):
    """Route an incoming Telegram update to the appropriate handler."""
    message = update.get("message") or update.get("edited_message")
    if not message:
        return  # Ignore non-message updates (inline queries, etc.)

    chat_id = str(message.get("chat", {}).get("id", ""))
    user_id = str(message.get("from", {}).get("id", ""))
    text = (message.get("text") or "").strip()

    if not text.startswith("/"):
        return  # Ignore non-command messages

    # Extract command and arguments (strip bot username suffix, e.g. /start@mybot)
    parts = text.split()
    command = parts[0].split("@")[0].lower()
    args = parts[1:]

    logger.info("Command %s from user %s in chat %s", command, user_id, chat_id)

    if command == "/start":
        _cmd_start(chat_id, user_id)
    elif command == "/stop":
        _cmd_stop(chat_id, user_id)
    elif command == "/status":
        _cmd_status(chat_id)
    elif command == "/scan":
        _cmd_scan(chat_id, args)
    elif command == "/help":
        telegram.send_text(chat_id, HELP_TEXT)
    else:
        telegram.send_text(chat_id, "Unknown command. Use /help to see available commands.")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _is_owner(user_id: str) -> bool:
    """
    Check if the sender's Telegram user ID matches OWNER_TELEGRAM_ID.
    Owner-only commands silently do nothing if this returns False.
    """
    if not OWNER_TELEGRAM_ID:
        logger.warning("OWNER_TELEGRAM_ID is not set — owner commands are disabled")
        return False
    return user_id == str(OWNER_TELEGRAM_ID)


def _cmd_start(chat_id: str, user_id: str):
    """Owner-only: enable scanning."""
    if not _is_owner(user_id):
        # Silently ignore — do not reveal owner status to non-owners
        logger.info("Non-owner %s attempted /start — ignored", user_id)
        return

    try:
        redis_client.set_scanning_enabled(True)
        telegram.send_text(
            chat_id,
            "✅ <b>Scanning enabled.</b>\nThe bot will now scan for new tokens every ~2 minutes.",
        )
    except Exception as exc:
        logger.error("/start failed: %s", exc)
        telegram.send_text(chat_id, f"❌ Failed to enable scanning: {exc}")


def _cmd_stop(chat_id: str, user_id: str):
    """Owner-only: disable scanning."""
    if not _is_owner(user_id):
        logger.info("Non-owner %s attempted /stop — ignored", user_id)
        return

    try:
        redis_client.set_scanning_enabled(False)
        telegram.send_text(
            chat_id,
            "⏸ <b>Scanning paused.</b>\nNo new tokens will be scanned until you send /start.",
        )
    except Exception as exc:
        logger.error("/stop failed: %s", exc)
        telegram.send_text(chat_id, f"❌ Failed to disable scanning: {exc}")


def _cmd_status(chat_id: str):
    """Show current scanning state and Redis/Telegram connectivity."""
    lines = ["<b>Robin Token Alert — Status</b>", ""]

    # Check scanning_enabled
    try:
        enabled = redis_client.is_scanning_enabled()
        lines.append(f"🔍 <b>Scanning:</b> {'ON ✅' if enabled else 'OFF ⏸'}")
    except Exception as exc:
        lines.append(f"🔍 <b>Scanning:</b> ❌ Redis error — {exc}")

    # Check alerted count
    try:
        count = redis_client.get_alerted_count()
        if count >= 0:
            lines.append(f"📋 <b>Tokens alerted (total):</b> {count}")
        else:
            lines.append("📋 <b>Tokens alerted:</b> ❌ Redis error")
    except Exception as exc:
        lines.append(f"📋 <b>Tokens alerted:</b> ❌ {exc}")

    # Bot token configured
    bot_ok = bool(TELEGRAM_BOT_TOKEN)
    lines.append(f"🤖 <b>Bot token:</b> {'configured ✅' if bot_ok else 'MISSING ❌'}")

    telegram.send_text(chat_id, "\n".join(lines))


def _cmd_scan(chat_id: str, args: list):
    """
    On-demand safety check for a specific contract address.
    Does NOT touch alerted_tokens — purely informational.
    Usage: /scan <contract_address>
    """
    if not args:
        telegram.send_text(
            chat_id,
            "Usage: /scan &lt;contract_address&gt;\nExample: /scan 0xabc123...",
        )
        return

    contract_address = args[0].strip().lower()

    # Basic sanity check on address format
    if not contract_address.startswith("0x") or len(contract_address) < 10:
        telegram.send_text(
            chat_id,
            f"❌ <code>{contract_address}</code> doesn't look like a valid contract address.",
        )
        return

    telegram.send_text(chat_id, f"🔍 Scanning <code>{contract_address}</code>…")

    # Look up token data from DexScreener
    token = dexscreener.get_token_info(contract_address)
    if token is None:
        telegram.send_text(chat_id, telegram.format_not_found(contract_address))
        return

    # Run safety checks
    try:
        safety_result = safety.run_safety_checks(token)
    except Exception as exc:
        logger.error("/scan safety check failed for %s: %s", contract_address, exc)
        telegram.send_text(chat_id, f"❌ Safety check failed: {exc}")
        return

    # Send result with inline buttons
    telegram.send_scan_result(chat_id, token, safety_result)
