"""
GMGN.ai API wrapper for new token discovery on Robinhood Chain.
Used as the primary source for new token pairs; DexScreener is the fallback.
"""

import logging
import time
from typing import Optional
import requests

logger = logging.getLogger(__name__)

GMGN_BASE = "https://gmgn.ai"

# Chain identifier used by GMGN for Robinhood Chain
GMGN_CHAIN = "rbn"

# How long a token can be before it's not "new" (seconds)
NEW_PAIR_MAX_AGE_SECONDS = 150


def get_gmgn_url(contract_address: str) -> str:
    """Return the GMGN.ai token page URL."""
    return f"{GMGN_BASE}/{GMGN_CHAIN}/token/{contract_address}"


def get_new_pairs() -> list[dict]:
    """
    Fetch recently created token pairs from GMGN.ai on Robinhood Chain.
    Returns a list of normalised token dicts (same shape as dexscreener.get_new_pairs).
    Returns an empty list on any error so the caller can fall back to DexScreener.
    """
    try:
        url = f"{GMGN_BASE}/defi/quotation/v1/pairs/{GMGN_CHAIN}/new_pairs"
        params = {
            "limit": 100,
            "orderby": "open_timestamp",
            "direction": "desc",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("GMGN new_pairs request failed: %s", exc)
        return []
    except Exception as exc:
        logger.warning("GMGN response parse error: %s", exc)
        return []

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - NEW_PAIR_MAX_AGE_SECONDS * 1000

    pairs = data.get("data", {}).get("pairs") or []
    if not pairs:
        # Try alternate response shape
        pairs = data.get("pairs") or (data if isinstance(data, list) else [])

    new_tokens = []
    for pair in pairs:
        try:
            created_at_ms = int(pair.get("open_timestamp", 0) or 0) * 1000
            if created_at_ms > 0 and created_at_ms < cutoff_ms:
                continue

            base = pair.get("base_token") or pair.get("baseToken") or {}
            token_address = (base.get("address") or pair.get("base_address") or "").lower()
            if not token_address or not token_address.startswith("0x"):
                continue

            liquidity = float(pair.get("liquidity") or pair.get("liquidity_usd") or 0)
            market_cap = float(pair.get("market_cap") or pair.get("fdv") or 0)

            # Social links from GMGN
            info = pair.get("token_info") or pair.get("info") or {}
            socials = _extract_socials_from_info(info)

            new_tokens.append({
                "contract_address": token_address,
                "pair_address": pair.get("address") or pair.get("pair_address") or "",
                "symbol": base.get("symbol") or pair.get("base_symbol") or "UNKNOWN",
                "name": base.get("name") or pair.get("base_name") or "Unknown Token",
                "created_at_ms": created_at_ms,
                "liquidity_usd": liquidity,
                "market_cap_usd": market_cap,
                "dexscreener_url": f"https://dexscreener.com/rbn/{token_address}",
                "gmgn_url": get_gmgn_url(token_address),
                "chain_id": GMGN_CHAIN,
                "socials": socials,
            })
        except Exception as exc:
            logger.debug("GMGN pair parse error: %s", exc)
            continue

    logger.info("GMGN: found %d new pair(s) on %s", len(new_tokens), GMGN_CHAIN)
    return new_tokens


def _extract_socials_from_info(info: dict) -> dict:
    """Extract website and twitter from a GMGN token info object."""
    socials = {}
    if not info:
        return socials

    website = info.get("website") or info.get("websites", [None])[0] if info.get("websites") else None
    twitter = info.get("twitter") or info.get("twitter_username")
    telegram = info.get("telegram")

    if website:
        socials["website"] = website
    if twitter:
        socials["twitter"] = twitter if twitter.startswith("http") else f"https://x.com/{twitter.lstrip('@')}"
    if telegram:
        socials["telegram"] = telegram if telegram.startswith("http") else f"https://t.me/{telegram.lstrip('@')}"

    return socials
