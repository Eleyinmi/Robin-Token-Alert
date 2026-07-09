"""
/api/scan — Scheduled token discovery, safety check, and alert endpoint.
Called every 2 minutes by the GitHub Actions workflow.
Security: only processes requests that carry the correct SCAN_SECRET header.
"""

import os
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from bot_lib import redis_client, dexscreener, safety, telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SCAN_SECRET = os.environ.get("SCAN_SECRET", "")


@app.route("/api/scan", methods=["GET", "POST"])
def scan():
    # ----------------------------------------------------------------
    # 1. Authenticate — reject anything without the correct secret header
    # ----------------------------------------------------------------
    provided_secret = request.headers.get("X-Scan-Secret", "")
    if not SCAN_SECRET or provided_secret != SCAN_SECRET:
        logger.warning("Scan request rejected: invalid or missing X-Scan-Secret")
        return jsonify({"error": "Unauthorized"}), 401

    # ----------------------------------------------------------------
    # 2. Check if scanning is enabled (Redis toggle)
    # ----------------------------------------------------------------
    if not redis_client.is_scanning_enabled():
        logger.info("Scanning is disabled — exiting early")
        return jsonify({"status": "skipped", "reason": "scanning_disabled"}), 200

    # ----------------------------------------------------------------
    # 3. Discover new tokens on Robinhood Chain via DexScreener
    # ----------------------------------------------------------------
    logger.info("Fetching new pairs from DexScreener...")
    new_tokens = dexscreener.get_new_pairs()
    logger.info("Found %d new token(s)", len(new_tokens))

    if not new_tokens:
        return jsonify({"status": "ok", "new_tokens": 0, "alerted": 0}), 200

    alerted = 0
    skipped_duplicate = 0
    skipped_fail = 0
    skipped_error = 0

    # ----------------------------------------------------------------
    # 4. Process EVERY new token — don't assume there's only one
    # ----------------------------------------------------------------
    for token in new_tokens:
        contract = token["contract_address"]

        # Skip tokens we've already alerted on
        if redis_client.has_been_alerted(contract):
            logger.info("Skipping %s — already alerted", contract)
            skipped_duplicate += 1
            continue

        try:
            safety_result = safety.run_safety_checks(token)
        except Exception as exc:
            logger.error("Safety check crashed for %s: %s", contract, exc)
            skipped_error += 1
            continue

        logger.info(
            "Token %s (%s) — safety: %s",
            token["symbol"],
            contract,
            safety_result["safety_status"],
        )

        # FAIL tokens do not get alerted
        if safety_result["safety_status"] == "FAIL":
            logger.info("Skipping %s — FAIL status", contract)
            try:
                redis_client.mark_alerted(contract)
            except Exception as exc:
                logger.error("Failed to mark FAIL token: %s", exc)
            skipped_fail += 1
            continue

        # PASS or CAUTION — send the alert
        try:
            sent = telegram.send_alert(token, safety_result)
        except Exception as exc:
            logger.error("Telegram send failed for %s: %s", contract, exc)
            skipped_error += 1
            continue

        if sent:
            try:
                redis_client.mark_alerted(contract)
            except Exception as exc:
                logger.error("Failed to mark alerted %s: %s", contract, exc)
            alerted += 1
        else:
            logger.error("Alert not sent for %s", contract)
            skipped_error += 1

        time.sleep(0.5)

    summary = {
        "status": "ok",
        "new_tokens": len(new_tokens),
        "alerted": alerted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_fail": skipped_fail,
        "skipped_error": skipped_error,
    }
    logger.info("Scan complete: %s", summary)
    return jsonify(summary), 200
