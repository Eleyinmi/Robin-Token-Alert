"""
DexScreener public API wrapper.
Fetches newly created token pairs on Robinhood Chain (chain ID: rbn).
No API key required.
"""

import logging
import time
from typing import Optional
import requests

logger = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com"

# Robinhood Chain identifier used by DexScreener
ROBINHOOD_CHAIN_ID = "rbn"

# How many seconds old a pair can be and still count as "new".
# 1 minute per scan run + a small buffer to avoid missing tokens on edge of window.
NEW_PAIR_MAX_AGE_SECONDS = 90


def get_new_pairs(since_timestamp: Optional[int] = None) -> list[dict]:
    """
    Fetch recently created token pairs on Robinhood Chain from DexScreener.

    Args:
        since_timestamp: Unix timestamp (ms). Only pairs created after this
                         time are returned. If None, uses NEW_PAIR_MAX_AGE_SECONDS
                         to define "new".

    Returns:
        List of normalised token dicts, one per new pair found.
    """
    try:
        url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("DexScreener /token-profiles/latest failed: %s", exc)
        return []

    now_ms = int(time.time() * 1000)
    cutoff_ms = (
        since_timestamp
        if since_timestamp is not None
        else now_ms - NEW_PAIR_MAX_AGE_SECONDS * 1000
    )

    new_tokens = []

    # data is a list of token profile objects
    for item in (data if isinstance(data, list) else []):
        if item.get("chainId") != ROBINHOOD_CHAIN_ID:
            continue

        token_address = item.get("tokenAddress", "")
        if not token_address:
            continue

        # Enrich with pair data to get liquidity, market cap, etc.
        pair_info = _get_pair_info(token_address)
        if pair_info is None:
            continue

        created_at_ms = pair_info.get("pairCreatedAt", 0) or 0
        if created_at_ms < cutoff_ms:
            continue

        new_tokens.append(_normalise(token_address, pair_info))

    logger.info(
        "DexScreener: found %d new pair(s) on %s since %d",
        len(new_tokens),
        ROBINHOOD_CHAIN_ID,
        cutoff_ms,
    )
    return new_tokens


def get_token_info(contract_address: str) -> Optional[dict]:
    """
    Fetch token/pair data for a specific contract address (used by /scan command).
    Tries Robinhood Chain first, then falls back to searching all chains on DexScreener.

    Returns a normalised dict or None if the token isn't found anywhere on DexScreener.
    """
    # Try rbn chain first
    pair_info = _get_pair_info(contract_address)
    if pair_info is not None:
        return _normalise(contract_address, pair_info)

    # Fallback: search across all chains
    logger.info("Token %s not found on rbn — searching all chains", contract_address)
    pair_info = _search_all_chains(contract_address)
    if pair_info is not None:
        return _normalise(contract_address, pair_info)

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_pair_info(contract_address: str) -> Optional[dict]:
    """
    Call DexScreener /tokens/{chainId}/{tokenAddress} and return the best pair,
    or None if no pairs are found or the request fails.
    """
    try:
        url = f"{DEXSCREENER_BASE}/tokens/{ROBINHOOD_CHAIN_ID}/{contract_address}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error(
            "DexScreener token lookup failed for %s: %s", contract_address, exc
        )
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return None

    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)
    return pairs[0]


def _search_all_chains(contract_address: str) -> Optional[dict]:
    """
    Search DexScreener across all chains for a token address.
    Used as fallback when the token isn't found on the rbn chain.
    """
    try:
        url = f"{DEXSCREENER_BASE}/latest/dex/tokens/{contract_address}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("DexScreener all-chain search failed for %s: %s", contract_address, exc)
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return None

    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)
    return pairs[0]


def _normalise(token_address: str, pair_info: dict) -> dict:
    """Build a standard token dict from a DexScreener pair object."""
    chain = pair_info.get("chainId", ROBINHOOD_CHAIN_ID)
    return {
        "contract_address": token_address.lower(),
        "pair_address": pair_info.get("pairAddress", ""),
        "symbol": pair_info.get("baseToken", {}).get("symbol", "UNKNOWN"),
        "name": pair_info.get("baseToken", {}).get("name", "Unknown Token"),
        "created_at_ms": pair_info.get("pairCreatedAt", 0) or 0,
        "liquidity_usd": float(pair_info.get("liquidity", {}).get("usd", 0) or 0),
        "market_cap_usd": float(pair_info.get("marketCap", 0) or 0),
        "dexscreener_url": pair_info.get("url", _build_fallback_url(chain, token_address)),
        "chain_id": chain,
    }


def _build_fallback_url(chain: str, contract_address: str) -> str:
    return f"https://dexscreener.com/{chain}/{contract_address}"
