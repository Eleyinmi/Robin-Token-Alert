import os
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from bot_lib import redis_client, dexscreener, safety, telegram as tg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SCAN_SECRET = os.environ.get("SCAN_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

HELP_TEXT = (
    "<b>Robin Token Alert Bot</b>\n\n"
    "/start — Enable scanning (owner only)\n"
    "/stop — Pause scanning (owner only)\n"
    "/status — Show bot status\n"
    "/scan &lt;address&gt; — On-demand safety check\n"
    "/help — Show this message"
)


# ---------------------------------------------------------------------------
# /api/scan — GitHub Actions calls this every 2 minutes
# ---------------------------------------------------------------------------

@app.route("/api/scan", methods=["GET", "POST"])
def scan():
    provided = request.headers.get("X-Scan-Secret", "")
    if not SCAN_SECRET or provided != SCAN_SECRET:
        logger.warning("Scan rejected: bad secret")
        return jsonify({"error": "Unauthorized"}), 401

    if not redis_client.is_scanning_enabled():
        return jsonify({"status": "skipped", "reason": "scanning_disabled"}), 200

    new_tokens = dexscreener.get_new_pairs()
    logger.info("Found %d new token(s)", len(new_tokens))

    if not new_tokens:
        return jsonify({"status": "ok", "new_tokens": 0, "alerted": 0}), 200

    alerted = skipped_duplicate = skipped_fail = skipped_error = 0

    for token in new_tokens:
        contract = token["contract_address"]
        if redis_client.has_been_alerted(contract):
            skipped_duplicate += 1
            continue
        try:
            result = safety.run_safety_checks(token)
        except Exception as exc:
            logger.error("Safety check error for %s: %s", contract, exc)
            skipped_error += 1
            continue

        if result["safety_status"] == "FAIL":
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            skipped_fail += 1
            continue

        try:
            sent = tg.send_alert(token, result)
        except Exception as exc:
            logger.error("Telegram send error for %s: %s", contract, exc)
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

    return jsonify({
        "status": "ok",
        "new_tokens": len(new_tokens),
        "alerted": alerted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_fail": skipped_fail,
        "skipped_error": skipped_error,
    }), 200


# ---------------------------------------------------------------------------
# /api/bot — Telegram webhook
# ---------------------------------------------------------------------------

@app.route("/api/bot", methods=["GET"])
def bot_health():
    return jsonify({"status": "Telegram webhook ready"}), 200


@app.route("/api/bot", methods=["POST"])
def bot_webhook():
    update = request.get_json(silent=True)
    if not update:
        return jsonify({"error": "Bad request"}), 400

    try:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True}), 200

        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = str(msg.get("from", {}).get("id", ""))
        text = (msg.get("text") or "").strip()

        if not text.startswith("/"):
            return jsonify({"ok": True}), 200

        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        is_owner = OWNER_TELEGRAM_ID and user_id == str(OWNER_TELEGRAM_ID)

        if cmd == "/start":
            if not is_owner:
                return jsonify({"ok": True}), 200
            redis_client.set_scanning_enabled(True)
            tg.send_text(chat_id,
                "✅ <b>Scanning enabled.</b>\nBot will scan for new tokens every ~2 minutes.")

        elif cmd == "/stop":
            if not is_owner:
                return jsonify({"ok": True}), 200
            redis_client.set_scanning_enabled(False)
            tg.send_text(chat_id, "⏸ <b>Scanning paused.</b>\nSend /start to resume.")

        elif cmd == "/status":
            lines = ["<b>Robin Token Alert — Status</b>", ""]
            try:
                enabled = redis_client.is_scanning_enabled()
                lines.append(f"🔍 Scanning: {'ON ✅' if enabled else 'OFF ⏸'}")
            except Exception as exc:
                lines.append(f"🔍 Scanning: ❌ {exc}")
            try:
                count = redis_client.get_alerted_count()
                lines.append(f"📋 Tokens alerted: {count if count >= 0 else '❌'}")
            except Exception as exc:
                lines.append(f"📋 Tokens alerted: ❌ {exc}")
            lines.append(f"🤖 Bot token: {'✅' if TELEGRAM_BOT_TOKEN else 'MISSING ❌'}")
            tg.send_text(chat_id, "\n".join(lines))

        elif cmd == "/scan":
            if not args:
                tg.send_text(chat_id, "Usage: /scan &lt;contract_address&gt;")
                return jsonify({"ok": True}), 200
            addr = args[0].strip().lower()
            if not addr.startswith("0x") or len(addr) < 10:
                tg.send_text(chat_id, f"❌ Invalid address: <code>{addr}</code>")
                return jsonify({"ok": True}), 200
            tg.send_text(chat_id, f"🔍 Scanning <code>{addr}</code>…")
            token = dexscreener.get_token_info(addr)
            if token is None:
                tg.send_text(chat_id, tg.format_not_found(addr))
                return jsonify({"ok": True}), 200
            result = safety.run_safety_checks(token)
            tg.send_scan_result(chat_id, token, result)

        elif cmd == "/help":
            tg.send_text(chat_id, HELP_TEXT)
        else:
            tg.send_text(chat_id, "Unknown command. Use /help.")

    except Exception as exc:
        logger.error("Dispatch error: %s", exc)

    return jsonify({"ok": True}), 200
