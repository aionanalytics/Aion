# backend/services/model_performance_tracker.py
"""
Model Performance Tracker — AION Analytics

Tracks prediction accuracy vs realized returns per symbol:
- Records predictions (expected return, confidence) when made
- Records realized outcomes when available
- Calculates rolling accuracy and Sharpe ratio per symbol
- Identifies when models are underperforming (trigger retraining)

Part of the adaptive ML pipeline feedback loop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from backend.core.config import PATHS
from utils.logger import log


class ModelPerformanceTracker:
    """Track prediction accuracy vs realized returns per symbol."""
    
    def __init__(self):
        # Use da_brains directory for tracking file
        brains_root = Path(PATHS.get("brains_root", "da_brains"))
        brains_root.mkdir(parents=True, exist_ok=True)
        self.tracking_file = brains_root / "model_performance.jsonl"
    
    def record_prediction(
        self,
        symbol: str,
        horizon: str,
        prediction: float,  # Model's predicted return
        confidence: float,  # Model's confidence (0-1)
        realized_return: Optional[float] = None,  # Filled in later
        ts: Optional[str] = None,
    ):
        """
        Record a prediction. realized_return filled in later.
        
        Args:
            symbol: Stock symbol
            horizon: Prediction horizon (e.g., "1d", "5d", "20d")
            prediction: Model's predicted return (decimal, e.g., 0.05 = +5%)
            confidence: Model's confidence in prediction (0-1)
            realized_return: Actual return (filled in later when available)
            ts: Timestamp (ISO format, defaults to now)
        """
        record = {
            "type": "prediction",
            "symbol": symbol,
            "horizon": horizon,
            "prediction": prediction,
            "confidence": confidence,
            "realized_return": realized_return,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # Append to tracking file
            with open(self.tracking_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            log(f"[model_performance_tracker] ⚠️ Failed to record prediction: {e}")
    
    def record_outcome(self, symbol: str, realized_return: float, ts: str):
        """
        Update prediction with realized outcome.
        
        Args:
            symbol: Stock symbol
            realized_return: Actual return that was realized
            ts: Original prediction timestamp to match against
        """
        record = {
            "type": "outcome",
            "symbol": symbol,
            "realized_return": realized_return,
            "original_ts": ts,
            "recorded_ts": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            with open(self.tracking_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            log(f"[model_performance_tracker] ⚠️ Failed to record outcome: {e}")
    
    def get_accuracy_per_symbol(self, symbol: str, days: int = 30) -> Dict[str, float]:
        """
        Calculate prediction accuracy for a symbol.
        
        Accuracy is defined as the fraction of predictions where the
        sign (direction) matched the realized return.
        
        Args:
            symbol: Stock symbol
            days: Number of days to look back
            
        Returns:
            Dictionary with accuracy metrics:
            - accuracy: Fraction of correct direction predictions
            - predictions_total: Total predictions made
            - predictions_with_outcomes: Predictions with realized outcomes
        """
        if not self.tracking_file.exists():
            return {
                "accuracy": 0.0,
                "predictions_total": 0,
                "predictions_with_outcomes": 0,
            }
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        predictions = {}
        outcomes = {}
        
        try:
            # Parse tracking file
            with open(self.tracking_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    
                    if record.get("symbol") != symbol:
                        continue
                    
                    if record["type"] == "prediction":
                        try:
                            ts = datetime.fromisoformat(record["ts"])
                            if ts > cutoff:
                                predictions[record["ts"]] = {
                                    "pred": record["prediction"],
                                    "conf": record["confidence"],
                                }
                        except (ValueError, KeyError):
                            continue
                    
                    elif record["type"] == "outcome":
                        orig_ts = record.get("original_ts")
                        if orig_ts in predictions:
                            outcomes[orig_ts] = record["realized_return"]
            
            # Calculate accuracy
            correct = 0
            total = 0
            
            for ts, pred_data in predictions.items():
                if ts in outcomes:
                    pred = pred_data["pred"]
                    real = outcomes[ts]
                    
                    # Correct if sign matches (predicted up/down, realized up/down)
                    if (pred > 0 and real > 0) or (pred < 0 and real < 0):
                        correct += 1
                    
                    total += 1
            
            return {
                "accuracy": correct / max(total, 1),
                "predictions_total": len(predictions),
                "predictions_with_outcomes": total,
            }
            
        except Exception as e:
            log(f"[model_performance_tracker] ⚠️ Failed to calculate accuracy: {e}")
            return {
                "accuracy": 0.0,
                "predictions_total": 0,
                "predictions_with_outcomes": 0,
            }
    
    def get_rolling_sharpe(self, symbol: str, days: int = 3) -> float:
        """
        Calculate rolling Sharpe ratio over last N days.
        
        Sharpe ratio measures risk-adjusted returns:
        sharpe = (mean_return / std_return) * sqrt(252)
        
        Args:
            symbol: Stock symbol
            days: Number of days to look back
            
        Returns:
            Sharpe ratio, or 0.0 if insufficient data
        """
        if not HAS_NUMPY:
            log("[model_performance_tracker] ⚠️ NumPy not available, cannot calculate Sharpe")
            return 0.0
        
        if not self.tracking_file.exists():
            return 0.0
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        returns = []
        
        try:
            with open(self.tracking_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    
                    if record.get("symbol") == symbol and record["type"] == "outcome":
                        try:
                            ts = datetime.fromisoformat(record["recorded_ts"])
                            if ts > cutoff:
                                returns.append(record["realized_return"])
                        except (ValueError, KeyError):
                            continue
            
            if len(returns) < 2:
                return 0.0
            
            # Calculate Sharpe (assuming 252 trading days)
            mean_return = np.mean(returns)
            std_return = np.std(returns)
            
            if std_return == 0:
                return 0.0
            
            sharpe = (mean_return / std_return) * np.sqrt(252)
            return float(sharpe)
            
        except Exception as e:
            log(f"[model_performance_tracker] ⚠️ Failed to calculate Sharpe: {e}")
            return 0.0
