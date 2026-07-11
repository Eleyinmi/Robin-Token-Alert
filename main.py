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
    "/watchfilter — Toggle MC range + social filter on/off\n"
    "/setmc &lt;max&gt; or /setmc &lt;min&gt; &lt;max&gt; — Set MC range\n\n"
    "<b>Channels:</b>\n"
    "/addchannel — Add this chat to broadcast list\n"
    "/removechannel — Remove this chat from broadcast list\n\n"
    "<b>General:</b>\n"
    "/status — Show full status\n"
    "/scan &lt;address&gt; — Safety check any token\n"
    "/test — Confirm bot can send messages here\n"
    "/diag — Check token sources + Redis state\n"
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
        is_channel_post = False
        if callback:
            chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
            user_id = str(callback.get("from", {}).get("id", ""))
            text = callback.get("data", "")
            tg.answer_callback_query(callback.get("id", ""))
        else:
            # channel_post = command sent inside a Telegram channel (bot is admin)
            # message / edited_message = private/group chat
            msg = (
                update.get("message")
                or update.get("edited_message")
                or update.get("channel_post")
            )
            if not msg:
                continue
            chat_id = str(msg.get("chat", {}).get("id", ""))
            # channel posts have no "from" — treat as owner if it's /addchannel or /removechannel
            user_id = str((msg.get("from") or {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            is_channel_post = update.get("channel_post") is not None

        if not text.startswith("/"):
            continue

        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]
        # Channel posts for /addchannel or /removechannel are always trusted
        # (whoever controls the channel controls whether it gets alerts)
        is_owner = (OWNER_TELEGRAM_ID and user_id == str(OWNER_TELEGRAM_ID)) or (
            is_channel_post and cmd in ("/addchannel", "/removechannel")
        )

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
                mc_min, mc_max = redis_client.get_watch_mc_range()
                if new_state:
                    msg_text = (
                        "🔽 <b>Watch filter ON</b>\n\n"
                        "Watch alerts will only fire for tokens that have:\n"
                        f"• Market cap between <b>${mc_min:,.0f}</b> and <b>${mc_max:,.0f}</b>\n"
                        "• A website or Twitter/X account\n\n"
                        "Use /setmc to change the MC range. Example: <code>/setmc 0 50000</code>"
                    )
                    kb = WATCH_CONTROL_FILTER_ON_KEYBOARD
                else:
                    msg_text = (
                        "🔼 <b>Watch filter OFF</b>\n\n"
                        "Watch alerts will now fire for <b>ALL</b> new launches, no filtering."
                    )
                    kb = WATCH_CONTROL_KEYBOARD
                tg.send_text(chat_id, msg_text, keyboard=kb)

            elif cmd == "/setmc":
                if not is_owner:
                    continue
                try:
                    if len(args) == 1:
                        # /setmc 50000 → sets max only, min stays 0
                        mc_min, mc_max = 0.0, float(args[0].replace(",", "").replace("k", "000").replace("K", "000"))
                    elif len(args) == 2:
                        # /setmc 1000 50000 → set min and max
                        mc_min = float(args[0].replace(",", "").replace("k", "000").replace("K", "000"))
                        mc_max = float(args[1].replace(",", "").replace("k", "000").replace("K", "000"))
                    else:
                        tg.send_text(
                            chat_id,
                            "Usage:\n"
                            "<code>/setmc 10000</code> — notify when MC is under $10,000\n"
                            "<code>/setmc 1000 50000</code> — notify when MC is between $1k and $50k\n\n"
                            "You can use <code>k</code> shorthand: <code>/setmc 1k 50k</code>"
                        )
                        continue
                    if mc_min >= mc_max:
                        tg.send_text(chat_id, "❌ Min MC must be less than max MC.")
                        continue
                    redis_client.set_watch_mc_range(mc_min, mc_max)
                    tg.send_text(
                        chat_id,
                        f"✅ <b>MC range updated</b>\n\n"
                        f"Watch filter will now match tokens with market cap between "
                        f"<b>${mc_min:,.0f}</b> and <b>${mc_max:,.0f}</b>.\n\n"
                        f"<i>Filter must also be ON (/watchfilter) for this to take effect.</i>"
                    )
                except ValueError:
                    tg.send_text(chat_id, "❌ Invalid number. Example: <code>/setmc 1000 50000</code>")

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

            elif cmd == "/test":
                if not is_owner:
                    continue
                import os as _os
                default_chat = _os.environ.get("TELEGRAM_CHAT_ID", "NOT SET")
                try:
                    channels = redis_client.get_broadcast_channels()
                    ch_list = ", ".join(channels) if channels else "none"
                except Exception:
                    ch_list = "error reading"
                tg.send_text(
                    chat_id,
                    f"✅ <b>Bot is alive and responding!</b>\n\n"
                    f"<b>Your chat ID:</b> <code>{chat_id}</code>\n"
                    f"<b>TELEGRAM_CHAT_ID env:</b> <code>{default_chat}</code>\n"
                    f"<b>Extra broadcast channels:</b> {ch_list}\n\n"
                    f"<i>If TELEGRAM_CHAT_ID does not match your chat ID and you want alerts here, "
                    f"update the GitHub Actions secret or send /addchannel in this chat.</i>"
                )

            elif cmd == "/diag":
                if not is_owner:
                    continue
                import requests as _req
                tg.send_text(chat_id, "🔍 <b>Running diagnostics…</b>\n<i>Probing APIs — takes ~20 seconds.</i>")
                lines = ["<b>Deep Diagnostics</b>", ""]

                # ── 1. DexScreener: what chain IDs are actually available? ──
                try:
                    r = _req.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
                    lines.append(f"DexScreener profiles: HTTP {r.status_code}")
                    if r.ok:
                        profiles = r.json() if isinstance(r.json(), list) else []
                        chain_counts: dict = {}
                        for p in profiles:
                            c = p.get("chainId", "?")
                            chain_counts[c] = chain_counts.get(c, 0) + 1
                        top = sorted(chain_counts.items(), key=lambda x: -x[1])[:8]
                        lines.append("  Chains seen: " + ", ".join(f"{c}({n})" for c, n in top))
                        rbn_count = chain_counts.get("rbn", 0)
                        lines.append(f"  'rbn' entries: {rbn_count} {'✅' if rbn_count else '❌ wrong chain ID!'}")
                except Exception as exc:
                    lines.append(f"DexScreener profiles: ❌ {exc}")

                # ── 2. DexScreener search for robinhood ──
                try:
                    r = _req.get("https://api.dexscreener.com/latest/dex/search?q=robinhood", timeout=10)
                    if r.ok:
                        pairs = r.json().get("pairs") or []
                        chains = list({p.get("chainId") for p in pairs if p.get("chainId")})[:5]
                        lines.append(f"DexScreener search 'robinhood': {len(pairs)} pairs, chains: {chains or 'none'}")
                    else:
                        lines.append(f"DexScreener search: HTTP {r.status_code}")
                except Exception as exc:
                    lines.append(f"DexScreener search: ❌ {exc}")

                # ── 3. GMGN new pairs (try rbn + robin + robinhood) ──
                lines.append("")
                for gchain in ("rbn", "robin", "robinhood"):
                    try:
                        r = _req.get(
                            f"https://gmgn.ai/defi/quotation/v1/pairs/{gchain}/new_pairs",
                            params={"limit": 5, "orderby": "open_timestamp", "direction": "desc"},
                            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                            timeout=8,
                        )
                        if r.ok:
                            pairs = (r.json().get("data") or {}).get("pairs") or r.json().get("pairs") or []
                            lines.append(f"GMGN chain '{gchain}': HTTP {r.status_code} → {len(pairs)} pairs {'✅' if pairs else ''}")
                            if pairs:
                                break
                        else:
                            lines.append(f"GMGN chain '{gchain}': HTTP {r.status_code}")
                    except Exception as exc:
                        lines.append(f"GMGN chain '{gchain}': ❌ {exc}")

                # ── 4. fun.noxa.fi — try a few endpoints ──
                lines.append("")
                noxa_endpoints = [
                    "/api/tokens?sort=createTime&order=desc&limit=5",
                    "/api/coins?limit=5",
                    "/api/v1/tokens/latest?limit=5",
                    "/api/token/list?limit=5",
                ]
                noxa_found = False
                for ep in noxa_endpoints:
                    try:
                        r = _req.get("https://fun.noxa.fi" + ep,
                                     headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                        lines.append(f"fun.noxa.fi{ep[:30]}: HTTP {r.status_code}")
                        if r.ok and r.text.strip().startswith(("{", "[")):
                            data = r.json()
                            items = data if isinstance(data, list) else (data.get("tokens") or data.get("coins") or data.get("data") or data.get("items") or [])
                            lines.append(f"  → {len(items)} item(s) {'✅' if items else ''}")
                            if items:
                                noxa_found = True
                                break
                    except Exception as exc:
                        lines.append(f"fun.noxa.fi{ep[:30]}: ❌ {exc}")
                if not noxa_found:
                    lines.append("  fun.noxa.fi: no working endpoint found yet")

                # ── 5. hood.fun ──
                lines.append("")
                for ep in ["/api/tokens?sort=createTime&order=desc&limit=5", "/api/coins?limit=5"]:
                    try:
                        r = _req.get("https://hood.fun" + ep,
                                     headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
                        lines.append(f"hood.fun{ep[:30]}: HTTP {r.status_code}")
                        if r.ok and r.text.strip().startswith(("{", "[")):
                            data = r.json()
                            items = data if isinstance(data, list) else (data.get("tokens") or data.get("coins") or data.get("data") or [])
                            lines.append(f"  → {len(items)} item(s) {'✅' if items else ''}")
                    except Exception as exc:
                        lines.append(f"hood.fun{ep[:30]}: ❌ {exc}")

                # ── 6. Redis state ──
                lines.append("")
                try:
                    scan_on = redis_client.is_scanning_enabled()
                    watch_on = redis_client.is_watch_enabled()
                    filter_on = redis_client.is_watch_filter_enabled()
                    mc_min, mc_max = redis_client.get_watch_mc_range()
                    lines.append(f"Safety scan: {'ON ✅' if scan_on else 'OFF ⏸'}")
                    lines.append(f"Watch mode: {'ON ✅' if watch_on else 'OFF ⏸'}")
                    lines.append(f"Filter: {'ON' if filter_on else 'OFF'} (${mc_min:,.0f}–${mc_max:,.0f})")
                except Exception as exc:
                    lines.append(f"Redis: ❌ {exc}")

                tg.send_text(chat_id, "\n".join(lines))

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
                mc_min, mc_max = redis_client.get_watch_mc_range()
                passes_mc = mc > 0 and mc_min <= mc <= mc_max
                passes_social = _has_social_profile(token)
                should_alert = passes_mc and passes_social
                if not should_alert:
                    logger.debug(
                        "Watch filter skipped %s — mc=$%.0f range=$%.0f-$%.0f passes_mc=%s social=%s",
                        contract, mc, mc_min, mc_max, passes_mc, passes_social,
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
