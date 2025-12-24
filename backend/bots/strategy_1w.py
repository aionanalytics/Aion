# backend/bots/strategy_1w.py
"""
Horizon-specific config for 1-week EOD swing bot.
Loaded via dynamic config store so it can be edited from the frontend.
"""

from __future__ import annotations

from backend.bots.config_store import get_bot_config

CONFIG = get_bot_config("eod_1w")
