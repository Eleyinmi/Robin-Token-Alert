#!/usr/bin/env python3
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

HELP_TEXT = (
    "<b>Robin Token Alert Bot</b>\n\n"
    "/start — Enable scanning (owner only)\n"
    "/stop — Pause scanning (owner only)\n"
    "/status — Show bot status\n"
    "/scan <address> — On-demand safety check\n"
    "/help — Show this message"
)

def get_updates(offset: int) -> list:
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        resp = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as exc:
        logger.warning("getUpdates failed: %s", exc)
    return []

def process_commands():
    from bot_lib import redis_client, dexscreener, safety, telegram as tg
    offset = redis_client.get_update_offset()
    updates = get_updates(offset)
    if not updates:
        return
    logger.info("Processing %d Telegram update(s)", len(updates))
    for update in updates:
        update_id = update.get("update_id", 0)
        offset = update_id + 1
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            continue
        chat_id = str(msg.get("chat", {}).get("id", ""))
        user_id = str(msg.get("from", {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        is_owner = OWNER_TELEGRAM_ID and user_id == str(OWNER_TELEGRAM_ID)
        try:
            if cmd == "/start":
                if not is_owner:
                    continue
                redis_client.set_scanning_enabled(True)
                tg.send_text(chat_id, "✅ <b>Scanning enabled.</b>\nBot will scan for new tokens every ~2 minutes.")
            elif cmd == "/stop":
                if not is_owner:
                    continue
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
                    continue
                addr = args[0].strip().lower()
                if not addr.startswith("0x") or len(addr) < 10:
                    tg.send_text(chat_id, f"❌ Invalid address: <code>{addr}</code>")
                    continue
                tg.send_text(chat_id, f"🔍 Scanning <code>{addr}</code>…")
                token = dexscreener.get_token_info(addr)
                if token is None:
                    tg.send_text(chat_id, tg.format_not_found(addr))
                    continue
                result = safety.run_safety_checks(token)
                tg.send_scan_result(chat_id, token, result)
            elif cmd == "/help":
                tg.send_text(chat_id, HELP_TEXT)
        except Exception as exc:
            logger.error("Command dispatch error (%s): %s", cmd, exc)
    redis_client.set_update_offset(offset)

def run_scan():
    from bot_lib import redis_client, dexscreener, safety, telegram as tg
    if not redis_client.is_scanning_enabled():
        logger.info("Scanning is disabled — skipping token scan")
        return
    new_tokens = dexscreener.get_new_pairs()
    logger.info("Found %d new token(s)", len(new_tokens))
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
    logger.info("Scan done — alerted=%d duplicates=%d failed_safety=%d errors=%d",
        alerted, skipped_duplicate, skipped_fail, skipped_error)

if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set — aborting")
        sys.exit(1)
    logger.info("=== Robin Token Alert run started ===")
    logger.info("Step 1: Processing Telegram commands…")
    process_commands()
    logger.info("Step 2: Running token scan…")
    run_scan()
    logger.info("=== Run complete ===")
