"""
/api/scan — Scheduled token discovery, safety check, and alert endpoint.
Called every 2 minutes by the GitHub Actions workflow in .github/workflows/scan-cron.yml.

Security: only processes requests that carry the correct SCAN_SECRET header.
"""

import os
import logging
import time
from http.server import BaseHTTPRequestHandler

# Ensure lib/ is importable (Vercel adds the project root to sys.path)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib import redis_client, dexscreener, safety, telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCAN_SECRET = os.environ.get("SCAN_SECRET", "")

# ---------------------------------------------------------------------------
# Vercel serverless handler
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self._run_scan()

    def do_GET(self):
        self._run_scan()

    def _run_scan(self):
        # ----------------------------------------------------------------
        # 1. Authenticate — reject anything without the correct secret header
        # ----------------------------------------------------------------
        provided_secret = self.headers.get("X-Scan-Secret", "")
        if not SCAN_SECRET or provided_secret != SCAN_SECRET:
            logger.warning("Scan request rejected: invalid or missing X-Scan-Secret")
            self._respond(401, {"error": "Unauthorized"})
            return

        # ----------------------------------------------------------------
        # 2. Check if scanning is enabled (Redis toggle)
        # ----------------------------------------------------------------
        if not redis_client.is_scanning_enabled():
            logger.info("Scanning is disabled — exiting early")
            self._respond(200, {"status": "skipped", "reason": "scanning_disabled"})
            return

        # ----------------------------------------------------------------
        # 3. Discover new tokens on Robinhood Chain via DexScreener
        # ----------------------------------------------------------------
        logger.info("Fetching new pairs from DexScreener...")
        new_tokens = dexscreener.get_new_pairs()
        logger.info("Found %d new token(s)", len(new_tokens))

        if not new_tokens:
            self._respond(200, {"status": "ok", "new_tokens": 0, "alerted": 0})
            return

        alerted = 0
        skipped_duplicate = 0
        skipped_fail = 0
        skipped_error = 0

        # ----------------------------------------------------------------
        # 4. Process EVERY new token — don't assume there's only one
        # ----------------------------------------------------------------
        for token in new_tokens:
            contract = token["contract_address"]

            # Skip tokens we've already alerted on (dedup across scan runs)
            if redis_client.has_been_alerted(contract):
                logger.info("Skipping %s — already alerted", contract)
                skipped_duplicate += 1
                continue

            # Run all safety checks
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

            # FAIL tokens do not get alerted — add to alerted set so we don't
            # recheck them every 2 minutes
            if safety_result["safety_status"] == "FAIL":
                logger.info("Skipping %s — FAIL status", contract)
                try:
                    redis_client.mark_alerted(contract)
                except Exception as exc:
                    logger.error("Failed to mark FAIL token as alerted: %s", exc)
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
                logger.error("Alert not sent for %s — Telegram returned failure", contract)
                skipped_error += 1

            # Small delay between tokens to avoid rate-limiting Telegram
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
        self._respond(200, summary)

    def _respond(self, status_code: int, body: dict):
        import json
        payload = json.dumps(body).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        logger.info(fmt, *args)
