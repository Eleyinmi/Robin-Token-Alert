import os
import logging
import requests

logger = logging.getLogger(__name__)

REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def _headers():
    return {"Authorization": "Bearer " + REDIS_REST_TOKEN}


def _call(command):
    if not REDIS_REST_URL or not REDIS_REST_TOKEN:
        raise RuntimeError("UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not set")
    url = REDIS_REST_URL + "/" + "/".join(str(c) for c in command)
    resp = requests.get(url, headers=_headers(), timeout=5)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Safety scan alerts (filtered)
# ---------------------------------------------------------------------------

def is_scanning_enabled():
    try:
        result = _call(["GET", "scanning_enabled"])
        value = result.get("result")
        if value is None:
            return True
        return value.lower() == "true"
    except Exception as exc:
        logger.error("Redis GET scanning_enabled failed: %s", exc)
        return True


def set_scanning_enabled(enabled):
    try:
        _call(["SET", "scanning_enabled", "true" if enabled else "false"])
    except Exception as exc:
        logger.error("Redis SET scanning_enabled failed: %s", exc)
        raise


def has_been_alerted(contract_address):
    try:
        result = _call(["SISMEMBER", "alerted_tokens", contract_address.lower()])
        return result.get("result") == 1
    except Exception as exc:
        logger.error("Redis SISMEMBER failed for %s: %s", contract_address, exc)
        return True


def mark_alerted(contract_address):
    try:
        _call(["SADD", "alerted_tokens", contract_address.lower()])
    except Exception as exc:
        logger.error("Redis SADD failed for %s: %s", contract_address, exc)
        raise


def get_alerted_count():
    try:
        result = _call(["SCARD", "alerted_tokens"])
        return result.get("result", 0)
    except Exception as exc:
        logger.error("Redis SCARD failed: %s", exc)
        return -1


# ---------------------------------------------------------------------------
# Watch mode (raw launch notifications)
# ---------------------------------------------------------------------------

def is_watch_enabled():
    try:
        result = _call(["GET", "watch_enabled"])
        value = result.get("result")
        if value is None:
            return False
        return value.lower() == "true"
    except Exception as exc:
        logger.error("Redis GET watch_enabled failed: %s", exc)
        return False


def set_watch_enabled(enabled):
    try:
        _call(["SET", "watch_enabled", "true" if enabled else "false"])
    except Exception as exc:
        logger.error("Redis SET watch_enabled failed: %s", exc)
        raise


def is_watch_filter_enabled():
    """When True, watch alerts only fire for tokens with MC in range AND social profile."""
    try:
        result = _call(["GET", "watch_filter_enabled"])
        value = result.get("result")
        if value is None:
            return False
        return value.lower() == "true"
    except Exception as exc:
        logger.error("Redis GET watch_filter_enabled failed: %s", exc)
        return False


def set_watch_filter_enabled(enabled):
    try:
        _call(["SET", "watch_filter_enabled", "true" if enabled else "false"])
    except Exception as exc:
        logger.error("Redis SET watch_filter_enabled failed: %s", exc)
        raise


def get_watch_mc_range() -> tuple[float, float]:
    """Return (min_mc, max_mc) for the watch filter. Defaults: 0 to 10,000."""
    try:
        min_result = _call(["GET", "watch_mc_min"])
        max_result = _call(["GET", "watch_mc_max"])
        mc_min = float(min_result.get("result") or 0)
        mc_max = float(max_result.get("result") or 20_000)
        return mc_min, mc_max
    except Exception as exc:
        logger.error("Redis GET watch_mc_range failed: %s", exc)
        return 0.0, 20_000.0


def set_watch_mc_range(mc_min: float, mc_max: float):
    try:
        _call(["SET", "watch_mc_min", str(mc_min)])
        _call(["SET", "watch_mc_max", str(mc_max)])
    except Exception as exc:
        logger.error("Redis SET watch_mc_range failed: %s", exc)
        raise


def has_been_watch_alerted(contract_address):
    try:
        result = _call(["SISMEMBER", "watch_alerted_tokens", contract_address.lower()])
        return result.get("result") == 1
    except Exception as exc:
        logger.error("Redis SISMEMBER watch failed for %s: %s", contract_address, exc)
        return True


def mark_watch_alerted(contract_address):
    try:
        _call(["SADD", "watch_alerted_tokens", contract_address.lower()])
    except Exception as exc:
        logger.error("Redis SADD watch failed for %s: %s", contract_address, exc)
        raise


# ---------------------------------------------------------------------------
# Broadcast channels (multiple Telegram chats/channels)
# ---------------------------------------------------------------------------

def get_broadcast_channels() -> list[str]:
    """Return all channel IDs configured to receive alerts."""
    try:
        result = _call(["SMEMBERS", "broadcast_channels"])
        members = result.get("result") or []
        return [str(m) for m in members]
    except Exception as exc:
        logger.error("Redis SMEMBERS broadcast_channels failed: %s", exc)
        return []


def add_broadcast_channel(chat_id: str):
    try:
        _call(["SADD", "broadcast_channels", str(chat_id)])
    except Exception as exc:
        logger.error("Redis SADD broadcast_channels failed: %s", exc)
        raise


def remove_broadcast_channel(chat_id: str):
    try:
        _call(["SREM", "broadcast_channels", str(chat_id)])
    except Exception as exc:
        logger.error("Redis SREM broadcast_channels failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Telegram update offset
# ---------------------------------------------------------------------------

def get_update_offset():
    try:
        result = _call(["GET", "telegram_offset"])
        value = result.get("result")
        return int(value) if value is not None else 0
    except Exception as exc:
        logger.error("Redis GET telegram_offset failed: %s", exc)
        return 0


def set_update_offset(offset):
    try:
        _call(["SET", "telegram_offset", str(offset)])
    except Exception as exc:
        logger.error("Redis SET telegram_offset failed: %s", exc)
