"""
Scan blueprint — /api/scan
Called every 2 minutes by GitHub Actions.
"""

import os
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, request, jsonify
from bot_lib import redis_client, dexscreener, safety, telegram

logger = logging.getLogger(__name__)

scan_bp = Blueprint("scan", __name__)

SCAN_SECRET = os.environ.get("SCAN_SECRET", "")


@scan_bp.route("/api/scan", methods=["GET", "POST"])
def scan():
    provided = request.headers.get("X-Scan-Secret", "")
    if not SCAN_SECRET or provided != SCAN_SECRET:
        logger.warning("Scan request rejected: invalid or missing X-Scan-Secret")
        return jsonify({"error": "Unauthorized"}), 401

    if not redis_client.is_scanning_enabled():
        logger.info("Scanning is disabled — exiting early")
        return jsonify({"status": "skipped", "reason": "scanning_disabled"}), 200

    logger.info("Fetching new pairs from DexScreener...")
    new_tokens = dexscreener.get_new_pairs()
    logger.info("Found %d new token(s)", len(new_tokens))

    if not new_tokens:
        return jsonify({"status": "ok", "new_tokens": 0, "alerted": 0}), 200

    alerted = 0
    skipped_duplicate = 0
    skipped_fail = 0
    skipped_error = 0

    for token in new_tokens:
        contract = token["contract_address"]

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

        logger.info("Token %s (%s) — safety: %s", token["symbol"], contract,
                    safety_result["safety_status"])

        if safety_result["safety_status"] == "FAIL":
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            skipped_fail += 1
            continue

        try:
            sent = telegram.send_alert(token, safety_result)
        except Exception as exc:
            logger.error("Telegram send failed for %s: %s", contract, exc)
            skipped_error += 1
            continue

        if sent:
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            alerted += 1
        else:
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
