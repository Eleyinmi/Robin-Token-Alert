import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from bot_lib import redis_client, dexscreener, safety, telegram as tg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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
