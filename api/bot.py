"""
Bot blueprint — /api/bot
Telegram webhook command handler.
"""

import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify
from bot_lib import redis_client, dexscreener, safety, telegram

logger = logging.getLogger(__name__)

bot_bp = Blueprint("bot", __name__)

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


@bot_bp.route("/api/bot", methods=["POST"])
def bot_webhook():
    update = request.get_json(silent=True)
    if not update:
        return jsonify({"error": "Bad request"}), 400
    try:
        _dispatch(update)
    except Exception as exc:
        logger.error("Unhandled error in _dispatch: %s", exc)
    return jsonify({"ok": True}), 200


@bot_bp.route("/api/bot", methods=["GET"])
def bot_health():
    return jsonify({"status": "Telegram webhook is ready"}), 200


def _dispatch(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return
    chat_id = str(message.get("chat", {}).get("id", ""))
    user_id = str(message.get("from", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    parts = text.split()
    command = parts[0].split("@")[0].lower()
    args = parts[1:]
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
        telegram.send_text(chat_id, "Unknown command. Use /help.")


def _is_owner(user_id: str) -> bool:
    if not OWNER_TELEGRAM_ID:
        return False
    return user_id == str(OWNER_TELEGRAM_ID)


def _cmd_start(chat_id: str, user_id: str):
    if not _is_owner(user_id):
        return
    try:
        redis_client.set_scanning_enabled(True)
        telegram.send_text(chat_id,
            "✅ <b>Scanning enabled.</b>\nThe bot will now scan for new tokens every ~2 minutes.")
    except Exception as exc:
        telegram.send_text(chat_id, f"❌ Failed to enable scanning: {exc}")


def _cmd_stop(chat_id: str, user_id: str):
    if not _is_owner(user_id):
        return
    try:
        redis_client.set_scanning_enabled(False)
        telegram.send_text(chat_id,
            "⏸ <b>Scanning paused.</b>\nSend /start to resume.")
    except Exception as exc:
        telegram.send_text(chat_id, f"❌ Failed to disable scanning: {exc}")


def _cmd_status(chat_id: str):
    lines = ["<b>Robin Token Alert — Status</b>", ""]
    try:
        enabled = redis_client.is_scanning_enabled()
        lines.append(f"🔍 <b>Scanning:</b> {'ON ✅' if enabled else 'OFF ⏸'}")
    except Exception as exc:
        lines.append(f"🔍 <b>Scanning:</b> ❌ Redis error — {exc}")
    try:
        count = redis_client.get_alerted_count()
        lines.append(f"📋 <b>Tokens alerted (total):</b> {count if count >= 0 else '❌ Redis error'}")
    except Exception as exc:
        lines.append(f"📋 <b>Tokens alerted:</b> ❌ {exc}")
    lines.append(f"🤖 <b>Bot token:</b> {'configured ✅' if TELEGRAM_BOT_TOKEN else 'MISSING ❌'}")
    telegram.send_text(chat_id, "\n".join(lines))


def _cmd_scan(chat_id: str, args: list):
    if not args:
        telegram.send_text(chat_id,
            "Usage: /scan &lt;contract_address&gt;\nExample: /scan 0xabc123...")
        return
    contract_address = args[0].strip().lower()
    if not contract_address.startswith("0x") or len(contract_address) < 10:
        telegram.send_text(chat_id,
            f"❌ <code>{contract_address}</code> doesn't look like a valid contract address.")
        return
    telegram.send_text(chat_id, f"🔍 Scanning <code>{contract_address}</code>…")
    token = dexscreener.get_token_info(contract_address)
    if token is None:
        telegram.send_text(chat_id, telegram.format_not_found(contract_address))
        return
    try:
        safety_result = safety.run_safety_checks(token)
    except Exception as exc:
        telegram.send_text(chat_id, f"❌ Safety check failed: {exc}")
        return
    telegram.send_scan_result(chat_id, token, safety_result)
