# Swing Replay Snapshot System

## Overview

This system eliminates look-ahead bias in historical replay by capturing End-of-Day (EOD) snapshots of market state and replaying from these snapshots instead of fetching live data.

## Architecture

```
Live Trading (Post-Market):
└─> Capture EOD Snapshot
    ├─> Bars (OHLCV)
    ├─> Fundamentals
    ├─> Macro data
    ├─> News/Sentiment
    └─> Rolling cache state
    
Replay Mode:
└─> Load Snapshot (not fetch)
    └─> All data from snapshot (point-in-time accurate)
```

## Components

### 1. Snapshot Manager (`backend/historical_replay_swing/snapshot_manager.py`)
- **EODSnapshot**: Dataclass containing complete market state for a single day
- **SnapshotManager**: Manages snapshot save/load operations
- **capture_eod_snapshot()**: Captures current market state

### 2. Replay Data Pipeline (`backend/services/replay_data_pipeline.py`)
- Detects replay mode via environment variables
- Provides replay-aware data loaders
- Routes requests to snapshots in replay mode, live APIs in normal mode

### 3. Validation System (`backend/historical_replay_swing/validation.py`)
- **ReplayValidator**: Validates snapshots for look-ahead bias
- Checks bar dates don't exceed snapshot date
- Validates training data doesn't contain future information

## Usage

### Automatic Snapshot Capture (Live Mode)

Snapshots are automatically captured after each successful nightly job run:

```python
# In nightly_job.py (automatic)
from backend.historical_replay_swing.snapshot_manager import (
    capture_eod_snapshot,
    SnapshotManager,
)
from backend.core.config import PATHS

snapshot = capture_eod_snapshot()
manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
manager.save_snapshot(snapshot)
```

### Running Historical Replay

1. **Ensure snapshots exist** for the date range you want to replay
2. **Start replay** using existing replay API/CLI
3. **Replay mode automatically**:
   - Sets `AION_RUN_MODE=replay`
   - Sets `AION_ASOF_DATE=<date>`
   - Loads data from snapshots
   - Validates no future data leakage

### Manual Snapshot Capture

```python
from pathlib import Path
from backend.historical_replay_swing.snapshot_manager import (
    capture_eod_snapshot,
    SnapshotManager,
)
from backend.core.config import PATHS

# Capture snapshot
snapshot = capture_eod_snapshot()

# Save snapshot
manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
manager.save_snapshot(snapshot)

print(f"Snapshot saved for {snapshot.date}")
print(f"Bars: {len(snapshot.bars)}")
print(f"Symbols: {len(snapshot.fundamentals)}")
```

### List Available Snapshots

```python
from pathlib import Path
from backend.historical_replay_swing.snapshot_manager import SnapshotManager
from backend.core.config import PATHS

manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
snapshots = manager.list_snapshots()

print(f"Available snapshots: {len(snapshots)}")
for date in snapshots:
    print(f"  - {date}")
```

### Validate Snapshot

```python
from pathlib import Path
from backend.historical_replay_swing.snapshot_manager import SnapshotManager
from backend.historical_replay_swing.validation import ReplayValidator
from backend.core.config import PATHS

manager = SnapshotManager(Path(PATHS["swing_replay_snapshots"]))
validator = ReplayValidator()

date = "2024-01-15"
snapshot = manager.load_snapshot(date)
result = validator.validate_snapshot(date, snapshot)

if result.valid:
    print(f"✅ Snapshot {date} is valid")
else:
    print(f"❌ Snapshot {date} has errors:")
    for error in result.errors:
        print(f"  - {error}")

if result.warnings:
    print("⚠️ Warnings:")
    for warning in result.warnings:
        print(f"  - {warning}")
```

## Environment Variables

- `AION_RUN_MODE`: Set to "replay" for replay mode (automatically set by job_manager)
- `AION_ASOF_DATE`: Date for replay (YYYY-MM-DD format, automatically set by job_manager)

## Data Flow

### Live Mode (Normal Trading)
1. Nightly job runs
2. Fetchers query live APIs (StockAnalysis, FRED, Alpaca)
3. Data stored in rolling cache
4. **NEW**: EOD snapshot captured after completion
5. Snapshot stored in `data/replay/swing/snapshots/<date>/`

### Replay Mode (Historical Simulation)
1. Job manager checks if snapshot exists for date
2. Snapshot loaded and validated
3. Environment variables set (AION_RUN_MODE=replay, AION_ASOF_DATE=<date>)
4. Data fetchers detect replay mode
5. **All data loaded from snapshot** (zero API calls)
6. Nightly job runs with historical data
7. Models trained only on data <= as_of_date

## Snapshot Storage Structure

```
data/replay/swing/snapshots/
├── 2024-01-15/
│   ├── manifest.json          # Metadata
│   ├── bars.parquet           # OHLCV data
│   ├── fundamentals.json.gz   # Fundamental data
│   ├── macro.json.gz          # Macro indicators
│   ├── news.json.gz           # News data
│   ├── sentiment.json.gz      # Sentiment data
│   └── rolling.json.gz        # Rolling cache state
├── 2024-01-16/
│   └── ...
└── 2024-01-17/
    └── ...
```

## Validation Checks

### Snapshot Validation
- ✅ Bar dates ≤ snapshot date
- ✅ Data completeness checks
- ⚠️ Warnings for missing data (non-fatal)

### Training Data Validation
- ✅ All training data dates ≤ as_of_date
- ✅ No future leakage in features

## Modified Components

### Data Fetchers (Replay-Aware)
- `backend/services/backfill_history.py`
- `backend/services/fundamentals_fetcher.py`
- `backend/services/macro_fetcher.py`

All now check `is_replay_mode()` and load from snapshots instead of APIs.

### Training Pipeline
- `backend/core/ai_model/core_training.py`
  - Accepts `as_of_date` parameter
  - Passes through to dataset builder for point-in-time filtering

### Job Manager
- `backend/historical_replay_swing/job_manager.py`
  - Validates snapshot exists before replay
  - Loads and validates snapshot
  - Sets environment variables
  - Runs nightly job in replay mode

### Nightly Job
- `backend/jobs/nightly_job.py`
  - Captures EOD snapshot after completion (non-replay mode only)
  - Passes `as_of_date` to training pipeline

## Testing

Run the test suite:

```bash
python3 tests/unit/test_replay_snapshot.py
```

Tests cover:
- ✅ Snapshot save/load functionality
- ✅ Replay mode detection
- ✅ Snapshot validation (including future data detection)

## Benefits

### Before (Look-Ahead Bias Issues)
- ❌ Fetchers used current date to fetch historical data
- ❌ Could see future bars when simulating past dates
- ❌ Model training could use future data
- ❌ Replay results were **invalid**

### After (Zero Look-Ahead Bias)
- ✅ Snapshots capture exact EOD state
- ✅ Replay uses only snapshot data
- ✅ Validation detects any future data leakage
- ✅ Replay results are **valid and reproducible**

## Performance

- Snapshot capture: ~2-5 seconds (runs once per day after nightly job)
- Snapshot load: ~0.5-2 seconds (faster than live API calls)
- Storage: ~10-50 MB per day (compressed)
- Net benefit: Replay is **faster** (no API calls) and **correct**

## Troubleshooting

### "No snapshot for date"
- Ensure nightly job has run for that date in live mode
- Check snapshot directory: `data/replay/swing/snapshots/`
- Manually capture snapshot if needed

### "Snapshot validation failed"
- Review validation errors in job manager logs
- Check if snapshot was corrupted
- Re-capture snapshot if needed

### "Replay mode not working"
- Check environment variables are set correctly
- Verify `is_replay_mode()` returns True
- Review fetcher logs for "Replay mode:" messages

## Future Enhancements

Potential improvements (not in current scope):
- Incremental snapshot updates
- Snapshot compression optimization
- Snapshot cloud backup/restore
- Snapshot diff/delta storage
- Multi-day snapshot validation

## Related Documentation

- Original issue: Problem statement in PR description
- Code review results: See code review comments
- Testing: `tests/unit/test_replay_snapshot.py`
