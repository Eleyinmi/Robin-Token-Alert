"""
Flask entrypoint — Vercel detects this as the Python web app.
Registers /api/scan and /api/bot blueprints.
"""

import logging
from flask import Flask
from api.scan import scan_bp
from api.bot import bot_bp

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.register_blueprint(scan_bp)
app.register_blueprint(bot_bp)
