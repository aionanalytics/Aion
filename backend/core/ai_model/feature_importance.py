# backend/core/ai_model/feature_importance.py
"""
Feature Importance Tracker — AION Analytics

Computes and stores feature importance post-training to:
- Identify which features actually contribute to predictions
- Drop low-variance features from next training
- Track feature importance evolution over time

Part of the adaptive ML pipeline feedback loop.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from backend.core.config import PATHS
from utils.logger import log


class FeatureImportanceTracker:
    """Compute and store feature importance post-training."""
    
    def __init__(self):
        ml_data = PATHS.get("ml_data", Path("ml_data"))
        self.importance_dir = Path(ml_data) / "feature_importance"
        self.importance_dir.mkdir(parents=True, exist_ok=True)
    
    def compute_importance(
        self,
        model,  # Trained LightGBM or sklearn model
        feature_names: List[str],
        horizon: str,  # "1d", "5d", "20d", etc.
        top_n: int = 20
    ) -> Dict[str, float]:
        """
        Extract feature importance from trained model.
        
        Args:
            model: Trained model with feature_importances_ attribute
            feature_names: List of feature names in training order
            horizon: Prediction horizon (e.g., "1d", "5d", "20d")
            top_n: Number of top features to store
            
        Returns:
            Dictionary of top features and their importance scores
        """
        try:
            # Get feature importance (works for LightGBM and sklearn models)
            if hasattr(model, 'feature_importances_'):
                importance = model.feature_importances_
            else:
                log(f"[feature_importance] ⚠️ Model for {horizon} has no feature_importances_")
                return {}
            
            # Create feature importance dictionary
            feature_importance_dict = {
                name: float(imp)
                for name, imp in zip(feature_names, importance)
            }
            
            # Sort and keep top N
            sorted_features = sorted(
                feature_importance_dict.items(),
                key=lambda x: x[1],
                reverse=True
            )
            top_features = {name: imp for name, imp in sorted_features[:top_n]}
            
            # Save to disk
            self._save_importance(horizon, top_features)
            
            log(f"[feature_importance] ✅ Saved top {len(top_features)} features for {horizon}")
            
            return top_features
            
        except Exception as e:
            log(f"[feature_importance] ⚠️ Failed to compute importance for {horizon}: {e}")
            return {}
    
    def _save_importance(self, horizon: str, importance: Dict[str, float]):
        """Save feature importance to file."""
        file_path = self.importance_dir / f"importance_{horizon}.json"
        
        data = {
            "horizon": horizon,
            "features": importance,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log(f"[feature_importance] ⚠️ Failed to save importance for {horizon}: {e}")
    
    def get_top_features(self, horizon: str, top_n: int = 20) -> List[str]:
        """
        Load top features for a horizon.
        
        Args:
            horizon: Prediction horizon
            top_n: Maximum number of features to return
            
        Returns:
            List of top feature names, or empty list if not available
        """
        file_path = self.importance_dir / f"importance_{horizon}.json"
        
        if not file_path.exists():
            return []  # Fallback: use all features
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                features = data.get("features", {})
                return list(features.keys())[:top_n]
        except Exception as e:
            log(f"[feature_importance] ⚠️ Failed to load importance for {horizon}: {e}")
            return []
    
    def get_low_importance_features(
        self,
        horizon: str,
        threshold: float = 0.01
    ) -> List[str]:
        """
        Get features with importance below threshold.
        
        These are candidates for removal in next training cycle.
        
        Args:
            horizon: Prediction horizon
            threshold: Minimum importance threshold
            
        Returns:
            List of low-importance feature names
        """
        file_path = self.importance_dir / f"importance_{horizon}.json"
        
        if not file_path.exists():
            return []
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                features = data.get("features", {})
                
                low_features = [
                    name for name, imp in features.items()
                    if imp < threshold
                ]
                
                return low_features
        except Exception as e:
            log(f"[feature_importance] ⚠️ Failed to get low importance features for {horizon}: {e}")
            return []
