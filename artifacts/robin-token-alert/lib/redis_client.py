"""
Upstash Redis client using the REST API (no persistent connection needed for
serverless functions). Manages two keys:
  - "alerted_tokens"   : a Redis SET of contract addresses we've already alerted on
  - "scanning_enabled" : a plain string key, "true" or "false"
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {REDIS_REST_TOKEN}"}


def _call(command: list) -> dict:
    """
    Execute a Redis command via the Upstash REST API.
    Raises RuntimeError on network/auth failures so callers can handle it.
    """
    if not REDIS_REST_URL or not REDIS_REST_TOKEN:
        raise RuntimeError(
            "UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not set"
        )

    url = f"{REDIS_REST_URL}/{'/'.join(str(c) for c in command)}"
    resp = requests.get(url, headers=_headers(), timeout=5)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# scanning_enabled helpers
# ---------------------------------------------------------------------------

def is_scanning_enabled() -> bool:
    """
    Return True if scanning is enabled (default: True when key is absent).
    This is checked at the very start of every /api/scan run.
    If the key doesn't exist yet we treat it as enabled.
    """
    try:
        result = _call(["GET", "scanning_enabled"])
        value = result.get("result")
        # If the key has never been set, default to enabled
        if value is None:
            return True
        return value.lower() == "true"
    except Exception as exc:
        logger.error("Redis GET scanning_enabled failed: %s", exc)
        # Fail open — allow scanning if Redis is unreachable
        return True


def set_scanning_enabled(enabled: bool) -> None:
    """Set scanning_enabled to "true" or "false"."""
    try:
        _call(["SET", "scanning_enabled", "true" if enabled else "false"])
    except Exception as exc:
        logger.error("Redis SET scanning_enabled failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# alerted_tokens helpers
# ---------------------------------------------------------------------------

def has_been_alerted(contract_address: str) -> bool:
    """Return True if this contract address is already in alerted_tokens."""
    try:
        result = _call(["SISMEMBER", "alerted_tokens", contract_address.lower()])
        return result.get("result") == 1
    except Exception as exc:
        logger.error("Redis SISMEMBER failed for %s: %s", contract_address, exc)
        # Fail closed — skip alerting if we can't check, to avoid duplicates
        return True


def mark_alerted(contract_address: str) -> None:
    """Add contract address to alerted_tokens SET."""
    try:
        _call(["SADD", "alerted_tokens", contract_address.lower()])
    except Exception as exc:
        logger.error("Redis SADD failed for %s: %s", contract_address, exc)
        raise


def get_alerted_count() -> int:
    """Return the number of tokens in alerted_tokens (for /status)."""
    try:
        result = _call(["SCARD", "alerted_tokens"])
        return result.get("result", 0)
    except Exception as exc:
        logger.error("Redis SCARD failed: %s", exc)
        return -1
