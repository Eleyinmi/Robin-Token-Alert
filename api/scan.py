import os
import sys
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify
from bot_lib import redis_client, dexscreener, safety, telegram as tg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SCAN_SECRET = os.environ.get("SCAN_SECRET", "")


@app.route("/api/scan", methods=["GET", "POST"])
def scan():
    provided = request.headers.get("X-Scan-Secret", "")
    if not SCAN_SECRET or provided != SCAN_SECRET:
        logger.warning("Scan rejected: bad secret")
        return jsonify({"error": "Unauthorized"}), 401

    if not redis_client.is_scanning_enabled():
        return jsonify({"status": "skipped", "reason": "scanning_disabled"}), 200

    new_tokens = dexscreener.get_new_pairs()
    logger.info("Found %d new token(s)", len(new_tokens))

    if not new_tokens:
        return jsonify({"status": "ok", "new_tokens": 0, "alerted": 0}), 200

    alerted = skipped_duplicate = skipped_fail = skipped_error = 0

    for token in new_tokens:
        contract = token["contract_address"]
        if redis_client.has_been_alerted(contract):
            skipped_duplicate += 1
            continue
        try:
            result = safety.run_safety_checks(token)
        except Exception as exc:
            logger.error("Safety check error for %s: %s", contract, exc)
            skipped_error += 1
            continue

        if result["safety_status"] == "FAIL":
            try:
                redis_client.mark_alerted(contract)
            except Exception:
                pass
            skipped_fail += 1
            continue

        try:
            sent = tg.send_alert(token, result)
        except Exception as exc:
            logger.error("Telegram send error for %s: %s", contract, exc)
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

    return jsonify({
        "status": "ok",
        "new_tokens": len(new_tokens),
        "alerted": alerted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_fail": skipped_fail,
        "skipped_error": skipped_error,
    }), 200
