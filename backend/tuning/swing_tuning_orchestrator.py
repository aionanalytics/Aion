"""backend.tuning.swing_tuning_orchestrator — Tuning Orchestrator

Orchestrates the entire autonomous tuning pipeline.

Execution Flow:
1. Load recent trade outcomes (30-day window)
2. Update P(hit) calibration from outcomes
3. Run threshold optimizer for each regime
4. Run position sizing tuner
5. Run exit strategy optimizer
6. Validate all proposed changes
7. Apply approved changes to configs.json
8. Log tuning decisions with full audit trail
9. Handle rollback if performance degrades

Schedule:
- Runs nightly (configurable via SWING_TUNING_SCHEDULE)
- Per-bot tuning (1w, 2w, 4w separately)
- Phase-based rollout via feature flags

Environment Variables:
  SWING_TUNING_ENABLED (default: false)
  SWING_TUNING_SCHEDULE (default: "22:00")
  SWING_TUNING_PHASE (default: "logging_only")
    Options: "logging_only", "calibration", "threshold", "position", "exit", "full"
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.swing_outcome_logger import load_recent_outcomes, get_outcome_statistics
from backend.calibration.phit_calibrator_swing import update_calibration_from_outcomes
from backend.tuning.swing_threshold_optimizer import ThresholdOptimizer
from backend.tuning.swing_position_tuner import PositionTuner
from backend.tuning.swing_exit_optimizer import ExitOptimizer
from backend.tuning.swing_tuning_validator import TuningValidator, TuningDecision

try:
    from backend.core.data_pipeline import log  # type: ignore
except Exception:  # pragma: no cover
    def log(msg: str) -> None:  # type: ignore
        print(msg)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.getenv(name, "") or "").strip()
        return int(float(raw)) if raw else int(default)
    except Exception:
        return int(default)


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name, "") or default).strip()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _tuning_history_path() -> Path:
    """Return path to tuning history log."""
    try:
        from config import PATHS  # type: ignore
        da = PATHS.get("da_brains")
        if da:
            return Path(da) / "swing" / "tuning_history.jsonl"
    except Exception:
        pass
    return Path("da_brains") / "swing" / "tuning_history.jsonl"


def _bot_configs_path() -> Path:
    """Return path to bot configs."""
    try:
        from config import PATHS  # type: ignore
        cache = PATHS.get("stock_cache")
        if cache:
            return Path(cache) / "master" / "bot" / "configs.json"
    except Exception:
        pass
    return Path("ml_data") / "config" / "bots_config.json"


def load_bot_configs() -> Dict[str, Any]:
    """Load bot configurations."""
    try:
        path = _bot_configs_path()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log(f"[orchestrator] Failed to load bot configs: {e}")
    return {}


def save_bot_configs(configs: Dict[str, Any]) -> bool:
    """Save bot configurations."""
    try:
        path = _bot_configs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Atomic write
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(configs, f, indent=2)
        tmp.replace(path)
        
        log(f"[orchestrator] Saved bot configs to {path}")
        return True
    except Exception as e:
        log(f"[orchestrator] Failed to save bot configs: {e}")
        return False


def append_tuning_decision(decision: TuningDecision) -> None:
    """Append tuning decision to history log."""
    try:
        path = _tuning_history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"[orchestrator] Failed to log tuning decision: {e}")


class TuningOrchestrator:
    """Orchestrates autonomous tuning pipeline."""
    
    def __init__(
        self,
        enabled: Optional[bool] = None,
        phase: Optional[str] = None,
        window_days: Optional[int] = None
    ):
        """
        Initialize tuning orchestrator.
        
        Args:
            enabled: Enable tuning (default: from SWING_TUNING_ENABLED)
            phase: Tuning phase (default: from SWING_TUNING_PHASE)
            window_days: Days of data to analyze
        """
        self.enabled = enabled if enabled is not None else _env_bool("SWING_TUNING_ENABLED", False)
        self.phase = phase or _env_str("SWING_TUNING_PHASE", "logging_only")
        self.window_days = window_days or _env_int("SWING_TUNING_WINDOW_DAYS", 30)
        
        # Initialize tuners
        self.validator = TuningValidator()
        self.threshold_optimizer = ThresholdOptimizer(
            window_days=self.window_days,
            validator=self.validator
        )
        self.position_tuner = PositionTuner(
            window_days=self.window_days,
            validator=self.validator
        )
        self.exit_optimizer = ExitOptimizer(
            window_days=self.window_days,
            validator=self.validator
        )
    
    def run_tuning_for_bot(
        self,
        bot_key: str,
        current_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run tuning pipeline for a single bot.
        
        Args:
            bot_key: Bot identifier (swing_1w, swing_2w, swing_4w)
            current_config: Current bot configuration
        
        Returns:
            Updated configuration with tuning applied
        """
        if not self.enabled:
            log(f"[orchestrator] Tuning disabled for {bot_key}")
            return current_config
        
        log(f"[orchestrator] ===== Running tuning for {bot_key} (phase: {self.phase}) =====")
        
        # Load recent outcomes
        outcomes = load_recent_outcomes(bot_key=bot_key, days=self.window_days)
        
        if not outcomes:
            log(f"[orchestrator] No outcomes found for {bot_key}")
            return current_config
        
        stats = get_outcome_statistics(bot_key=bot_key, days=self.window_days)
        log(f"[orchestrator] {bot_key}: {stats['total_trades']} trades, "
            f"Sharpe={stats['sharpe_ratio']:.2f}, win_rate={stats['win_rate']:.1%}")
        
        # Phase 1: Outcome logging only (always runs)
        if self.phase == "logging_only":
            log(f"[orchestrator] Phase: logging_only - no tuning applied")
            return current_config
        
        # Phase 2: P(hit) calibration
        if self.phase in ["calibration", "threshold", "position", "exit", "full"]:
            log(f"[orchestrator] Updating P(hit) calibration from {len(outcomes)} outcomes")
            update_calibration_from_outcomes(bot_key=bot_key, outcomes=outcomes)
        
        # Create updated config (start with copy)
        updated_config = current_config.copy()
        
        # Phase 3: Threshold optimization
        if self.phase in ["threshold", "full"]:
            self._run_threshold_tuning(bot_key, updated_config, outcomes)
        
        # Phase 4: Position sizing
        if self.phase in ["position", "full"]:
            self._run_position_tuning(bot_key, updated_config, outcomes)
        
        # Phase 5: Exit strategy
        if self.phase in ["exit", "full"]:
            self._run_exit_tuning(bot_key, updated_config, outcomes)
        
        return updated_config
    
    def _run_threshold_tuning(
        self,
        bot_key: str,
        config: Dict[str, Any],
        outcomes: List[Dict[str, Any]]
    ) -> None:
        """Run confidence threshold tuning."""
        log(f"[orchestrator] Running threshold optimization...")
        
        # Get current thresholds (may be regime-specific)
        current_threshold = config.get("conf_threshold", 0.55)
        
        # Optimize for each regime
        regimes = ["bull", "bear", "chop", "stress"]
        
        for regime in regimes:
            result = self.threshold_optimizer.optimize_threshold_for_regime(
                bot_key=bot_key,
                regime=regime,
                current_threshold=current_threshold
            )
            
            if result and result["validation"].approved:
                # Apply change
                new_threshold = result["new_threshold"]
                
                # Store regime-specific threshold if enabled
                if config.get("per_regime_tuning", True):
                    if "regime_thresholds" not in config:
                        config["regime_thresholds"] = {}
                    config["regime_thresholds"][regime] = new_threshold
                else:
                    # Apply globally
                    config["conf_threshold"] = new_threshold
                
                # Log decision
                decision = TuningDecision(
                    bot_key=bot_key,
                    regime=regime,
                    decision_ts=_utc_iso(),
                    phase="threshold_optimization",
                    parameter="conf_threshold",
                    old_value=result["old_threshold"],
                    new_value=new_threshold,
                    improvement_pct=result["improvement_pct"],
                    sharpe_old=result["old_sharpe"],
                    sharpe_new=result["new_sharpe"],
                    confidence_interval=result["validation"].confidence_interval or (0, 0),
                    trades_analyzed=result["trades_analyzed"],
                    applied=True,
                    reason=result["validation"].reason
                )
                append_tuning_decision(decision)
    
    def _get_current_regime(self, outcomes: List[Dict[str, Any]]) -> str:
        """
        Determine current market regime from recent outcomes.
        
        Args:
            outcomes: Recent trade outcomes
        
        Returns:
            Current regime (bull/bear/chop/stress)
        """
        if not outcomes:
            return "bull"  # Default fallback
        
        # Use most recent outcome's exit regime as current regime
        recent_outcomes = sorted(outcomes, key=lambda o: o.get("exit_ts", ""), reverse=True)
        if recent_outcomes:
            return recent_outcomes[0].get("regime_exit", "bull")
        
        return "bull"
    
    def _run_position_tuning(
        self,
        bot_key: str,
        config: Dict[str, Any],
        outcomes: List[Dict[str, Any]]
    ) -> None:
        """Run position sizing tuning."""
        log(f"[orchestrator] Running position sizing optimization...")
        
        current_params = {
            "starter_fraction": config.get("starter_fraction", 0.35),
            "max_weight_per_name": config.get("max_weight_per_name", 0.15)
        }
        
        # Get current regime from recent outcomes
        regime = self._get_current_regime(outcomes)
        
        results = self.position_tuner.optimize_all_sizing(
            bot_key=bot_key,
            regime=regime,
            current_params=current_params
        )
        
        # Apply approved changes
        for param_name, result in results.items():
            if result and result["validation"].approved:
                config[param_name] = result["new_value"]
                
                # Log decision
                decision = TuningDecision(
                    bot_key=bot_key,
                    regime=regime,
                    decision_ts=_utc_iso(),
                    phase="position_optimization",
                    parameter=param_name,
                    old_value=result["old_value"],
                    new_value=result["new_value"],
                    improvement_pct=result["improvement_pct"],
                    sharpe_old=result["old_sharpe"],
                    sharpe_new=result["new_sharpe"],
                    confidence_interval=result["validation"].confidence_interval or (0, 0),
                    trades_analyzed=result["trades_analyzed"],
                    applied=True,
                    reason=result["validation"].reason
                )
                append_tuning_decision(decision)
    
    def _run_exit_tuning(
        self,
        bot_key: str,
        config: Dict[str, Any],
        outcomes: List[Dict[str, Any]]
    ) -> None:
        """Run exit strategy tuning."""
        log(f"[orchestrator] Running exit strategy optimization...")
        
        current_params = {
            "stop_loss_pct": config.get("stop_loss_pct", -0.05),
            "take_profit_pct": config.get("take_profit_pct", 0.10)
        }
        
        # Get current regime from recent outcomes
        regime = self._get_current_regime(outcomes)
        
        results = self.exit_optimizer.optimize_all_exits(
            bot_key=bot_key,
            regime=regime,
            current_params=current_params
        )
        
        # Apply approved changes
        for param_name, result in results.items():
            if result and result["validation"].approved:
                config[param_name] = result["new_value"]
                
                # Log decision
                decision = TuningDecision(
                    bot_key=bot_key,
                    regime=regime,
                    decision_ts=_utc_iso(),
                    phase="exit_optimization",
                    parameter=param_name,
                    old_value=result["old_value"],
                    new_value=result["new_value"],
                    improvement_pct=result["improvement_pct"],
                    sharpe_old=result["old_sharpe"],
                    sharpe_new=result["new_sharpe"],
                    confidence_interval=result["validation"].confidence_interval or (0, 0),
                    trades_analyzed=result["trades_analyzed"],
                    applied=True,
                    reason=result["validation"].reason
                )
                append_tuning_decision(decision)
    
    def run_nightly_tuning(self) -> Dict[str, Any]:
        """
        Run nightly tuning for all bots.
        
        Returns:
            Dict with tuning summary
        """
        if not self.enabled:
            log(f"[orchestrator] Tuning disabled (SWING_TUNING_ENABLED=false)")
            return {"enabled": False, "bots_tuned": 0}
        
        log(f"[orchestrator] ===== Starting Nightly Tuning (phase: {self.phase}) =====")
        
        # Load current configs
        configs = load_bot_configs()
        
        bots_tuned = 0
        
        # Tune each bot separately
        for bot_key in ["swing_1w", "swing_2w", "swing_4w"]:
            if bot_key in configs:
                bot_config = configs[bot_key]
                
                # Check if tuning enabled for this bot
                if not bot_config.get("tuning_enabled", True):
                    log(f"[orchestrator] Tuning disabled for {bot_key}")
                    continue
                
                updated_config = self.run_tuning_for_bot(bot_key, bot_config)
                configs[bot_key] = updated_config
                bots_tuned += 1
        
        # Save updated configs
        if bots_tuned > 0:
            save_bot_configs(configs)
        
        log(f"[orchestrator] ===== Nightly Tuning Complete: {bots_tuned} bots tuned =====")
        
        return {
            "enabled": True,
            "phase": self.phase,
            "bots_tuned": bots_tuned,
            "timestamp": _utc_iso()
        }


def run_nightly_tuning() -> Dict[str, Any]:
    """
    Convenience function to run nightly tuning.
    
    Returns:
        Tuning summary
    """
    orchestrator = TuningOrchestrator()
    return orchestrator.run_nightly_tuning()
