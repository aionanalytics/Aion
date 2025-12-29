"""dt_backend.ml

Lazy-loaded intraday ML utilities.

Public API (import-safe):
- build_intraday_dataset
- train_intraday_models
- score_intraday_tickers
- build_intraday_signals
- train_incremental_intraday

This file intentionally avoids importing submodules at module load.
"""

from __future__ import annotations

from typing import Any, Dict


def build_intraday_dataset(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build / refresh the intraday ML dataset parquet."""
    from .ml_data_builder_intraday import build_intraday_dataset as _fn

    return _fn(*args, **kwargs)


def train_intraday_models(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Train and persist intraday models (currently LightGBM 3-class)."""
    from .train_lightgbm_intraday import train_lightgbm_intraday as _fn

    return _fn(*args, **kwargs)


def score_intraday_tickers(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Score tickers and write predictions_dt into rolling."""
    # Keep heavy imports inside.
    from .ai_model_intraday import score_intraday_batch, load_intraday_models
    from dt_backend.core.data_pipeline_dt import _read_rolling, ensure_symbol_node, save_rolling

    import pandas as pd

    max_symbols = kwargs.get("max_symbols")

    rolling = _read_rolling() or {}
    rows = []
    index = []

    for sym, node in rolling.items():
        if not isinstance(sym, str) or sym.startswith("_"):
            continue

        feats = node.get("features_dt")
        if not isinstance(feats, dict) or not feats:
            continue

        rows.append(feats)
        index.append(sym)

        if max_symbols and len(rows) >= int(max_symbols):
            break

    if not rows:
        return {"status": "empty", "symbols_scored": 0}

    df = pd.DataFrame(rows, index=index)

    models = load_intraday_models()
    proba_df, labels = score_intraday_batch(df, models=models)

    updated = 0
    for sym in proba_df.index:
        node = ensure_symbol_node(rolling, sym)
        node["predictions_dt"] = {
            "label": str(labels.loc[sym]),
            "proba": proba_df.loc[sym].to_dict(),
        }
        rolling[sym] = node
        updated += 1

    # Persist so downstream steps (policy, signals, UI) see the scores.
    try:
        save_rolling(rolling)
    except Exception:
        # If save fails, still return the in-memory count.
        pass

    return {"status": "ok", "symbols_scored": updated}


def build_intraday_signals(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build ranked intraday signals from rolling."""
    from .signals_rank_builder import build_intraday_signals as _fn

    return _fn(*args, **kwargs)


def train_incremental_intraday(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Online incremental training (if enabled)."""
    from .continuous_learning_intraday import train_incremental_intraday as _fn

    return _fn(*args, **kwargs)


__all__ = [
    "build_intraday_dataset",
    "train_intraday_models",
    "score_intraday_tickers",
    "build_intraday_signals",
    "train_incremental_intraday",
]
