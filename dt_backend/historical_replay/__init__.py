"""
dt_backend.historical_replay package

Modern components:
  • historical_replay_engine   → per-day intraday replay + PnL
  • historical_replay_manager  → multi-day replay ranges + replay_log.json
  • sequence_builder           → deep-learning sequence datasets
  • replay_harness             → full pipeline (replay + sequences)
  • job_manager                → background replay jobs with progress
"""

from .historical_replay_engine import (
    replay_intraday_day,
    ReplayResult,
)

from .historical_replay_manager import (
    run_replay_range,
    ReplaySummary,
)

from .sequence_builder import (
    build_sequences_for_symbol,
    write_sequence_dataset,
)

from .replay_harness import (
    build_sequences_from_rolling,
    run_full_replay_and_sequences,
)

from .job_manager import (
    create_job,
    start_job,
    list_jobs,
    get_job,
    cancel_job,
    JOBS,
)

__all__ = [
    # Engine
    "replay_intraday_day",
    "ReplayResult",
    # Manager
    "run_replay_range",
    "ReplaySummary",
    # Sequences
    "build_sequences_for_symbol",
    "write_sequence_dataset",
    "build_sequences_from_rolling",
    "run_full_replay_and_sequences",
    # Jobs
    "create_job",
    "start_job",
    "list_jobs",
    "get_job",
    "cancel_job",
    "JOBS",
]
