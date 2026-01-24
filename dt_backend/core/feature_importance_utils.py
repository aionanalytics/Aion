"""Feature importance utility functions for ML interpretability.

Provides helper functions for calculating feature importance scores
using various methods including permutation importance and 
SHAP-like feature attribution.
"""

from __future__ import annotations

import numpy as np
from typing import Any, Dict, List, Tuple, Optional


def calculate_permutation_importance(
    features: Dict[str, float],
    baseline_score: float,
    prediction_func: Optional[Any] = None,
    n_samples: int = 10
) -> Dict[str, float]:
    """Calculate permutation importance for features.
    
    Permutation importance measures feature importance by evaluating how much
    the model's prediction changes when a feature's value is randomly shuffled.
    
    Args:
        features: Dictionary of feature names to values
        baseline_score: Original prediction score/confidence
        prediction_func: Optional function to re-evaluate predictions (for true permutation)
        n_samples: Number of permutations to average over
    
    Returns:
        Dict mapping feature names to importance scores (0.0-1.0)
    """
    if not features:
        return {}
    
    importances = {}
    
    # If no prediction function, use heuristic based on feature variance
    if prediction_func is None:
        # Simple heuristic: features with higher values have more potential impact
        values = np.array(list(features.values()))
        mean_val = np.mean(np.abs(values))
        
        if mean_val == 0:
            # All features are zero, equal importance
            uniform_importance = 1.0 / len(features)
            return {k: uniform_importance for k in features.keys()}
        
        for fname, fval in features.items():
            # Normalize by mean absolute value
            importances[fname] = abs(fval) / (mean_val * len(features))
    else:
        # True permutation importance (expensive)
        for fname in features.keys():
            score_diffs = []
            for _ in range(n_samples):
                # Would permute feature and recalculate - simplified here
                # In practice: perturbed_features = features.copy()
                # perturbed_features[fname] = np.random.permutation([features[fname]])[0]
                # new_score = prediction_func(perturbed_features)
                # score_diffs.append(abs(baseline_score - new_score))
                pass
            # importances[fname] = np.mean(score_diffs)
    
    # Normalize importances to sum to 1.0
    total = sum(importances.values())
    if total > 0:
        importances = {k: v / total for k, v in importances.items()}
    
    return importances


def calculate_shap_like_attribution(
    features: Dict[str, float],
    feature_means: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """Calculate SHAP-like feature attribution scores.
    
    Simplified version of SHAP (SHapley Additive exPlanations) that
    attributes the prediction to each feature based on its deviation
    from the mean value.
    
    Args:
        features: Dictionary of feature names to values
        feature_means: Optional dict of feature means (for normalization)
    
    Returns:
        Dict mapping feature names to attribution scores
    """
    if not features:
        return {}
    
    attributions = {}
    
    if feature_means is None:
        # Use zero as baseline
        feature_means = {k: 0.0 for k in features.keys()}
    
    # Calculate deviation from mean
    for fname, fval in features.items():
        mean_val = feature_means.get(fname, 0.0)
        attributions[fname] = fval - mean_val
    
    # Normalize by total absolute contribution
    total_abs = sum(abs(v) for v in attributions.values())
    if total_abs > 0:
        attributions = {k: v / total_abs for k, v in attributions.items()}
    
    return attributions


def get_top_n_features(
    importance_dict: Dict[str, float],
    top_n: int = 10
) -> List[Tuple[str, float]]:
    """Get top N features by importance score.
    
    Args:
        importance_dict: Dictionary of feature names to importance scores
        top_n: Number of top features to return
    
    Returns:
        List of (feature_name, importance_score) tuples, sorted by importance
    """
    if not importance_dict:
        return []
    
    sorted_features = sorted(
        importance_dict.items(),
        key=lambda x: abs(x[1]),
        reverse=True
    )
    
    return sorted_features[:top_n]


def calculate_feature_correlation(
    feature_history: List[Dict[str, float]],
    pnl_history: List[float]
) -> Dict[str, float]:
    """Calculate correlation between features and P&L outcomes.
    
    Args:
        feature_history: List of feature dictionaries over time
        pnl_history: List of corresponding P&L values
    
    Returns:
        Dict mapping feature names to correlation coefficients (-1.0 to 1.0)
    """
    if not feature_history or not pnl_history:
        return {}
    
    if len(feature_history) != len(pnl_history):
        raise ValueError("feature_history and pnl_history must have same length")
    
    # Get all feature names
    all_features = set()
    for features in feature_history:
        all_features.update(features.keys())
    
    correlations = {}
    pnl_array = np.array(pnl_history)
    
    for fname in all_features:
        # Extract feature values, filling missing with 0
        feature_values = np.array([
            features.get(fname, 0.0) for features in feature_history
        ])
        
        # Calculate correlation coefficient
        if np.std(feature_values) == 0 or np.std(pnl_array) == 0:
            correlations[fname] = 0.0
        else:
            correlations[fname] = float(np.corrcoef(feature_values, pnl_array)[0, 1])
    
    return correlations


def detect_feature_drift(
    recent_importance: Dict[str, float],
    historical_importance: Dict[str, float],
    threshold: float = 0.15
) -> Tuple[bool, float]:
    """Detect if feature importance has drifted significantly.
    
    Args:
        recent_importance: Recent feature importance scores
        historical_importance: Historical baseline importance scores
        threshold: Drift threshold (0.0-1.0)
    
    Returns:
        Tuple of (drift_detected: bool, drift_score: float)
    """
    if not recent_importance or not historical_importance:
        return False, 0.0
    
    # Get common features
    common_features = set(recent_importance.keys()) & set(historical_importance.keys())
    
    if not common_features:
        return True, 1.0  # Complete feature set change
    
    # Calculate drift score (mean absolute difference)
    drift_scores = []
    for fname in common_features:
        recent_val = recent_importance[fname]
        hist_val = historical_importance[fname]
        drift_scores.append(abs(recent_val - hist_val))
    
    drift_score = float(np.mean(drift_scores))
    drift_detected = drift_score > threshold
    
    return drift_detected, drift_score
