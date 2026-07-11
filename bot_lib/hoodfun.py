"""
Hood.fun and NOXA Fun launchpad API wrappers.
These are the native Robinhood Chain launchpads (like pump.fun on Solana).
Tokens launch here BEFORE appearing on DexScreener or GMGN.
"""

import logging
import time
from typing import Optional
import requests

logger = logging.getLogger(__name__)

NEW_PAIR_MAX_AGE_SECONDS = 90

SOURCES = [
    {
        "name": "hood.fun",
        "base": "https://hood.fun",
        "endpoints": [
            "/api/tokens?sort=createTime&order=desc&limit=50",
            "/api/coins?sort=created&order=desc&limit=50",
            "/api/v1/tokens/latest?limit=50",
            "/api/tokens/new?limit=50",
        ],
        "chain": "rbn",
    },
    {
        "name": "noxa.fun",
        "base": "https://noxa.fun",
        "endpoints": [
            "/api/tokens?sort=createTime&order=desc&limit=50",
            "/api/coins?sort=created&order=desc&limit=50",
            "/api/v1/tokens/latest?limit=50",
            "/api/tokens/new?limit=50",
        ],
        "chain": "rbn",
    },
]


def get_new_pairs() -> list[dict]:
    """
    Fetch new token launches from hood.fun and noxa.fun.
    Returns a merged, deduplicated list of normalised token dicts.
    """
    results: list[dict] = []
    seen: set[str] = set()

    for source in SOURCES:
        tokens = _fetch_from_source(source)
        for token in tokens:
            addr = token.get("contract_address", "")
            if addr and addr not in seen:
                seen.add(addr)
                results.append(token)

    logger.info("Launchpad: found %d new token(s) from hood.fun/noxa.fun", len(results))
    return results


def _fetch_from_source(source: dict) -> list[dict]:
    """Try each endpoint for a source until one works, then parse the response."""
    name = source["name"]
    base = source["base"]
    chain = source["chain"]
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; RobinTokenAlert/1.0)",
        "Accept": "application/json",
    }

    for endpoint in source["endpoints"]:
        url = base + endpoint
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()

            # Try to parse JSON
            data = resp.json()
            tokens = _parse_response(data, chain, base)
            if tokens is not None:
                logger.info("%s: endpoint %s returned %d tokens", name, endpoint, len(tokens))
                return tokens

        except requests.RequestException as exc:
            logger.debug("%s %s failed: %s", name, endpoint, exc)
        except Exception as exc:
            logger.debug("%s %s parse error: %s", name, endpoint, exc)

    logger.info("%s: no working endpoint found", name)
    return []


def _parse_response(data, chain: str, base_url: str) -> Optional[list[dict]]:
    """
    Try to extract a list of tokens from various response shapes.
    Returns None if the data shape is unrecognized.
    """
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - NEW_PAIR_MAX_AGE_SECONDS * 1000

    # Unwrap common wrapper shapes
    items = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("tokens", "coins", "data", "result", "results", "items"):
            if isinstance(data.get(key), list):
                items = data[key]
                break

    if items is None:
        return None

    tokens = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            token = _normalise_item(item, chain, base_url, cutoff_ms)
            if token:
                tokens.append(token)
        except Exception:
            continue

    return tokens


def _normalise_item(item: dict, chain: str, base_url: str, cutoff_ms: int) -> Optional[dict]:
    """Normalise a single launchpad token item to our standard format."""

    # Contract address — try multiple field names
    contract = (
        item.get("contractAddress")
        or item.get("contract_address")
        or item.get("address")
        or item.get("tokenAddress")
        or item.get("mint")
        or ""
    ).lower()

    if not contract or not contract.startswith("0x"):
        return None

    # Created timestamp
    created_at_ms = 0
    for ts_field in ("createTime", "created_at", "createdAt", "open_timestamp", "timestamp", "created"):
        val = item.get(ts_field)
        if val:
            val = int(val)
            # Seconds vs milliseconds
            created_at_ms = val * 1000 if val < 1e12 else val
            break

    if created_at_ms > 0 and created_at_ms < cutoff_ms:
        return None  # Too old

    # Basic token info
    symbol = (
        item.get("symbol") or item.get("ticker") or "UNKNOWN"
    ).upper()
    name = item.get("name") or item.get("tokenName") or symbol

    liquidity = float(item.get("liquidity") or item.get("liquidity_usd") or 0)
    market_cap = float(
        item.get("marketCap") or item.get("market_cap") or item.get("fdv") or 0
    )

    # Social links
    socials: dict = {}
    website = item.get("website") or item.get("websiteUrl") or item.get("website_url")
    twitter = item.get("twitter") or item.get("twitterUrl") or item.get("twitter_url")
    tg = item.get("telegram") or item.get("telegramUrl") or item.get("telegram_url")

    if website:
        socials["website"] = website
    if twitter:
        socials["twitter"] = twitter if twitter.startswith("http") else f"https://x.com/{twitter.lstrip('@')}"
    if tg:
        socials["telegram"] = tg if tg.startswith("http") else f"https://t.me/{tg.lstrip('@')}"

    # Also try nested info/links
    info = item.get("info") or item.get("metadata") or {}
    if isinstance(info, dict):
        if not socials.get("website") and info.get("website"):
            socials["website"] = info["website"]
        if not socials.get("twitter") and info.get("twitter"):
            socials["twitter"] = info["twitter"]

    dex_url = f"https://dexscreener.com/{chain}/{contract}"
    gmgn_url = f"https://gmgn.ai/{chain}/token/{contract}"

    # Try to build a launchpad URL
    source_url = (
        item.get("url")
        or item.get("launchUrl")
        or f"{base_url}/token/{contract}"
    )

    return {
        "contract_address": contract,
        "pair_address": item.get("pairAddress") or item.get("pair_address") or "",
        "symbol": symbol,
        "name": name,
        "created_at_ms": created_at_ms,
        "liquidity_usd": liquidity,
        "market_cap_usd": market_cap,
        "dexscreener_url": dex_url,
        "gmgn_url": gmgn_url,
        "source_url": source_url,
        "chain_id": chain,
        "socials": socials,
    }
