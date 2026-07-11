"""
Telegram message formatting, inline keyboard construction, and send functions.
Uses the Bot API directly via HTTP — no third-party library needed.
"""

import os
import logging
import datetime
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Maestro bot deep-link config — update MAESTRO_BOT_USERNAME and
# MAESTRO_DEEP_LINK_TEMPLATE in your environment variables if the parameter
# format changes. {contract_address} will be substituted at send time.
MAESTRO_BOT_USERNAME = os.environ.get("MAESTRO_BOT_USERNAME", "maestro")
MAESTRO_DEEP_LINK_TEMPLATE = os.environ.get(
    "MAESTRO_DEEP_LINK_TEMPLATE",
    "https://t.me/{bot_username}?start={contract_address}",
)

TELEGRAM_API_BASE = "https://api.telegram.org"

DISCLAIMER = (
    "\n\n⚠️ <i>Not financial advice. Automated checks catch known scam patterns "
    "only — always verify independently before trading.</i>"
)

STATUS_EMOJI = {
    "PASS": "✅",
    "CAUTION": "⚠️",
    "FAIL": "❌",
}

CHECK_STATUS_EMOJI = {
    "pass": "✅",
    "caution": "⚠️",
    "fail": "❌",
    "unavailable": "❓",
}

CHECK_LABELS = {
    "honeypot": "Honeypot check",
    "liquidity": "Liquidity",
    "liquidity_lock": "LP lock",
    "holder_conc": "Holder concentration",
}


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_alert(token: dict, safety_result: dict) -> str:
    """
    Build the full HTML-formatted alert message for a token.

    Args:
        token: dict from dexscreener.py
        safety_result: dict from safety.py run_safety_checks()
    """
    status = safety_result["safety_status"]
    status_emoji = STATUS_EMOJI.get(status, "❓")
    checks = safety_result["checks"]

    created_dt = ""
    if token.get("created_at_ms"):
        dt = datetime.datetime.utcfromtimestamp(token["created_at_ms"] / 1000)
        created_dt = dt.strftime("%Y-%m-%d %H:%M UTC")

    market_cap = (
        f"${token['market_cap_usd']:,.0f}" if token.get("market_cap_usd") else "N/A"
    )
    liquidity = (
        f"${token['liquidity_usd']:,.0f}" if token.get("liquidity_usd") else "N/A"
    )

    lines = [
        f"{status_emoji} <b>New Token Alert — {status}</b>",
        "",
        f"<b>{token['name']}</b> (<code>{token['symbol']}</code>)",
        f"<b>CA:</b> <code>{token['contract_address']}</code>",
        "",
        f"<b>Market Cap:</b> {market_cap}",
        f"<b>Liquidity:</b> {liquidity}",
        f"<b>Created:</b> {created_dt or 'Unknown'}",
        "",
        "<b>Safety Checks:</b>",
    ]

    for check_key, check_data in checks.items():
        label = CHECK_LABELS.get(check_key, check_key)
        emoji = CHECK_STATUS_EMOJI.get(check_data["status"], "❓")
        lines.append(f"  {emoji} <b>{label}:</b> {check_data['detail']}")

    if safety_result.get("failed_checks"):
        lines.append("")
        lines.append(
            f"<b>Flagged:</b> {', '.join(safety_result['failed_checks'])}"
        )

    lines.append(DISCLAIMER)

    return "\n".join(lines)


def format_scan_result(token: dict, safety_result: dict) -> str:
    """
    Format result for the /scan <address> command — same structure as alert
    but with a slightly different header.
    """
    msg = format_alert(token, safety_result)
    msg = msg.replace("New Token Alert", "On-Demand Scan Result", 1)
    return msg


def format_not_found(contract_address: str) -> str:
    return (
        f"❌ Token <code>{contract_address}</code> not found on DexScreener "
        f"(Robinhood Chain). Double-check the contract address."
    )


# ---------------------------------------------------------------------------
# Inline keyboard builder
# ---------------------------------------------------------------------------

def build_inline_keyboard(token: dict) -> dict:
    """
    Build the Telegram inline keyboard with URL buttons:
      - View on DexScreener
      - View on GMGN
      - Buy on Maestro (deep-link with contract address)
    """
    maestro_url = MAESTRO_DEEP_LINK_TEMPLATE.format(
        bot_username=MAESTRO_BOT_USERNAME,
        contract_address=token["contract_address"],
    )
    gmgn_url = token.get("gmgn_url") or f"https://gmgn.ai/rbn/token/{token['contract_address']}"

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 DexScreener",
                    "url": token.get("dexscreener_url", "https://dexscreener.com"),
                },
                {
                    "text": "🟢 GMGN",
                    "url": gmgn_url,
                },
            ],
            [
                {
                    "text": "🤖 Buy on Maestro",
                    "url": maestro_url,
                },
            ],
        ]
    }


# ---------------------------------------------------------------------------
# Bot command menu registration
# ---------------------------------------------------------------------------

def answer_callback_query(callback_query_id, text=""):
    """Dismiss the loading spinner when an inline button is tapped."""
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=5)
    except Exception:
        pass


def send_watch_alert(token: dict, chat_id: str = None) -> bool:
    """
    Send a raw launch notification (no safety checks) for watch mode.
    Only called for tokens that passed the MC < $10k + social profile filter.
    """
    if not TELEGRAM_BOT_TOKEN:
        return False

    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        logger.error("send_watch_alert: no chat_id provided and TELEGRAM_CHAT_ID not set")
        return False

    import datetime
    created_dt = ""
    if token.get("created_at_ms"):
        dt = datetime.datetime.utcfromtimestamp(token["created_at_ms"] / 1000)
        created_dt = dt.strftime("%Y-%m-%d %H:%M UTC")

    market_cap = f"${token['market_cap_usd']:,.0f}" if token.get("market_cap_usd") else "N/A"
    liquidity = f"${token['liquidity_usd']:,.0f}" if token.get("liquidity_usd") else "N/A"

    socials = token.get("socials") or {}
    social_parts = []
    if socials.get("website"):
        social_parts.append(f'<a href="{socials["website"]}">🌐 Website</a>')
    if socials.get("twitter"):
        social_parts.append(f'<a href="{socials["twitter"]}">𝕏 Twitter</a>')
    if socials.get("telegram"):
        social_parts.append(f'<a href="{socials["telegram"]}">✈️ Telegram</a>')
    social_line = "  |  ".join(social_parts) if social_parts else "None"

    lines = [
        "🆕 <b>New Launch Detected</b>",
        "",
        f"<b>{token['name']}</b> (<code>{token['symbol']}</code>)",
        f"<b>CA:</b> <code>{token['contract_address']}</code>",
        "",
        f"<b>Market Cap:</b> {market_cap}",
        f"<b>Liquidity:</b> {liquidity}",
        f"<b>Listed:</b> {created_dt or 'Unknown'}",
        f"<b>Socials:</b> {social_line}",
        "",
        "<i>⚠️ No safety checks run — always verify before trading.</i>",
    ]

    contract = token["contract_address"]
    dex_url = token.get("dexscreener_url", f"https://dexscreener.com/rbn/{contract}")
    gmgn_url = token.get("gmgn_url", f"https://gmgn.ai/rbn/token/{contract}")

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 DexScreener", "url": dex_url},
                {"text": "🟢 GMGN",        "url": gmgn_url},
            ]
        ]
    }

    return send_text(target, "\n".join(lines), keyboard=keyboard)


def set_bot_commands() -> bool:
    """Register the bot command menu shown in Telegram's UI."""
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
    commands = [
        {"command": "start",         "description": "Enable safety-checked alerts (owner only)"},
        {"command": "stop",          "description": "Pause safety-checked alerts (owner only)"},
        {"command": "watch",         "description": "Enable raw launch notifications"},
        {"command": "unwatch",       "description": "Pause raw launch notifications"},
        {"command": "watchfilter",   "description": "Toggle MC range + social filter on/off"},
        {"command": "setmc",         "description": "Set MC range for filter: /setmc 1000 50000"},
        {"command": "addchannel",    "description": "Add this chat to alert broadcast list"},
        {"command": "removechannel", "description": "Remove this chat from broadcast list"},
        {"command": "status",        "description": "Show full status"},
        {"command": "scan",          "description": "Safety check any contract: /scan 0x..."},
        {"command": "test",          "description": "Confirm bot can send messages here"},
        {"command": "diag",          "description": "Check token sources and Redis state"},
        {"command": "help",          "description": "Show all commands"},
    ]
    try:
        import json
        resp = requests.post(url, json={"commands": commands}, timeout=10)
        return resp.ok
    except Exception as exc:
        logger.warning("setMyCommands failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Send functions
# ---------------------------------------------------------------------------

def send_alert(token: dict, safety_result: dict, chat_id: str = None) -> bool:
    """
    Send an alert message to the configured Telegram channel (or a specific
    chat_id if provided, e.g. for /scan replies).

    Returns True on success, False on failure.
    """
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat:
        logger.error("TELEGRAM_CHAT_ID not configured")
        return False

    text = format_alert(token, safety_result)
    keyboard = build_inline_keyboard(token)

    return _send_message(target_chat, text, reply_markup=keyboard)


def send_scan_result(chat_id: str, token: dict, safety_result: dict) -> bool:
    """Send an on-demand /scan result back to the user."""
    text = format_scan_result(token, safety_result)
    keyboard = build_inline_keyboard(token)
    return _send_message(chat_id, text, reply_markup=keyboard)


def send_text(chat_id: str, text: str, keyboard: dict = None) -> bool:
    """Send a plain text (HTML-formatted) message to a chat, with optional inline keyboard."""
    return _send_message(chat_id, text, reply_markup=keyboard)


def _send_message(chat_id: str, text: str, reply_markup: dict = None) -> bool:
    """
    Call Telegram sendMessage API. Automatically splits messages longer than
    4000 characters across multiple sends (Telegram's limit is 4096).
    Returns True on success, False on any failure.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        return False

    # Split long messages at newline boundaries to stay under 4000 chars
    MAX_LEN = 4000
    chunks = []
    if len(text) <= MAX_LEN:
        chunks = [text]
    else:
        lines = text.split("\n")
        current = ""
        for line in lines:
            addition = (line + "\n")
            if len(current) + len(addition) > MAX_LEN:
                if current:
                    chunks.append(current.rstrip())
                current = addition
            else:
                current += addition
        if current.strip():
            chunks.append(current.rstrip())

    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    success = True
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # Only attach keyboard to the last chunk
        if reply_markup and i == len(chunks) - 1:
            import json
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                logger.error(
                    "Telegram sendMessage failed: %s — %s", resp.status_code, resp.text
                )
                success = False
        except requests.RequestException as exc:
            logger.error("Telegram sendMessage request failed: %s", exc)
            success = False
    return success
