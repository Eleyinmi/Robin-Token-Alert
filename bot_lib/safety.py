"""
Safety scanner module.
Runs honeypot simulation, liquidity check, liquidity lock check, and holder
concentration check for a given token. Returns a structured result dict.
"""

import os
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIN_LIQUIDITY_USD = float(os.environ.get("MIN_LIQUIDITY_USD", "3000"))
MAX_BUY_SELL_TAX_PCT = 10.0
MAX_TOP10_HOLDER_PCT = 60.0

HONEYPOT_BASE = "https://api.honeypot.is/v2"
GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"

# Robinhood Chain is Arbitrum-based; GoPlus uses chain ID 42161 for Arbitrum
# but RBN may have its own chain ID — update GOPLUS_CHAIN_ID if needed.
GOPLUS_CHAIN_ID = os.environ.get("GOPLUS_CHAIN_ID", "42161")

# Safety statuses
STATUS_PASS = "PASS"
STATUS_CAUTION = "CAUTION"
STATUS_FAIL = "FAIL"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_safety_checks(token: dict) -> dict:
    """
    Run all safety checks on a token dict (as returned by dexscreener.py).

    Returns a results dict:
    {
        "safety_status": "PASS" | "CAUTION" | "FAIL",
        "checks": {
            "honeypot":       {"status": "pass"|"fail"|"caution"|"unavailable", "detail": str},
            "liquidity":      {"status": ..., "detail": str},
            "liquidity_lock": {"status": ..., "detail": str},
            "holder_conc":    {"status": ..., "detail": str},
        },
        "failed_checks": [list of check names that are "fail" or "caution"],
    }
    """
    contract = token["contract_address"]
    checks = {}

    # Run checks — each is wrapped so a failure in one doesn't crash others
    checks["honeypot"] = _check_honeypot(contract)
    checks["liquidity"] = _check_liquidity(token["liquidity_usd"])
    checks["liquidity_lock"] = _check_liquidity_lock(contract)
    checks["holder_conc"] = _check_holder_concentration(contract)

    # Determine overall status
    # Any "fail" → FAIL (no alert sent at all)
    # Any "caution" → CAUTION (alert sent, flags listed)
    # All "pass" or "unavailable" → PASS
    statuses = [c["status"] for c in checks.values()]

    if "fail" in statuses:
        overall = STATUS_FAIL
    elif "caution" in statuses:
        overall = STATUS_CAUTION
    else:
        overall = STATUS_PASS

    failed_checks = [
        name for name, c in checks.items() if c["status"] in ("fail", "caution")
    ]

    return {
        "safety_status": overall,
        "checks": checks,
        "failed_checks": failed_checks,
    }


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

def _check_honeypot(contract_address: str) -> dict:
    """
    Simulate buy then sell using Honeypot.is API.
    Falls back to GoPlus if Honeypot.is is rate-limited or unavailable.
    """
    result = _honeypot_is(contract_address)
    if result is not None:
        return result

    # Fallback to GoPlus
    result = _goplus_honeypot(contract_address)
    if result is not None:
        return result

    return {"status": "unavailable", "detail": "Honeypot check unavailable (API error)"}


def _honeypot_is(contract_address: str) -> Optional[dict]:
    """Call Honeypot.is and return a check result dict, or None on rate-limit/error."""
    try:
        url = f"{HONEYPOT_BASE}/IsHoneypot"
        resp = requests.get(
            url,
            params={"address": contract_address},
            timeout=8,
        )

        # 429 = rate limited — caller will try GoPlus instead
        if resp.status_code == 429:
            logger.warning("Honeypot.is rate-limited for %s — will try GoPlus", contract_address)
            return None

        resp.raise_for_status()
        data = resp.json()

        honeypot_result = data.get("honeypotResult", {})
        simulation = data.get("simulationResult", {})

        is_honeypot = honeypot_result.get("isHoneypot", False)
        if is_honeypot:
            reason = honeypot_result.get("honeypotReason", "Sell simulation failed")
            return {"status": "fail", "detail": f"HONEYPOT detected: {reason}"}

        buy_tax = float(simulation.get("buyTax", 0) or 0)
        sell_tax = float(simulation.get("sellTax", 0) or 0)

        if sell_tax > MAX_BUY_SELL_TAX_PCT or buy_tax > MAX_BUY_SELL_TAX_PCT:
            return {
                "status": "caution",
                "detail": f"High tax — buy: {buy_tax:.1f}%, sell: {sell_tax:.1f}%",
            }

        return {
            "status": "pass",
            "detail": f"No honeypot. Buy tax: {buy_tax:.1f}%, sell tax: {sell_tax:.1f}%",
        }

    except requests.RequestException as exc:
        logger.error("Honeypot.is request failed for %s: %s", contract_address, exc)
        return None


def _goplus_honeypot(contract_address: str) -> Optional[dict]:
    """Call GoPlus Security API as a fallback honeypot check."""
    try:
        url = f"{GOPLUS_BASE}/token_security/{GOPLUS_CHAIN_ID}"
        resp = requests.get(
            url,
            params={"contract_addresses": contract_address},
            timeout=10,
        )

        if resp.status_code == 429:
            logger.warning("GoPlus rate-limited for %s", contract_address)
            return None

        resp.raise_for_status()
        data = resp.json()
        result_data = (data.get("result") or {}).get(contract_address.lower(), {})

        if not result_data:
            return {"status": "unavailable", "detail": "GoPlus returned no data"}

        is_honeypot = str(result_data.get("is_honeypot", "0")) == "1"
        if is_honeypot:
            return {"status": "fail", "detail": "HONEYPOT detected (GoPlus)"}

        buy_tax = float(result_data.get("buy_tax", 0) or 0) * 100
        sell_tax = float(result_data.get("sell_tax", 0) or 0) * 100

        if sell_tax > MAX_BUY_SELL_TAX_PCT or buy_tax > MAX_BUY_SELL_TAX_PCT:
            return {
                "status": "caution",
                "detail": f"High tax (GoPlus) — buy: {buy_tax:.1f}%, sell: {sell_tax:.1f}%",
            }

        return {
            "status": "pass",
            "detail": f"No honeypot (GoPlus). Buy: {buy_tax:.1f}%, sell: {sell_tax:.1f}%",
        }

    except requests.RequestException as exc:
        logger.error("GoPlus request failed for %s: %s", contract_address, exc)
        return None


def _check_liquidity(liquidity_usd: float) -> dict:
    """Flag tokens below MIN_LIQUIDITY_USD as CAUTION."""
    if liquidity_usd < MIN_LIQUIDITY_USD:
        return {
            "status": "caution",
            "detail": f"Low liquidity: ${liquidity_usd:,.0f} (min ${MIN_LIQUIDITY_USD:,.0f})",
        }
    return {
        "status": "pass",
        "detail": f"Liquidity OK: ${liquidity_usd:,.0f}",
    }


def _check_liquidity_lock(contract_address: str) -> dict:
    """
    Check via GoPlus whether LP tokens are in a burn/lock address or still
    held by the deployer. Flags deployer-held LP as CAUTION.
    """
    try:
        url = f"{GOPLUS_BASE}/token_security/{GOPLUS_CHAIN_ID}"
        resp = requests.get(
            url,
            params={"contract_addresses": contract_address},
            timeout=10,
        )

        if resp.status_code == 429:
            logger.warning("GoPlus rate-limited (lp lock) for %s", contract_address)
            return {"status": "unavailable", "detail": "LP lock check unavailable (rate limited)"}

        resp.raise_for_status()
        data = resp.json()
        result_data = (data.get("result") or {}).get(contract_address.lower(), {})

        if not result_data:
            return {"status": "unavailable", "detail": "LP lock data unavailable"}

        lp_holders = result_data.get("lp_holders") or []
        locked_pct = 0.0
        for holder in lp_holders:
            if holder.get("is_locked") or holder.get("tag") in ("Burn", "Dead"):
                locked_pct += float(holder.get("percent", 0) or 0) * 100

        if locked_pct >= 80:
            return {"status": "pass", "detail": f"LP locked/burned: {locked_pct:.1f}%"}
        elif locked_pct >= 50:
            return {"status": "caution", "detail": f"LP partially locked: {locked_pct:.1f}%"}
        else:
            return {
                "status": "caution",
                "detail": f"LP not locked or low lock rate: {locked_pct:.1f}% locked",
            }

    except requests.RequestException as exc:
        logger.error("GoPlus LP lock check failed for %s: %s", contract_address, exc)
        return {"status": "unavailable", "detail": f"LP lock check failed: {exc}"}


def _check_holder_concentration(contract_address: str) -> dict:
    """
    Check if the top 10 holders control more than MAX_TOP10_HOLDER_PCT% of supply.
    Uses GoPlus. If data is unavailable for this chain, marks as 'unavailable'.
    """
    try:
        url = f"{GOPLUS_BASE}/token_security/{GOPLUS_CHAIN_ID}"
        resp = requests.get(
            url,
            params={"contract_addresses": contract_address},
            timeout=10,
        )

        if resp.status_code == 429:
            logger.warning("GoPlus rate-limited (holders) for %s", contract_address)
            return {"status": "unavailable", "detail": "Holder check unavailable (rate limited)"}

        resp.raise_for_status()
        data = resp.json()
        result_data = (data.get("result") or {}).get(contract_address.lower(), {})

        if not result_data:
            return {"status": "unavailable", "detail": "Holder data unavailable for this chain"}

        holders = result_data.get("holders") or []
        if not holders:
            return {"status": "unavailable", "detail": "Holder list not available for this chain"}

        top10 = sorted(
            holders,
            key=lambda h: float(h.get("percent", 0) or 0),
            reverse=True,
        )[:10]

        top10_pct = sum(float(h.get("percent", 0) or 0) * 100 for h in top10)

        if top10_pct > MAX_TOP10_HOLDER_PCT:
            return {
                "status": "caution",
                "detail": f"Top 10 holders control {top10_pct:.1f}% of supply",
            }
        return {
            "status": "pass",
            "detail": f"Top 10 holders: {top10_pct:.1f}% of supply",
        }

    except requests.RequestException as exc:
        logger.error("GoPlus holder check failed for %s: %s", contract_address, exc)
        return {"status": "unavailable", "detail": f"Holder check failed: {exc}"}
