# backend/core/ai_model/regime_features.py
"""
Regime-Specific Feature Selector — AION Analytics

Selects appropriate feature sets based on market regime:
- Trending markets: Use momentum, RSI, moving average features
- Range/choppy markets: Use mean reversion, Bollinger Bands, oscillators
- Volatile markets: Use ATR, volatility, reversal patterns

This allows the model to focus on relevant signals per regime,
improving prediction quality and reducing noise.

Part of the adaptive ML pipeline feedback loop.
"""

from __future__ import annotations

from typing import Dict, List


class RegimeFeatureSelector:
    """Select features based on market regime."""
    
    # Feature groups by type
    # These match common technical indicators used in feature engineering
    
    MOMENTUM_FEATURES = [
        "rsi_14",
        "rsi_7",
        "macd",
        "macd_signal",
        "macd_hist",
        "ema_5_cross",
        "ema_10_cross",
        "momentum_20",
        "momentum_50",
        "roc_14",
    ]
    
    MEAN_REVERSION_FEATURES = [
        "bb_width",
        "bb_position",
        "bb_upper",
        "bb_lower",
        "stoch_k",
        "stoch_d",
        "williams_r",
        "cci",
        "mean_reversion_score",
    ]
    
    VOLATILITY_FEATURES = [
        "atr_14",
        "atr_20",
        "true_range",
        "parkinson_vol",
        "garman_klass_vol",
        "volatility_ratio",
        "historical_vol",
        "rolling_std",
    ]
    
    TREND_FEATURES = [
        "slope_20",
        "slope_50",
        "slope_200",
        "ema_20_50_diff",
        "sma_50_200_cross",
        "adx",
        "adx_pos",
        "adx_neg",
        "trend_strength",
    ]
    
    BASE_FEATURES = [
        "volume",
        "volume_ratio",
        "vwap",
        "vwap_distance",
        "price_momentum",
        "log_return",
        "returns_1d",
        "returns_5d",
    ]
    
    # Regime-specific feature sets
    # Each regime gets a tailored combination of feature groups
    REGIME_SETS = {
        "trending_up": TREND_FEATURES + MOMENTUM_FEATURES + BASE_FEATURES,
        "trending_down": TREND_FEATURES + MOMENTUM_FEATURES + BASE_FEATURES,
        "bull": TREND_FEATURES + MOMENTUM_FEATURES + BASE_FEATURES,
        "bear": TREND_FEATURES + MOMENTUM_FEATURES + VOLATILITY_FEATURES + BASE_FEATURES,
        "range": MEAN_REVERSION_FEATURES + BASE_FEATURES + VOLATILITY_FEATURES[:3],
        "chop": MEAN_REVERSION_FEATURES + BASE_FEATURES + VOLATILITY_FEATURES[:3],
        "volatile": VOLATILITY_FEATURES + BASE_FEATURES + MEAN_REVERSION_FEATURES[:3],
        "panic": VOLATILITY_FEATURES + BASE_FEATURES + TREND_FEATURES[:3],
        "transitioning": TREND_FEATURES + MOMENTUM_FEATURES + MEAN_REVERSION_FEATURES + BASE_FEATURES,
    }
    
    @staticmethod
    def get_features_for_regime(regime: str) -> List[str]:
        """
        Get feature set for current regime.
        
        Args:
            regime: Market regime label (e.g., "bull", "bear", "chop", "panic")
            
        Returns:
            List of feature names appropriate for this regime.
            Falls back to "transitioning" (all features) if regime not recognized.
        """
        regime_lower = regime.lower() if regime else "transitioning"
        
        # Get regime-specific features or default to transitioning
        features = RegimeFeatureSelector.REGIME_SETS.get(
            regime_lower,
            RegimeFeatureSelector.REGIME_SETS["transitioning"]
        )
        
        # Return a copy to avoid mutation
        return list(features)
    
    @staticmethod
    def get_available_regimes() -> List[str]:
        """Get list of all supported regime labels."""
        return list(RegimeFeatureSelector.REGIME_SETS.keys())
    
    @staticmethod
    def get_feature_groups() -> Dict[str, List[str]]:
        """
        Get all feature groups.
        
        Returns:
            Dictionary mapping group names to feature lists
        """
        return {
            "momentum": list(RegimeFeatureSelector.MOMENTUM_FEATURES),
            "mean_reversion": list(RegimeFeatureSelector.MEAN_REVERSION_FEATURES),
            "volatility": list(RegimeFeatureSelector.VOLATILITY_FEATURES),
            "trend": list(RegimeFeatureSelector.TREND_FEATURES),
            "base": list(RegimeFeatureSelector.BASE_FEATURES),
        }
