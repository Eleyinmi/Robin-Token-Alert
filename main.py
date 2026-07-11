#!/usr/bin/env python3
import os
import sys
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

WELCOME_TEXT = (
    "👋 <b>Welcome to Robin Token Alert!</b>\n\n"
    "I monitor new token launches on Robinhood Chain every minute and run "
    "automated safety checks before sending alerts.\n\n"
    "<b>What I check:</b>\n"
    "✅ Honeypot detection\n"
    "✅ Liquidity depth\n"
    "✅ LP lock status\n"
    "✅ Holder concentration\n\n"
    "<b>Commands:</b>\n"
    "/status — check scanning status\n"
    "/watch — get notified of ALL new launches\n"
    "/scan &lt;address&gt; — safety check any contract\n"
    "/help — full command list\n\n"
    "<i>Alerts are posted automatically when new tokens pass all checks.</i>"
)

OWNER_ENABLED_TEXT = (
    "✅ <b>Scanning enabled!</b>\n\n"
    "I will now scan for new Robinhood Chain tokens every minute and send "
    "alerts here for any that pass safety checks.\n\n"
    "Send /stop to pause at any time."
)

HELP_TEXT = (
    "<b>Robin Token Alert — Commands</b>\n\n"
    "<b>Safety scan (owner only):</b>\n"
    "/start — Enable safety-checked alerts\n"
    "/stop — Pause safety-checked alerts\n\n"
    "<b>Watch mode (all new launches):</b>\n"
    "/watch — Enable raw launch notifications\n"
    "/unwatch — Pause raw launch notifications\n"
    "/watchfilter — Toggle MC&lt;10k + social filter on/off\n\n"
    "<b>Channels:</b>\n"
    "/addchannel — Add this chat to broadcast list\n"
    "/removechannel — Remove this chat from broadcast list\n\n"
    "<b>General:</b>\n"
    "/status — Show full status\n"
    "/scan &lt;address&gt; — Safety check any token\n"
    "/help — Show this message"
)

MAIN_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "📊 Status", "callback_data": "/status"},
        {"text": "❓ Help",   "callback_data": "/help"},
    ]]
}

SCAN_CONTROL_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "⏸ Stop scanning", "callback_data": "/stop"},
        {"text": "📊 Status",        "callback_data": "/status"},
    ]]
}

SCAN_RESUME_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "▶️ Start scanning", "callback_data": "/start"},
        {"text": "📊 Status",         "callback_data": "/status"},
    ]]
}

WATCH_CONTROL_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "⏸ Stop watching",   "callback_data": "/unwatch"},
            {"text": "📊 Status",          "callback_data": "/status"},
        ],
        [
            {"text": "🔽 Enable filter (MC<10k + social)", "callback_data": "/watchfilter"},
        ],
    ]
}

WATCH_CONTROL_FILTER_ON_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "⏸ Stop watching",    "callback_data": "/unwatch"},
            {"text": "📊 Status",           "callback_data": "/status"},
        ],
        [
            {"text": "🔼 Disable filter",   "callback_data": "/watchfilter"},
        ],
    ]
}

WATCH_RESUME_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "👁 Start watching", "callback_data": "/watch"},
        {"text": "📊 Status",         "callback_data": "/status"},
    ]]
}


def get_updates(offset):
    import requests
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getUpdates"
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

    tg.set_bot_commands()

    offset = redis_client.get_update_offset()
    updates = get_updates(offset)

    if not updates:
        return

    logger.info("Processing %d Telegram update(s)", len(updates))

    for update in updates:
        update_id = update.get("update_id", 0)
        offset = update_id + 1

        callback = update.get("callback_query")
        if callback:
            chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
            user_id = str(callback.get("from", {}).get("id", ""))
            text = callback.get("data", "")
            tg.answer_callback_query(callback.get("id", ""))
        else:
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
                if is_owner:
                    redis_client.set_scanning_enabled(True)
                    tg.send_text(chat_id, OWNER_ENABLED_TEXT, keyboard=SCAN_CONTROL_KEYBOARD)
                else:
                    tg.send_text(chat_id, WELCOME_TEXT, keyboard=MAIN_KEYBOARD)

            elif cmd == "/stop":
                if not is_owner:
                    continue
                redis_client.set_scanning_enabled(False)
                tg.send_text(
                    chat_id,
                    "⏸ <b>Scanning paused.</b>\n\nSafety-checked alerts stopped. Send /start to resume.",
                    keyboard=SCAN_RESUME_KEYBOARD,
                )

            elif cmd == "/watch":
                if not is_owner:
                    continue
                redis_client.set_watch_enabled(True)
                filter_on = redis_client.is_watch_filter_enabled()
                filter_status = "ON ✅ (MC&lt;10k + social)" if filter_on else "OFF — all launches"
                kb = WATCH_CONTROL_FILTER_ON_KEYBOARD if filter_on else WATCH_CONTROL_KEYBOARD
                tg.send_text(
                    chat_id,
                    f"👁 <b>Watch mode enabled!</b>\n\n"
                    f"I will send a notification for new token launches on Robinhood Chain.\n\n"
                    f"<b>Filter:</b> {filter_status}\n\n"
                    f"Use the button below to toggle the filter on/off.\n"
                    f"Send /unwatch to pause.",
                    keyboard=kb,
                )

            elif cmd == "/unwatch":
                if not is_owner:
                    continue
                redis_client.set_watch_enabled(False)
                tg.send_text(
                    chat_id,
                    "⏸ <b>Watch mode paused.</b>\n\nRaw launch notifications stopped. Send /watch to resume.",
                    keyboard=WATCH_RESUME_KEYBOARD,
                )

            elif cmd == "/watchfilter":
                if not is_owner:
                    continue
                current = redis_client.is_watch_filter_enabled()
                new_state = not current
                redis_client.set_watch_filter_enabled(new_state)
                if new_state:
                    msg_text = (
                        "🔽 <b>Watch filter ON</b>\n\n"
                        "Watch alerts will only fire for tokens that have:\n"
                        "• Market cap under <b>$10,000</b>\n"
                        "• A website or Twitter/X account\n\n"
                        "This reduces noise and focuses on early-stage launches with a social presence."
                    )
                    kb = WATCH_CONTROL_FILTER_ON_KEYBOARD
                else:
                    msg_text = (
                        "🔼 <b>Watch filter OFF</b>\n\n"
                        "Watch alerts will now fire for <b>ALL</b> new launches, no filtering."
                    )
                    kb = WATCH_CONTROL_KEYBOARD
                tg.send_text(chat_id, msg_text, keyboard=kb)

            elif cmd == "/addchannel":
                if not is_owner:
                    continue
                redis_client.add_broadcast_channel(chat_id)
                tg.send_text(
                    chat_id,
                    f"✅ <b>Channel added!</b>\n\n"
                    f"This chat (<code>{chat_id}</code>) will now receive all alerts.\n\n"
                    f"<i>Tip: forward this bot to any channel, make it an admin, then send /addchannel in that channel.</i>",
                )

            elif cmd == "/removechannel":
                if not is_owner:
                    continue
                redis_client.remove_broadcast_channel(chat_id)
                tg.send_text(
                    chat_id,
                    f"✅ <b>Channel removed.</b>\n\nThis chat (<code>{chat_id}</code>) will no longer receive alerts.",
                )

            elif cmd == "/status":
                lines = ["<b>Robin Token Alert — Status</b>", ""]
                try:
                    enabled = redis_client.is_scanning_enabled()
                    lines.append("🔍 Safety scan: " + ("ON ✅" if enabled else "OFF ⏸"))
                except Exception as exc:
                    lines.append("🔍 Safety scan: ❌ " + str(exc))
                try:
                    watch = redis_client.is_watch_enabled()
                    filter_on = redis_client.is_watch_filter_enabled()
                    watch_label = "ON ✅" if watch else "OFF ⏸"
                    if watch and filter_on:
                        watch_label += " (filter: MC&lt;10k + social)"
                    lines.append("👁 Watch mode: " + watch_label)
                except Exception as exc:
                    lines.append("👁 Watch mode: ❌ " + str(exc))
                try:
                    count = redis_client.get_alerted_count()
                    lines.append("📋 Tokens alerted: " + (str(count) if count >= 0 else "❌"))
                except Exception as exc:
                    lines.append("📋 Tokens alerted: ❌ " + str(exc))
                try:
                    channels = redis_client.get_broadcast_channels()
                    lines.append(f"📡 Broadcast channels: {len(channels)}")
                except Exception:
                    pass
                lines.append("🤖 Bot: Online ✅")
                tg.send_text(chat_id, "\n".join(lines), keyboard=MAIN_KEYBOARD)

            elif cmd == "/scan":
                if not args:
                    tg.send_text(chat_id, "Usage: /scan &lt;contract_address&gt;\n\nExample:\n<code>/scan 0xabc123...</code>")
                    continue
                addr = args[0].strip().lower()
                if not addr.startswith("0x") or len(addr) < 10:
                    tg.send_text(chat_id, "❌ Invalid address: <code>" + addr + "</code>\n\nMust start with <code>0x</code>.")
                    continue
                tg.send_text(chat_id, "🔍 Scanning <code>" + addr + "</code>…\n\n<i>This may take a few seconds.</i>")
                token = dexscreener.get_token_info(addr)
                if token is None:
                    tg.send_text(chat_id, f"❌ Token <code>{addr}</code> not found on DexScreener or GMGN.\n\nDouble-check the contract address.")
                    continue
                result = safety.run_safety_checks(token)
                tg.send_scan_result(chat_id, token, result)

            elif cmd == "/help":
                tg.send_text(chat_id, HELP_TEXT, keyboard=MAIN_KEYBOARD)

        except Exception as exc:
            logger.error("Command dispatch error (%s): %s", cmd, exc)

    redis_client.set_update_offset(offset)


def _has_social_profile(token: dict) -> bool:
    socials = token.get("socials") or {}
    return bool(socials.get("website") or socials.get("twitter"))


def _merge_token_lists(*lists) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for tokens in lists:
        for token in tokens:
            addr = token.get("contract_address", "")
            if addr and addr not in seen:
                seen.add(addr)
                merged.append(token)
    return merged


def _get_broadcast_targets(redis_client, default_chat_id: str) -> list[str]:
    """Return all channels to broadcast to, always including the default."""
    targets = set()
    if default_chat_id:
        targets.add(default_chat_id)
    try:
        for ch in redis_client.get_broadcast_channels():
            if ch:
                targets.add(ch)
    except Exception:
        pass
    return list(targets)


def run_scan():
    from bot_lib import redis_client, hoodfun, gmgn, dexscreener, safety, telegram as tg

    scan_enabled = redis_client.is_scanning_enabled()
    watch_enabled = redis_client.is_watch_enabled()
    watch_filter = redis_client.is_watch_filter_enabled()

    if not scan_enabled and not watch_enabled:
        logger.info("Both scanning and watch mode are disabled — skipping")
        return

    # Fetch from all sources and merge (hood.fun first — earliest launch data)
    hood_tokens = hoodfun.get_new_pairs()
    gmgn_tokens = gmgn.get_new_pairs()
    dex_tokens = dexscreener.get_new_pairs()

    new_tokens = _merge_token_lists(hood_tokens, gmgn_tokens, dex_tokens)
    logger.info(
        "Found %d new token(s) total (hood.fun=%d gmgn=%d dex=%d)",
        len(new_tokens), len(hood_tokens), len(gmgn_tokens), len(dex_tokens),
    )

    import os as _os
    default_chat = _os.environ.get("TELEGRAM_CHAT_ID", "")
    broadcast_targets = _get_broadcast_targets(redis_client, default_chat)
    logger.info("Broadcasting to %d channel(s)", len(broadcast_targets))

    alerted = watch_sent = skipped_duplicate = skipped_fail = skipped_error = 0

    for token in new_tokens:
        contract = token["contract_address"]

        # --- Watch mode ---
        if watch_enabled and not redis_client.has_been_watch_alerted(contract):
            should_alert = True
            if watch_filter:
                mc = token.get("market_cap_usd", 0) or 0
                passes_mc = mc > 0 and mc < 10_000
                passes_social = _has_social_profile(token)
                should_alert = passes_mc and passes_social
                if not should_alert:
                    logger.debug(
                        "Watch filter skipped %s — mc=$%.0f passes_mc=%s social=%s",
                        contract, mc, passes_mc, passes_social,
                    )

            try:
                redis_client.mark_watch_alerted(contract)
            except Exception:
                pass

            if should_alert:
                for target in broadcast_targets:
                    try:
                        tg.send_watch_alert(token, chat_id=target)
                    except Exception as exc:
                        logger.error("Watch alert error for %s to %s: %s", contract, target, exc)
                watch_sent += 1

        # --- Safety scan mode ---
        if not scan_enabled:
            continue

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

        sent_any = False
        for target in broadcast_targets:
            try:
                if tg.send_alert(token, result, chat_id=target):
                    sent_any = True
            except Exception as exc:
                logger.error("Alert send error for %s to %s: %s", contract, target, exc)
                skipped_error += 1

        if sent_any:
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            alerted += 1

        time.sleep(0.5)

    logger.info(
        "Scan done — safety_alerted=%d watch_sent=%d duplicates=%d failed_safety=%d errors=%d",
        alerted, watch_sent, skipped_duplicate, skipped_fail, skipped_error,
    )


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
