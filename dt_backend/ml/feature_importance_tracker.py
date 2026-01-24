"""Feature importance tracker for ML interpretability and drift detection.

Tracks which features drive trading decisions and detects feature drift
that might indicate the need for model retraining.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from dt_backend.core.config_dt import DT_PATHS
    ML_DATA_DIR = Path(DT_PATHS.get("dtml_data", "ml_data_dt"))
except Exception:
    ML_DATA_DIR = Path("ml_data_dt")

from dt_backend.core.feature_importance_utils import (
    calculate_permutation_importance,
    get_top_n_features,
    detect_feature_drift,
)

try:
    from dt_backend.core.logger_dt import log
except Exception:
    def log(msg: str) -> None:
        print(msg, flush=True)


class FeatureImportanceTracker:
    """Track which features drive trading decisions."""
    
    def __init__(self, ml_data_dir: Optional[Path] = None):
        """Initialize feature importance tracker.
        
        Args:
            ml_data_dir: Directory for storing feature importance data.
                        Defaults to ml_data_dt/
        """
        self.ml_data_dir = ml_data_dir or ML_DATA_DIR
        self.ml_data_dir.mkdir(parents=True, exist_ok=True)
        
        # File paths
        self.importance_log_path = self.ml_data_dir / "feature_importance.jsonl"
        self.stats_path = self.ml_data_dir / "feature_importance_stats.json"
        self.drift_alerts_path = self.ml_data_dir / "feature_drift_alerts.json"
        
        # In-memory cache
        self._recent_importances: List[Dict[str, Any]] = []
        self._baseline_importance: Optional[Dict[str, float]] = None
        
        # Load baseline if exists
        self._load_baseline()
    
    def log_prediction(
        self,
        symbol: str,
        features_dict: Dict[str, Any],
        prediction: str,
        confidence: float,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log prediction with feature importance.
        
        Calculates which features most influenced:
        - BUY/SELL decision
        - Confidence score
        - Trade gate
        
        Args:
            symbol: Trading symbol (e.g., "AAPL")
            features_dict: Dictionary of feature names to values
            prediction: Trading action ("BUY", "SELL", "HOLD")
            confidence: Confidence score (0.0-1.0)
            metadata: Optional additional metadata
        """
        if not features_dict:
            return
        
        # Convert all feature values to float
        features_float = {}
        for key, value in features_dict.items():
            try:
                features_float[key] = float(value)
            except (ValueError, TypeError):
                continue
        
        if not features_float:
            return
        
        # Calculate feature importance
        importance_scores = calculate_permutation_importance(
            features_float,
            baseline_score=confidence
        )
        
        # Get top 10 features
        top_features = get_top_n_features(importance_scores, top_n=10)
        
        # Create log entry
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "prediction": prediction,
            "confidence": confidence,
            "top_features": [
                {"name": fname, "importance": score}
                for fname, score in top_features
            ],
            "num_features": len(features_float),
        }
        
        if metadata:
            log_entry["metadata"] = metadata
        
        # Append to log file
        self._append_to_log(log_entry)
        
        # Update in-memory cache
        self._recent_importances.append({
            "symbol": symbol,
            "importance": importance_scores,
            "timestamp": log_entry["timestamp"]
        })
        
        # Keep only recent history (last 100 entries)
        if len(self._recent_importances) > 100:
            self._recent_importances = self._recent_importances[-100:]
    
    def get_top_features(
        self,
        symbol: Optional[str] = None,
        top_n: int = 10
    ) -> List[Tuple[str, float]]:
        """Return top N features by importance for symbol or overall.
        
        Args:
            symbol: Optional symbol to filter by. If None, returns overall.
            top_n: Number of top features to return
        
        Returns:
            List of (feature_name, importance_score) tuples
        """
        # Load recent data if needed
        if not self._recent_importances:
            self._load_recent_data()
        
        # Filter by symbol if specified
        if symbol:
            relevant = [
                entry["importance"]
                for entry in self._recent_importances
                if entry["symbol"] == symbol
            ]
        else:
            relevant = [entry["importance"] for entry in self._recent_importances]
        
        if not relevant:
            return []
        
        # Aggregate importance scores
        aggregated = defaultdict(list)
        for importance_dict in relevant:
            for fname, score in importance_dict.items():
                aggregated[fname].append(score)
        
        # Calculate mean importance
        mean_importance = {
            fname: float(np.mean(scores))
            for fname, scores in aggregated.items()
        }
        
        return get_top_n_features(mean_importance, top_n=top_n)
    
    def detect_drift(self, threshold: float = 0.15) -> bool:
        """Check if feature importance has shifted significantly.
        
        Args:
            threshold: Drift threshold (0.0-1.0). Higher = more tolerant.
        
        Returns:
            True if drift detected, False otherwise
        """
        # Load recent data if needed
        if not self._recent_importances:
            self._load_recent_data()
        
        # Need baseline for comparison
        if self._baseline_importance is None:
            log("[feature_importance] No baseline importance found, cannot detect drift")
            return False
        
        # Calculate recent average importance
        if not self._recent_importances:
            return False
        
        recent_scores = defaultdict(list)
        for entry in self._recent_importances[-20:]:  # Last 20 entries
            for fname, score in entry["importance"].items():
                recent_scores[fname].append(score)
        
        recent_importance = {
            fname: float(np.mean(scores))
            for fname, scores in recent_scores.items()
        }
        
        # Detect drift
        drift_detected, drift_score = detect_feature_drift(
            recent_importance,
            self._baseline_importance,
            threshold=threshold
        )
        
        # Log drift detection
        drift_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "drift_detected": drift_detected,
            "drift_score": drift_score,
            "threshold": threshold,
            "recent_importance": recent_importance,
            "baseline_importance": self._baseline_importance,
        }
        
        # Save drift alert
        with self.drift_alerts_path.open("w") as f:
            json.dump(drift_data, f, indent=2)
        
        if drift_detected:
            log(f"[feature_importance] ⚠️ Feature drift detected! Score: {drift_score:.3f} (threshold: {threshold})")
        
        return drift_detected
    
    def update_stats(self) -> Dict[str, Any]:
        """Update feature importance statistics.
        
        Returns:
            Dict containing statistics about feature importance
        """
        # Get overall top features
        top_features = self.get_top_features(top_n=20)
        
        # Calculate stats
        stats = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_predictions": len(self._recent_importances),
            "top_features": [
                {"name": fname, "importance": score}
                for fname, score in top_features
            ],
        }
        
        # Save stats
        with self.stats_path.open("w") as f:
            json.dump(stats, f, indent=2)
        
        return stats
    
    def _append_to_log(self, entry: Dict[str, Any]) -> None:
        """Append entry to JSONL log file."""
        with self.importance_log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    
    def _load_recent_data(self, max_entries: int = 100) -> None:
        """Load recent entries from log file."""
        if not self.importance_log_path.exists():
            return
        
        try:
            with self.importance_log_path.open("r") as f:
                # Read last N lines
                lines = f.readlines()
                recent_lines = lines[-max_entries:]
            
            self._recent_importances = []
            for line in recent_lines:
                try:
                    entry = json.loads(line)
                    # Reconstruct importance dict from top features
                    importance = {
                        feat["name"]: feat["importance"]
                        for feat in entry.get("top_features", [])
                    }
                    self._recent_importances.append({
                        "symbol": entry["symbol"],
                        "importance": importance,
                        "timestamp": entry["timestamp"]
                    })
                except json.JSONDecodeError:
                    continue
        
        except Exception as e:
            log(f"[feature_importance] Warning: Failed to load recent data: {e}")
    
    def _load_baseline(self) -> None:
        """Load baseline importance from stats file."""
        if not self.stats_path.exists():
            return
        
        try:
            with self.stats_path.open("r") as f:
                stats = json.load(f)
            
            # Extract baseline importance from top features
            self._baseline_importance = {
                feat["name"]: feat["importance"]
                for feat in stats.get("top_features", [])
            }
        
        except Exception as e:
            log(f"[feature_importance] Warning: Failed to load baseline: {e}")


# Singleton instance for easy access
_tracker_instance: Optional[FeatureImportanceTracker] = None


def get_tracker() -> FeatureImportanceTracker:
    """Get singleton feature importance tracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = FeatureImportanceTracker()
    return _tracker_instance
