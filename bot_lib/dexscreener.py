"""
DexScreener public API wrapper.
Fetches newly created token pairs on Robinhood Chain (chain ID: rbn).
Used as fallback when GMGN is unavailable. No API key required.
"""

import logging
import time
from typing import Optional
import requests

logger = logging.getLogger(__name__)

DEXSCREENER_BASE = "https://api.dexscreener.com"
ROBINHOOD_CHAIN_ID = "robinhood"
NEW_PAIR_MAX_AGE_SECONDS = 150


def get_new_pairs(since_timestamp: Optional[int] = None) -> list[dict]:
    """
    Fetch recently created token pairs on Robinhood Chain from DexScreener.
    Also captures social links (website, twitter) from token profiles.
    """
    # Step 1: fetch token profiles — these contain social links
    try:
        profile_url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
        resp = requests.get(profile_url, timeout=10)
        resp.raise_for_status()
        profiles_raw = resp.json()
    except requests.RequestException as exc:
        logger.error("DexScreener /token-profiles/latest failed: %s", exc)
        return []

    # Build a lookup: tokenAddress -> socials
    socials_map: dict[str, dict] = {}
    rbn_addresses: list[str] = []

    for item in (profiles_raw if isinstance(profiles_raw, list) else []):
        if item.get("chainId") != ROBINHOOD_CHAIN_ID:
            continue
        addr = item.get("tokenAddress", "").lower()
        if not addr:
            continue
        rbn_addresses.append(addr)
        socials_map[addr] = _extract_socials(item.get("links") or [])

    now_ms = int(time.time() * 1000)
    cutoff_ms = (
        since_timestamp if since_timestamp is not None
        else now_ms - NEW_PAIR_MAX_AGE_SECONDS * 1000
    )

    new_tokens = []
    for token_address in rbn_addresses:
        pair_info = _get_pair_info(token_address)
        if pair_info is None:
            continue

        created_at_ms = pair_info.get("pairCreatedAt", 0) or 0
        if created_at_ms < cutoff_ms:
            continue

        token = _normalise(token_address, pair_info)
        token["socials"] = socials_map.get(token_address, {})
        new_tokens.append(token)

    logger.info("DexScreener: found %d new pair(s) on %s", len(new_tokens), ROBINHOOD_CHAIN_ID)
    return new_tokens


def get_token_info(contract_address: str) -> Optional[dict]:
    """
    Fetch token/pair data for a specific contract address (used by /scan).
    Tries Robinhood Chain first, then falls back to all chains.
    """
    pair_info = _get_pair_info(contract_address)
    if pair_info is not None:
        token = _normalise(contract_address, pair_info)
        # Try to get socials from token profile
        token["socials"] = _fetch_socials_for(contract_address)
        return token

    logger.info("Token %s not on rbn — searching all chains", contract_address)
    pair_info = _search_all_chains(contract_address)
    if pair_info is not None:
        token = _normalise(contract_address, pair_info)
        token["socials"] = {}
        return token

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_pair_info(contract_address: str) -> Optional[dict]:
    try:
        url = f"{DEXSCREENER_BASE}/tokens/{ROBINHOOD_CHAIN_ID}/{contract_address}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("DexScreener token lookup failed for %s: %s", contract_address, exc)
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return None

    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0), reverse=True)
    return pairs[0]


def _search_all_chains(contract_address: str) -> Optional[dict]:
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


def _fetch_socials_for(contract_address: str) -> dict:
    """Fetch social links for a single token from DexScreener token-profiles."""
    try:
        url = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        for item in (resp.json() if isinstance(resp.json(), list) else []):
            if item.get("tokenAddress", "").lower() == contract_address.lower():
                return _extract_socials(item.get("links") or [])
    except Exception:
        pass
    return {}


def _extract_socials(links: list) -> dict:
    """
    Parse DexScreener links array into a clean socials dict.
    Each link has: {"type": "twitter"|"website"|"telegram", "url": "..."}
    """
    socials = {}
    for link in links:
        link_type = (link.get("type") or "").lower()
        url = link.get("url") or link.get("href") or ""
        if not url:
            continue
        if link_type in ("twitter", "x"):
            socials["twitter"] = url
        elif link_type == "website":
            socials["website"] = url
        elif link_type == "telegram":
            socials["telegram"] = url
    return socials


def _normalise(token_address: str, pair_info: dict) -> dict:
    chain = pair_info.get("chainId", ROBINHOOD_CHAIN_ID)
    return {
        "contract_address": token_address.lower(),
        "pair_address": pair_info.get("pairAddress", ""),
        "symbol": pair_info.get("baseToken", {}).get("symbol", "UNKNOWN"),
        "name": pair_info.get("baseToken", {}).get("name", "Unknown Token"),
        "created_at_ms": pair_info.get("pairCreatedAt", 0) or 0,
        "liquidity_usd": float(pair_info.get("liquidity", {}).get("usd", 0) or 0),
        "market_cap_usd": float(pair_info.get("marketCap", 0) or 0),
        "dexscreener_url": pair_info.get("url", f"https://dexscreener.com/{chain}/{token_address}"),
        "gmgn_url": f"https://gmgn.ai/{chain}/token/{token_address}",
        "chain_id": chain,
        "socials": {},
    }


def _build_fallback_url(chain: str, contract_address: str) -> str:
    return f"https://dexscreener.com/{chain}/{contract_address}"
