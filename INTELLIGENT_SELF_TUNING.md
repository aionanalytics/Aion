# Intelligent Self-Tuning System for Swing Bots

## Overview

The Intelligent Self-Tuning System enables swing trading bots to autonomously learn from their trading outcomes and optimize all critical parameters to maximize risk-adjusted returns (Sharpe ratio). The system implements a production-grade autonomous optimization pipeline with comprehensive safety guardrails.

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Swing Bot Execution                       │
│  (base_swing_bot.py runs 1w/2w/4w strategies)               │
└────────────────────┬────────────────────────────────────────┘
                     │ Logs every trade outcome
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              Trade Outcome Logger                            │
│  (swing_outcome_logger.py stores outcomes to JSONL)         │
└────────────────────┬────────────────────────────────────────┘
                     │ Analyzed nightly
                     ▼
┌─────────────────────────────────────────────────────────────┐
│            Tuning Orchestrator (Nightly Job)                 │
│  (swing_tuning_orchestrator.py)                             │
│    ├─ Update P(hit) Calibration                             │
│    ├─ Optimize Confidence Thresholds                        │
│    ├─ Optimize Position Sizing                              │
│    ├─ Optimize Exit Strategy                                │
│    └─ Validate & Apply Changes                              │
└────────────────────┬────────────────────────────────────────┘
                     │ Updates configs
                     ▼
┌─────────────────────────────────────────────────────────────┐
│         Bot Configuration (configs.json)                     │
│  Bots load optimized parameters on next run                 │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

1. **Trade Outcome Logger** (`backend/services/swing_outcome_logger.py`)
   - Records every swing trade execution with comprehensive metadata
   - Tracks: symbol, entry/exit prices, confidence, expected vs actual returns, hold duration, exit reason, market regime
   - Storage: Append-only JSONL file (`da_brains/swing/outcomes/swing_outcomes.jsonl`)
   - Enables outcome attribution per confidence bucket and regime

2. **P(hit) Calibrator** (`backend/calibration/phit_calibrator_swing.py`)
   - Learns actual probability of success from historical outcomes
   - Bins trades by confidence level, expected return, and market regime
   - Calculates actual win rates per bucket
   - Updates P(hit) estimates for improved position sizing
   - Falls back to formula-based calibration if insufficient data

3. **Confidence Threshold Optimizer** (`backend/tuning/swing_threshold_optimizer.py`)
   - Analyzes trade outcomes to find optimal confidence threshold per regime
   - Tests thresholds from 0.40-0.75 in configurable steps
   - Calculates Sharpe ratio for each threshold
   - Identifies sweet spot that maximizes risk-adjusted returns
   - Applies changes only if improvement ≥5% (configurable)

4. **Position Sizing Tuner** (`backend/tuning/swing_position_tuner.py`)
   - Optimizes position sizing parameters:
     - `starter_fraction`: Initial entry size (25-50% of goal)
     - `add_fraction`: Add-on size (20-50% of goal)
     - `max_weight_per_name`: Max concentration per symbol (5-25%)
   - Analyzes position survival rates and drawdowns
   - Finds optimal sizing for maximum Sharpe ratio

5. **Exit Strategy Optimizer** (`backend/tuning/swing_exit_optimizer.py`)
   - Tunes exit discipline parameters:
     - `stop_loss_pct`: Stop loss threshold (-2% to -8%)
     - `take_profit_pct`: Take profit threshold (+5% to +20%)
   - Analyzes exit reason distribution
   - Optimizes for Sharpe ratio improvement
   - Supports per-regime tuning

6. **Tuning Validator** (`backend/tuning/swing_tuning_validator.py`)
   - Comprehensive safety guardrails:
     - Minimum 50 trades before applying tuning (configurable)
     - 95% confidence intervals on all estimates
     - Only applies if Sharpe ratio improves ≥5%
     - Maximum 20% parameter change per cycle
     - Auto-rollback if Sharpe drops >10% in next period
     - Per-regime validation
   - Full audit trail with all validation checks and decisions

7. **Tuning Orchestrator** (`backend/tuning/swing_tuning_orchestrator.py`)
   - Coordinates entire tuning pipeline:
     - Runs nightly (configurable schedule)
     - Aggregates 30-day outcome window (configurable)
     - Calls each tuner in sequence
     - Validates all proposed changes
     - Persists new parameters to configs.json
     - Logs tuning history with before/after metrics
   - Handles per-bot tuning (1w, 2w, 4w separately)
   - Enables/disables tuning via feature flags
   - Manages tuning phases (outcome logging → calibration → full optimization)

8. **Swing Tuning Router** (`backend/routers/swing_tuning_router.py`)
   - 8 API endpoints for full visibility and control
   - Monitoring endpoints (history, metrics, outcomes, calibration)
   - Control endpoints (enable, override, rollback, manual run)
   - Full audit trail accessible via API

## Configuration

### Environment Variables

```bash
# Enable/Disable Tuning
SWING_TUNING_ENABLED=true

# Data Requirements
SWING_TUNING_MIN_TRADES=50              # Minimum trades before tuning
SWING_TUNING_WINDOW_DAYS=30             # Days of outcomes to analyze

# Validation Thresholds
SWING_TUNING_MIN_SHARPE_IMPROVEMENT=0.05  # Minimum 5% Sharpe improvement
SWING_TUNING_MAX_CHANGE_PCT=0.20          # Maximum 20% parameter change
SWING_TUNING_ROLLBACK_THRESHOLD=0.10      # Rollback if Sharpe drops >10%
SWING_TUNING_CONFIDENCE_LEVEL=0.95        # 95% confidence intervals

# Schedule
SWING_TUNING_SCHEDULE="22:00"           # Nightly at 10pm UTC

# Phase Control
SWING_TUNING_PHASE="full"               # Options: logging_only, calibration, threshold, position, exit, full

# P(hit) Calibration
SWING_PHIT_CALIBRATION_ENABLED=true
SWING_PHIT_MIN_SAMPLES=10               # Minimum trades per calibration bucket
```

### Bot Configuration (configs.json)

```json
{
  "1w": {
    "tuning_enabled": true,
    "per_regime_tuning": true,
    "auto_rollback_enabled": true,
    "conf_threshold": 0.55,
    "starter_fraction": 0.35,
    "max_weight_per_name": 0.15,
    "stop_loss_pct": -0.05,
    "take_profit_pct": 0.10
  }
}
```

## Phase-Based Rollout

The system supports gradual rollout through phases:

### Phase 1: Outcome Logging Only
```bash
SWING_TUNING_ENABLED=true
SWING_TUNING_PHASE="logging_only"
```
- Collects baseline trade outcomes
- No parameter changes applied
- Build confidence in data quality

### Phase 2: P(hit) Calibration + Threshold Optimization
```bash
SWING_TUNING_PHASE="threshold"
```
- Updates P(hit) calibration from outcomes
- Optimizes confidence thresholds per regime
- First parameter improvements applied

### Phase 3: Position Sizing
```bash
SWING_TUNING_PHASE="position"
```
- Optimizes starter_fraction, add_fraction, max_weight_per_name
- Better position scaling based on actual performance

### Phase 4: Exit Strategy
```bash
SWING_TUNING_PHASE="exit"
```
- Optimizes stop loss and take profit levels
- Refines exit discipline

### Phase 5: Full Autonomous Tuning
```bash
SWING_TUNING_PHASE="full"
```
- All optimization phases active
- Complete autonomous tuning pipeline
- Zero manual knob tuning required

## API Endpoints

### Monitoring Endpoints

#### GET /api/eod/tuning/{bot_key}/history
Get tuning decision history for a bot.

**Query Parameters:**
- `limit`: Max records to return (default: 100, max: 1000)
- `regime`: Filter by regime (optional)
- `parameter`: Filter by parameter name (optional)

**Response:**
```json
{
  "bot_key": "swing_1w",
  "total_decisions": 15,
  "decisions": [
    {
      "bot_key": "swing_1w",
      "regime": "bull",
      "decision_ts": "2026-01-27T22:00:00Z",
      "phase": "threshold_optimization",
      "parameter": "conf_threshold",
      "old_value": 0.55,
      "new_value": 0.58,
      "improvement_pct": 0.072,
      "sharpe_old": 1.45,
      "sharpe_new": 1.55,
      "confidence_interval": [0.56, 0.60],
      "trades_analyzed": 67,
      "applied": true,
      "reason": "Sharpe improved >5% with 95% confidence"
    }
  ]
}
```

#### GET /api/eod/tuning/{bot_key}/metrics
Get current performance metrics and Sharpe ratio.

**Query Parameters:**
- `days`: Days of data to analyze (default: 30)

**Response:**
```json
{
  "bot_key": "swing_1w",
  "days_analyzed": 30,
  "total_trades": 67,
  "win_rate": 0.612,
  "avg_return": 0.042,
  "sharpe_ratio": 1.55,
  "avg_hold_hours": 48.3,
  "exit_reasons": {
    "TAKE_PROFIT": 32,
    "STOP_LOSS": 15,
    "TARGET_REBALANCE": 12,
    "AI_CONFIRM": 5,
    "TIME_STOP": 3
  },
  "regime_breakdown": {
    "bull": {
      "total_trades": 45,
      "win_rate": 0.67,
      "sharpe_ratio": 1.72
    },
    "bear": {
      "total_trades": 22,
      "win_rate": 0.50,
      "sharpe_ratio": 0.98
    }
  }
}
```

#### GET /api/eod/tuning/{bot_key}/outcomes
Get recent trade outcomes.

**Query Parameters:**
- `days`: Days to look back (default: 30)
- `limit`: Max outcomes to return (default: 100)

#### GET /api/eod/tuning/{bot_key}/calibration
Get P(hit) calibration table.

**Response:**
```json
{
  "bot_key": "swing_1w",
  "total_buckets": 12,
  "calibration": {
    "bull_0.60-0.70_3-6%": {
      "phit": 0.65,
      "samples": 15,
      "avg_return": 0.048
    }
  }
}
```

### Control Endpoints

#### POST /api/eod/tuning/{bot_key}/enable
Enable or disable tuning for a bot.

**Request:**
```json
{
  "enabled": true
}
```

#### POST /api/eod/tuning/{bot_key}/override
Manually override a parameter.

**Request:**
```json
{
  "parameter": "conf_threshold",
  "value": 0.60,
  "regime": "bull"
}
```

#### POST /api/eod/tuning/{bot_key}/rollback
Rollback to previous configuration.

**Request:**
```json
{
  "steps": 1
}
```

#### POST /api/eod/tuning/run
Manually trigger a tuning run.

**Query Parameters:**
- `phase`: Tuning phase to run (default: "full")

## Safety Mechanisms

### 1. Data Requirements
- Minimum 50 trades before any tuning (configurable)
- Sufficient data per regime for regime-specific tuning
- Graceful degradation if data insufficient

### 2. Statistical Validation
- 95% confidence intervals on all estimates
- Sharpe improvement gate: ≥5% required
- Multiple hypothesis testing protection

### 3. Parameter Bounds
- Maximum 20% parameter change per cycle
- Regime-specific bounds for each parameter
- Hard limits prevent extreme values

### 4. Auto-Rollback
- Monitors performance after changes
- Automatic rollback if Sharpe drops >10%
- Rollback history maintained for audit

### 5. Feature Flags
- Enable/disable tuning per bot
- Phase-based rollout control
- Manual override capability

### 6. Audit Trail
- Every decision logged to history
- Full before/after metrics
- Rollback capability

## Expected Outcomes

### Week 1
- ✅ 50+ trades collected
- ✅ Outcome data flowing to database

### Week 2-3
- ✅ P(hit) calibration active
- ✅ Threshold optimizer running
- ✅ `conf_threshold` adjusts based on performance
- ✅ First parameter improvements applied

### Week 4
- ✅ Position sizing tuner active
- ✅ `starter_fraction`, `add_fraction`, `max_weight_per_name` optimized
- ✅ Better position scaling

### Week 5+
- ✅ Exit strategy tuner active
- ✅ Stops, take-profits refined
- ✅ **Full autonomous tuning running**
- ✅ **Swing bot Sharpe ratio improves 10-30%**
- ✅ **Performance improvements compound over time**
- ✅ **Zero manual knob tuning required**

## Testing

### Unit Tests
- **46 tests passing** across all components
- Test files:
  - `tests/unit/test_swing_outcome_logger.py` (9 tests)
  - `tests/unit/test_swing_tuning_validator.py` (19 tests)
  - `tests/unit/test_swing_threshold_optimizer.py` (8 tests)
  - `tests/unit/test_swing_tuning_orchestrator.py` (10 tests)

### Running Tests
```bash
# Run all tuning tests
pytest tests/unit/test_swing_outcome_logger.py \
       tests/unit/test_swing_tuning_validator.py \
       tests/unit/test_swing_threshold_optimizer.py \
       tests/unit/test_swing_tuning_orchestrator.py -v

# Run with coverage
pytest tests/unit/test_swing_*.py --cov=backend.services.swing_outcome_logger \
                                   --cov=backend.tuning \
                                   --cov=backend.calibration.phit_calibrator_swing
```

## Monitoring & Debugging

### Log Files
- Outcome log: `da_brains/swing/outcomes/swing_outcomes.jsonl`
- Tuning history: `da_brains/swing/tuning_history.jsonl`
- Calibration table: `da_brains/swing/phit_calibration.json`

### Checking System Health
```bash
# Check recent outcomes
curl http://localhost:8000/api/eod/tuning/swing_1w/outcomes?days=7

# Check current metrics
curl http://localhost:8000/api/eod/tuning/swing_1w/metrics

# Check tuning history
curl http://localhost:8000/api/eod/tuning/swing_1w/history

# Trigger manual tuning run
curl -X POST http://localhost:8000/api/eod/tuning/run?phase=full
```

## Troubleshooting

### Issue: No tuning decisions being made
**Check:**
1. Is `SWING_TUNING_ENABLED=true`?
2. Are there enough trades? (minimum 50)
3. Check tuning history for validation failures
4. Review metrics endpoint for data quality

### Issue: Tuning applied but performance degraded
**Solution:**
1. Check if auto-rollback triggered
2. Manually rollback: `POST /api/eod/tuning/{bot_key}/rollback`
3. Review decision that caused degradation
4. Consider disabling specific optimization phase

### Issue: Outcome logging not working
**Check:**
1. Verify swing bot is executing trades
2. Check `da_brains/swing/outcomes/` directory exists
3. Review bot logs for outcome logger errors
4. Ensure Position dataclass has entry metadata

## Future Enhancements

1. **Multi-objective Optimization**
   - Optimize for both Sharpe ratio and max drawdown
   - Pareto frontier exploration

2. **Advanced ML Calibration**
   - Neural network for P(hit) calibration
   - Time-series forecasting of regime transitions

3. **Dynamic Regime Detection**
   - Adaptive regime boundaries
   - Automatic regime discovery

4. **Portfolio-Level Optimization**
   - Optimize across all bots simultaneously
   - Correlation-aware position sizing

5. **Real-time Tuning**
   - Intraday parameter adjustments
   - Streaming outcome processing

## References

- Outcome Logger: `backend/services/swing_outcome_logger.py`
- Tuning Validator: `backend/tuning/swing_tuning_validator.py`
- Threshold Optimizer: `backend/tuning/swing_threshold_optimizer.py`
- Position Tuner: `backend/tuning/swing_position_tuner.py`
- Exit Optimizer: `backend/tuning/swing_exit_optimizer.py`
- P(hit) Calibrator: `backend/calibration/phit_calibrator_swing.py`
- Orchestrator: `backend/tuning/swing_tuning_orchestrator.py`
- API Router: `backend/routers/swing_tuning_router.py`
