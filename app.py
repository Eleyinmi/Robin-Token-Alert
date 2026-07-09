"""
Robin Token Alert — Flask entrypoint for Vercel.

Routes:
  POST/GET /api/scan  — scheduled scan called by GitHub Actions every 2 min
  POST/GET /api/bot   — Telegram webhook command handler
"""

import os
import sys
import logging
import time

# Make bot_lib importable when running as a Vercel serverless function
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------

SCAN_SECRET = os.environ.get("SCAN_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

HELP_TEXT = (
    "<b>Robin Token Alert Bot</b>\n\n"
    "<b>Commands:</b>\n"
    "/start — Enable token scanning (owner only)\n"
    "/stop — Pause token scanning (owner only)\n"
    "/status — Show current bot status\n"
    "/scan &lt;contract_address&gt; — Run safety check on a specific token\n"
    "/help — Show this message"
)


# ---------------------------------------------------------------------------
# /api/scan — scheduled scan
# ---------------------------------------------------------------------------

@app.route("/api/scan", methods=["GET", "POST"])
def scan():
    from bot_lib import redis_client, dexscreener, safety, telegram

    provided = request.headers.get("X-Scan-Secret", "")
    if not SCAN_SECRET or provided != SCAN_SECRET:
        logger.warning("Scan request rejected: invalid or missing X-Scan-Secret")
        return jsonify({"error": "Unauthorized"}), 401

    if not redis_client.is_scanning_enabled():
        logger.info("Scanning is disabled — skipping")
        return jsonify({"status": "skipped", "reason": "scanning_disabled"}), 200

    logger.info("Fetching new pairs from DexScreener...")
    new_tokens = dexscreener.get_new_pairs()
    logger.info("Found %d new token(s)", len(new_tokens))

    if not new_tokens:
        return jsonify({"status": "ok", "new_tokens": 0, "alerted": 0}), 200

    alerted = 0
    skipped_duplicate = 0
    skipped_fail = 0
    skipped_error = 0

    for token in new_tokens:
        contract = token["contract_address"]

        if redis_client.has_been_alerted(contract):
            logger.info("Skipping %s — already alerted", contract)
            skipped_duplicate += 1
            continue

        try:
            safety_result = safety.run_safety_checks(token)
        except Exception as exc:
            logger.error("Safety check crashed for %s: %s", contract, exc)
            skipped_error += 1
            continue

        logger.info("Token %s (%s) — safety: %s", token["symbol"], contract,
                    safety_result["safety_status"])

        if safety_result["safety_status"] == "FAIL":
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            skipped_fail += 1
            continue

        try:
            sent = telegram.send_alert(token, safety_result)
        except Exception as exc:
            logger.error("Telegram send failed for %s: %s", contract, exc)
            skipped_error += 1
            continue

        if sent:
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            alerted += 1
        else:
            skipped_error += 1

        time.sleep(0.5)

    summary = {
        "status": "ok",
        "new_tokens": len(new_tokens),
        "alerted": alerted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_fail": skipped_fail,
        "skipped_error": skipped_error,
    }
    logger.info("Scan complete: %s", summary)
    return jsonify(summary), 200


# ---------------------------------------------------------------------------
# /api/bot — Telegram webhook
# ---------------------------------------------------------------------------

@app.route("/api/bot", methods=["POST"])
def bot_webhook():
    from bot_lib import redis_client, dexscreener, safety, telegram

    update = request.get_json(silent=True)
    if not update:
        return jsonify({"error": "Bad request"}), 400

    try:
        _dispatch(update, redis_client, dexscreener, safety, telegram)
    except Exception as exc:
        logger.error("Unhandled error in _dispatch: %s", exc)

    return jsonify({"ok": True}), 200


@app.route("/api/bot", methods=["GET"])
def bot_health():
    return jsonify({"status": "Telegram webhook is ready"}), 200


# ---------------------------------------------------------------------------
# Telegram command dispatcher
# ---------------------------------------------------------------------------

def _dispatch(update, redis_client, dexscreener, safety, telegram):
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

    logger.info("Command %s from user %s in chat %s", command, user_id, chat_id)

    if command == "/start":
        if not _is_owner(user_id):
            return
        try:
            redis_client.set_scanning_enabled(True)
            telegram.send_text(chat_id,
                "✅ <b>Scanning enabled.</b>\nThe bot will scan for new tokens every ~2 minutes.")
        except Exception as exc:
            telegram.send_text(chat_id, f"❌ Failed to enable scanning: {exc}")

    elif command == "/stop":
        if not _is_owner(user_id):
            return
        try:
            redis_client.set_scanning_enabled(False)
            telegram.send_text(chat_id,
                "⏸ <b>Scanning paused.</b>\nSend /start to resume.")
        except Exception as exc:
            telegram.send_text(chat_id, f"❌ Failed to disable scanning: {exc}")

    elif command == "/status":
        lines = ["<b>Robin Token Alert — Status</b>", ""]
        try:
            enabled = redis_client.is_scanning_enabled()
            lines.append(f"🔍 <b>Scanning:</b> {'ON ✅' if enabled else 'OFF ⏸'}")
        except Exception as exc:
            lines.append(f"🔍 <b>Scanning:</b> ❌ Redis error — {exc}")
        try:
            count = redis_client.get_alerted_count()
            lines.append(f"📋 <b>Tokens alerted:</b> {count if count >= 0 else '❌ Redis error'}")
        except Exception as exc:
            lines.append(f"📋 <b>Tokens alerted:</b> ❌ {exc}")
        lines.append(f"🤖 <b>Bot token:</b> {'configured ✅' if TELEGRAM_BOT_TOKEN else 'MISSING ❌'}")
        telegram.send_text(chat_id, "\n".join(lines))

    elif command == "/scan":
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

    elif command == "/help":
        telegram.send_text(chat_id, HELP_TEXT)

    else:
        telegram.send_text(chat_id, "Unknown command. Use /help.")


def _is_owner(user_id: str) -> bool:
    if not OWNER_TELEGRAM_ID:
        return False
    return user_id == str(OWNER_TELEGRAM_ID)
