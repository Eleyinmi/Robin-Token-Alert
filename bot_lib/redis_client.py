import os
import logging
import requests

logger = logging.getLogger(__name__)

REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

def _headers() -> dict:
    return {"Authorization": f"Bearer {REDIS_REST_TOKEN}"}

def _call(command: list
