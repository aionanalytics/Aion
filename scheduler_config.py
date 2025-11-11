"""
scheduler_config.py — v1.7
Controls when each automated backend job runs.
"""

ENABLE = True
TIMEZONE = "America/Denver"

# Each entry defines one scheduled backend task.
# Times are in 24-hour format (local time in TIMEZONE).

SCHEDULE = [
    {
        "name": "nightly_full",
        "time": "22:00",  # nightly rebuild
        "script": "backend/nightly_job.py",
        "args": [],
        "description": "Full nightly rebuild: backfill, metrics, build, train, drift, insights."
    },
    {
        "name": "midday_light",
        "time": "12:30",  # midday refresh
        "script": "backend/backfill_history.py",
        "args": ["--light"],
        "description": "Midday light refresh: StockAnalysis bundle + metrics update."
    },
    {
        "name": "evening_insights",
        "time": "18:00",
        "script": "backend/insights_builder.py",
        "args": [],
        "description": "Rebuilds insights (top picks, filters, scores) before market close."
    },
    {
        "name": "social_sentiment",
        "time": "20:30",
        "script": "backend/social_sentiment_fetcher.py",
        "args": [],
        "description": "Collects Reddit/FinBERT sentiment and writes social_sentiment_YYYYMMDD.json."
    },

    # ==============================================================
    # DAY TRADING JOBS (dt_backend)
    # ==============================================================
    {
        "name": "daytrade_prep",
        "time": "07:30",
        "script": "dt_backend/daytrading_job.py",
        "args": [],
        "description": "Collect bars and update intraday rolling + ML dataset."
    },
    {
        "name": "daytrade_bots_full",
        "time": "07:31",
        "script": "dt_backend/trading_bot_simulator.py",
        "args": ["--mode", "full"],
        "description": "Run full initialization for intraday bots before open."
    },
    {
        "name": "daytrade_bots_loop",
        "time": "09:30",
        "script": "dt_backend/trading_bot_simulator.py",
        "args": ["--mode", "loop"],
        "description": "Hourly intraday trading loop during market hours."
    },

    # ==============================================================
    # NIGHTLY BOT JOBS (backend)
    # ==============================================================
    {
        "name": "nightly_bots_full_1w",
        "time": "07:30",
        "script": "backend/trading_bot_nightly_1w.py",
        "args": ["--mode", "full"],
        "description": "Full pre-market reevaluation for 1-week bots."
    },
    {
        "name": "nightly_bots_full_2w",
        "time": "07:33",
        "script": "backend/trading_bot_nightly_2w.py",
        "args": ["--mode", "full"],
        "description": "Full pre-market reevaluation for 2-week bots."
    },
    {
        "name": "nightly_bots_full_4w",
        "time": "07:36",
        "script": "backend/trading_bot_nightly_4w.py",
        "args": ["--mode", "full"],
        "description": "Full pre-market reevaluation for 4-week bots."
    },
    {
        "name": "nightly_bots_loop_1w",
        "time": "09:30",
        "script": "backend/trading_bot_nightly_1w.py",
        "args": ["--mode", "loop"],
        "description": "Hourly intraday loop for 1-week bots (market hours only)."
    },
    {
        "name": "nightly_bots_loop_2w",
        "time": "09:35",
        "script": "backend/trading_bot_nightly_2w.py",
        "args": ["--mode", "loop"],
        "description": "Hourly intraday loop for 2-week bots (market hours only)."
    },
    {
        "name": "nightly_bots_loop_4w",
        "time": "09:40",
        "script": "backend/trading_bot_nightly_4w.py",
        "args": ["--mode", "loop"],
        "description": "Hourly intraday loop for 4-week bots (market hours only)."
    },
]
