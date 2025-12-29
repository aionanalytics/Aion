"""dt_backend.ml package

High-level intraday ML building blocks:
  • build_intraday_dataset
  • train_intraday_models
  • score_intraday_tickers
  • build_intraday_signals
  • train_incremental_intraday

Design goals:
  - Keep imports light at module load (avoid importing LightGBM unless needed).
  - Provide stable public names expected by dt_backend.jobs.
"""

from __future__ import annotations

# ---------------------------------------------------------------------
# Safe import (no heavy dependencies at module load)
# ---------------------------------------------------------------------
from .ml_data_builder_intraday import build_intraday_dataset


# ---------------------------------------------------------------------
# Lazy import wrappers (avoid loading LightGBM early)
# ---------------------------------------------------------------------
def train_intraday_models(*args, **kwargs):
    """Train and persist the intraday classifier model(s).

    Your jobs (ex: backfill_intraday_full) expect this symbol to exist.
    Internally we use the LightGBM trainer.
    """
    from .train_lightgbm_intraday import train_lightgbm_intraday as _fn

    return _fn(*args, **kwargs)


def score_intraday_tickers(*args, **kwargs):
    """Score intraday tickers and write predictions_dt into rolling.

    Compatibility wrapper expected by dt_backend.jobs.daytrading_job.
    """
    from .ai_model_intraday import score_intraday_batch, load_intraday_models
    from dt_backend.core.data_pipeline_dt import _read_rolling, ensure_symbol_node

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

        if max_symbols and len(rows) >= max_symbols:
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

    return {"status": "ok", "symbols_scored": updated}


def build_intraday_signals(*args, **kwargs):
    """Lazy loader for intraday signal builder."""
    from .signals_rank_builder import build_intraday_signals as _fn

    return _fn(*args, **kwargs)


def train_incremental_intraday(*args, **kwargs):
    """Lazy loader for online incremental training."""
    from .continuous_learning_intraday import train_incremental_intraday as _fn

    return _fn(*args, **kwargs)


__all__ = [
    "build_intraday_dataset",
    "train_intraday_models",
    "score_intraday_tickers",
    "build_intraday_signals",
    "train_incremental_intraday",
]
