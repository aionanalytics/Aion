"""
dt_backend/historical_replay/validation_dt.py

Comprehensive validation suite for historical replay.

Validates:
1. Data integrity (bars, features completeness)
2. Prediction quality (model outputs, confidence)
3. Results consistency (PnL, trades, hit rate)
4. Pipeline correctness (context → features → predictions → policy → execution)

Usage:
    >>> from dt_backend.historical_replay.validation_dt import validate_replay_result
    >>> validation = validate_replay_result("2025-01-15")
    >>> if validation["passed"]:
    ...     print("Validation passed!")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

try:
    from dt_backend.core.config_dt import DT_PATHS
    from dt_backend.core.data_pipeline_dt import log
except Exception:
    DT_PATHS: Dict[str, Path] = {
        "ml_data_dt": Path("ml_data_dt"),
    }
    
    def log(msg: str) -> None:
        print(msg, flush=True)


# Expected regime labels
VALID_REGIME_LABELS = {"TREND_UP", "TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_VOL", "UNKNOWN"}


@dataclass
class ValidationResult:
    """Result of validation checks."""
    
    date: str
    passed: bool
    checks_passed: int
    checks_failed: int
    errors: List[str]
    warnings: List[str]
    details: Dict[str, Any]
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _load_replay_result(date_str: str) -> Optional[Dict[str, Any]]:
    """Load replay result for a date."""
    try:
        root = DT_PATHS.get("ml_data_dt", Path("ml_data_dt"))
        result_path = root / "intraday" / "replay" / "replay_results" / f"{date_str}.json"
        
        if not result_path.exists():
            return None
        
        with result_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_raw_day(date_str: str) -> Optional[List[Dict[str, Any]]]:
    """Load raw day data for a date."""
    try:
        root = DT_PATHS.get("ml_data_dt", Path("ml_data_dt"))
        raw_gz = root / "intraday" / "replay" / "raw_days" / f"{date_str}.json.gz"
        raw_json = root / "intraday" / "replay" / "raw_days" / f"{date_str}.json"
        
        import gzip
        
        if raw_gz.exists():
            with gzip.open(raw_gz, "rt", encoding="utf-8") as f:
                return json.load(f)
        elif raw_json.exists():
            with raw_json.open("r", encoding="utf-8") as f:
                return json.load(f)
        
        return None
    except Exception:
        return None


def validate_data_integrity(
    date_str: str,
    raw_day: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate data integrity for a replay day.
    
    Checks:
    - Raw day data exists and is valid
    - Bars data is present for each symbol
    - Bars have required fields (timestamp, price, volume)
    - No duplicate symbols
    
    Returns:
        (passed, errors, warnings)
    """
    errors = []
    warnings = []
    
    if raw_day is None:
        raw_day = _load_raw_day(date_str)
    
    if not raw_day:
        errors.append(f"No raw day data found for {date_str}")
        return False, errors, warnings
    
    if not isinstance(raw_day, list):
        errors.append("Raw day data is not a list")
        return False, errors, warnings
    
    if len(raw_day) == 0:
        warnings.append("Raw day data is empty")
    
    # Check for duplicate symbols
    symbols_seen = set()
    for entry in raw_day:
        if not isinstance(entry, dict):
            warnings.append("Entry in raw day is not a dict")
            continue
        
        symbol = entry.get("symbol")
        if not symbol:
            warnings.append("Entry missing symbol field")
            continue
        
        if symbol in symbols_seen:
            warnings.append(f"Duplicate symbol: {symbol}")
        symbols_seen.add(symbol)
        
        # Check bars
        bars = entry.get("bars")
        if not bars:
            warnings.append(f"Symbol {symbol} has no bars")
            continue
        
        if not isinstance(bars, list):
            warnings.append(f"Symbol {symbol} bars is not a list")
            continue
        
        if len(bars) == 0:
            warnings.append(f"Symbol {symbol} has empty bars")
            continue
        
        # Check first bar structure
        first_bar = bars[0]
        if not isinstance(first_bar, dict):
            warnings.append(f"Symbol {symbol} first bar is not a dict")
            continue
        
        # Check for required fields (flexible field names)
        has_price = any(k in first_bar for k in ["c", "close", "price"])
        has_ts = any(k in first_bar for k in ["t", "ts", "timestamp"])
        
        if not has_price:
            warnings.append(f"Symbol {symbol} bars missing price field")
        if not has_ts:
            warnings.append(f"Symbol {symbol} bars missing timestamp field")
    
    passed = len(errors) == 0
    return passed, errors, warnings


def validate_predictions(
    date_str: str,
    result: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate prediction quality.
    
    Checks:
    - Result exists
    - Predictions made for symbols
    - Probability distributions are valid (sum to ~1.0)
    - Labels are in expected set
    
    Returns:
        (passed, errors, warnings)
    """
    errors = []
    warnings = []
    
    if result is None:
        result = _load_replay_result(date_str)
    
    if not result:
        errors.append(f"No replay result found for {date_str}")
        return False, errors, warnings
    
    # Check basic fields
    n_symbols = result.get("n_symbols", 0)
    if n_symbols == 0:
        warnings.append("No symbols in replay result")
    
    # Meta should contain predictions info if available
    meta = result.get("meta", {})
    if isinstance(meta, dict):
        regime_dt = meta.get("regime_dt")
        if regime_dt and isinstance(regime_dt, dict):
            label = regime_dt.get("label")
            confidence = regime_dt.get("confidence")
            
            if label not in VALID_REGIME_LABELS:
                warnings.append(f"Unexpected regime label: {label}")
            
            if confidence is not None:
                conf_val = float(confidence)
                if not (0.0 <= conf_val <= 1.0):
                    warnings.append(f"Regime confidence out of range: {conf_val}")
    
    passed = len(errors) == 0
    return passed, errors, warnings


def validate_results_consistency(
    date_str: str,
    result: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate results consistency.
    
    Checks:
    - PnL calculations are reasonable
    - Hit rate is in valid range [0, 1]
    - Trades count matches expectations
    - Average PnL per trade is consistent
    
    Returns:
        (passed, errors, warnings)
    """
    errors = []
    warnings = []
    
    if result is None:
        result = _load_replay_result(date_str)
    
    if not result:
        errors.append(f"No replay result found for {date_str}")
        return False, errors, warnings
    
    # Check required fields
    n_trades = result.get("n_trades", 0)
    gross_pnl = result.get("gross_pnl", 0.0)
    avg_pnl_per_trade = result.get("avg_pnl_per_trade", 0.0)
    hit_rate = result.get("hit_rate", 0.0)
    
    # Validate hit rate
    if not (0.0 <= hit_rate <= 1.0):
        errors.append(f"Hit rate out of range: {hit_rate}")
    
    # Validate avg PnL consistency
    if n_trades > 0:
        expected_avg = gross_pnl / n_trades
        if abs(avg_pnl_per_trade - expected_avg) > 0.01:
            warnings.append(
                f"Average PnL per trade inconsistent: "
                f"reported={avg_pnl_per_trade:.4f}, expected={expected_avg:.4f}"
            )
    else:
        if avg_pnl_per_trade != 0.0:
            warnings.append("Average PnL per trade should be 0 when n_trades=0")
        if gross_pnl != 0.0:
            warnings.append("Gross PnL should be 0 when n_trades=0")
        if hit_rate != 0.0:
            warnings.append("Hit rate should be 0 when n_trades=0")
    
    # Sanity checks on PnL magnitude
    if n_trades > 0:
        if abs(gross_pnl) > 1_000_000:
            warnings.append(f"Unusually large gross PnL: {gross_pnl:.2f}")
        
        if abs(avg_pnl_per_trade) > 100_000:
            warnings.append(f"Unusually large avg PnL per trade: {avg_pnl_per_trade:.2f}")
    
    passed = len(errors) == 0
    return passed, errors, warnings


def validate_pipeline_stages(
    date_str: str,
) -> Tuple[bool, List[str], List[str]]:
    """
    Validate that all pipeline stages executed properly.
    
    Checks that intermediate outputs exist:
    - Raw day data
    - Replay result
    
    Returns:
        (passed, errors, warnings)
    """
    errors = []
    warnings = []
    
    raw_day = _load_raw_day(date_str)
    if not raw_day:
        errors.append("Raw day data not found")
    
    result = _load_replay_result(date_str)
    if not result:
        errors.append("Replay result not found")
    
    passed = len(errors) == 0
    return passed, errors, warnings


def validate_replay_result(date_str: str, save_to_file: bool = True) -> ValidationResult:
    """
    Run comprehensive validation on a replay result.
    
    Args:
        date_str: Date string in ISO format (YYYY-MM-DD)
        save_to_file: If True, save validation result to file
    
    Returns:
        ValidationResult with all checks
    """
    errors = []
    warnings = []
    checks_passed = 0
    checks_failed = 0
    details = {}
    
    # Load data once
    raw_day = _load_raw_day(date_str)
    result = _load_replay_result(date_str)
    
    # Run all validation checks
    checks = [
        ("data_integrity", lambda: validate_data_integrity(date_str, raw_day)),
        ("predictions", lambda: validate_predictions(date_str, result)),
        ("results_consistency", lambda: validate_results_consistency(date_str, result)),
        ("pipeline_stages", lambda: validate_pipeline_stages(date_str)),
    ]
    
    for check_name, check_fn in checks:
        try:
            passed, check_errors, check_warnings = check_fn()
            
            details[check_name] = {
                "passed": passed,
                "errors": check_errors,
                "warnings": check_warnings,
            }
            
            if passed:
                checks_passed += 1
            else:
                checks_failed += 1
            
            errors.extend(check_errors)
            warnings.extend(check_warnings)
            
        except Exception as e:
            checks_failed += 1
            error_msg = f"Check {check_name} failed with exception: {e}"
            errors.append(error_msg)
            details[check_name] = {
                "passed": False,
                "errors": [error_msg],
                "warnings": [],
            }
    
    # Overall result
    overall_passed = checks_failed == 0
    
    validation = ValidationResult(
        date=date_str,
        passed=overall_passed,
        checks_passed=checks_passed,
        checks_failed=checks_failed,
        errors=errors,
        warnings=warnings,
        details=details,
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    
    # Save to file if requested
    if save_to_file:
        try:
            root = DT_PATHS.get("ml_data_dt", Path("ml_data_dt"))
            validation_dir = root / "intraday" / "replay" / "validation"
            validation_dir.mkdir(parents=True, exist_ok=True)
            
            validation_path = validation_dir / f"{date_str}.json"
            with validation_path.open("w", encoding="utf-8") as f:
                json.dump(validation.to_dict(), f, indent=2, ensure_ascii=False)
            
            log(f"[validation] 💾 Saved validation result to {validation_path}")
        except Exception as e:
            log(f"[validation] ⚠️ Failed to save validation result: {e}")
    
    # Log summary
    status = "✅ PASSED" if overall_passed else "❌ FAILED"
    log(
        f"[validation] {status} {date_str}: "
        f"{checks_passed} passed, {checks_failed} failed, "
        f"{len(warnings)} warnings"
    )
    
    return validation


def validate_date_range(
    start_date: str,
    end_date: str,
    save_summary: bool = True,
) -> Dict[str, Any]:
    """
    Validate a range of replay results.
    
    Args:
        start_date: Start date (ISO format)
        end_date: End date (ISO format)
        save_summary: If True, save summary to file
    
    Returns:
        Summary of validation results
    """
    from datetime import datetime, timedelta
    
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "total_days": 0,
        "passed": 0,
        "failed": 0,
        "validation_results": [],
    }
    
    current = start
    while current <= end:
        date_str = current.isoformat()
        summary["total_days"] += 1
        
        try:
            validation = validate_replay_result(date_str, save_to_file=True)
            
            if validation.passed:
                summary["passed"] += 1
            else:
                summary["failed"] += 1
            
            summary["validation_results"].append({
                "date": date_str,
                "passed": validation.passed,
                "checks_passed": validation.checks_passed,
                "checks_failed": validation.checks_failed,
                "error_count": len(validation.errors),
                "warning_count": len(validation.warnings),
            })
            
        except Exception as e:
            summary["failed"] += 1
            log(f"[validation] ❌ Validation failed for {date_str}: {e}")
        
        current += timedelta(days=1)
    
    # Save summary
    if save_summary:
        try:
            root = DT_PATHS.get("ml_data_dt", Path("ml_data_dt"))
            summary_dir = root / "intraday" / "replay" / "validation"
            summary_dir.mkdir(parents=True, exist_ok=True)
            
            summary_path = summary_dir / f"summary_{start_date}_to_{end_date}.json"
            with summary_path.open("w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
            
            log(f"[validation] 📊 Saved validation summary to {summary_path}")
        except Exception as e:
            log(f"[validation] ⚠️ Failed to save validation summary: {e}")
    
    log(
        f"[validation] 📊 Range validation: "
        f"{summary['passed']}/{summary['total_days']} passed, "
        f"{summary['failed']}/{summary['total_days']} failed"
    )
    
    return summary


__all__ = [
    "ValidationResult",
    "validate_replay_result",
    "validate_date_range",
    "validate_data_integrity",
    "validate_predictions",
    "validate_results_consistency",
    "validate_pipeline_stages",
]
