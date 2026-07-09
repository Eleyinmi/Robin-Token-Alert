"""
Telegram message formatting, inline keyboard construction, and send functions.
Uses the Bot API directly (no python-telegram-bot library needed for sending —
we only use it for the webhook handler in bot.py).
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
    # Replace the header line to indicate this is an on-demand scan
    msg = msg.replace(
        "New Token Alert",
        "On-Demand Scan Result",
        1,
    )
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
    Build the Telegram inline keyboard with two URL buttons:
      - View on DexScreener
      - Buy on Maestro (deep-link with contract address)
    """
    maestro_url = MAESTRO_DEEP_LINK_TEMPLATE.format(
        bot_username=MAESTRO_BOT_USERNAME,
        contract_address=token["contract_address"],
    )

    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 View on DexScreener",
                    "url": token.get("dexscreener_url", "https://dexscreener.com"),
                },
                {
                    "text": "🤖 Buy on Maestro",
                    "url": maestro_url,
                },
            ]
        ]
    }


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


def send_text(chat_id: str, text: str) -> bool:
    """Send a plain text (HTML-formatted) message to a chat."""
    return _send_message(chat_id, text)


def _send_message(chat_id: str, text: str, reply_markup: dict = None) -> bool:
    """
    Call Telegram sendMessage API.
    Returns True on success, False on any failure.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            logger.error(
                "Telegram sendMessage failed: %s — %s", resp.status_code, resp.text
            )
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Telegram sendMessage request failed: %s", exc)
        return False
