# backend/core/memmap_trainer.py
"""
Memmap + Reservoir trainer for LightGBM
- Streams parquet in batches
- Writes into disk-backed np.memmap (float32)
- Uses reservoir sampling to cap max_rows without loading everything
- Trains LightGBM directly from memmap

This fixes: streaming-read but non-streaming-train (X_parts + concatenate spikes).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import numpy as np
import lightgbm as lgb

# -------------------------
# PyArrow imports (SAFE)
# -------------------------
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception as e:
    pa = None
    pq = None


@dataclass
class MemmapTrainResult:
    model: object
    rows_seen: int
    rows_used: int
    seconds_ingest: float
    seconds_train: float
    tmp_dir: str


def _ensure_pyarrow():
    if pa is None or pq is None:
        raise RuntimeError(
            "pyarrow is required for memmap trainer. Install with: pip install pyarrow"
        )


def _safe_float32(arr: np.ndarray) -> np.ndarray:
    if arr.dtype == np.float32:
        return arr
    return arr.astype(np.float32, copy=False)


def _finite_row_mask(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.isfinite(y) & np.isfinite(X).all(axis=1)


def train_lgbm_memmap_reservoir(
    parquet_path: str,
    feature_cols: List[str],
    target_col: str,
    lgb_params: Dict,
    symbol_whitelist: Optional[Set[str]] = None,
    *,
    tmp_root: str,
    max_rows: int = 800_000,
    batch_rows: int = 100_000,
    min_rows: int = 20_000,
    seed: int = 42,
    y_clip_low: float | None = None,
    y_clip_high: float | None = None,
    cleanup: bool = True,
) -> MemmapTrainResult:
    """
    Stream parquet -> reservoir sample into disk-backed memmap -> train LGBM.
    """
    _ensure_pyarrow()

    if max_rows <= 0:
        raise ValueError("max_rows must be positive")

    os.makedirs(tmp_root, exist_ok=True)
    run_id = f"mm_{int(time.time())}_{os.getpid()}_{np.random.default_rng(seed).integers(0, 1_000_000)}"
    tmp_dir = os.path.join(tmp_root, run_id)
    os.makedirs(tmp_dir, exist_ok=True)

    X_path = os.path.join(tmp_dir, "X.float32.mmap")
    y_path = os.path.join(tmp_dir, "y.float32.mmap")

    n_features = len(feature_cols)
    if n_features == 0:
        raise ValueError("feature_cols is empty")

    rng = np.random.default_rng(seed)

    X_mm = np.memmap(X_path, mode="w+", dtype=np.float32, shape=(max_rows, n_features))
    y_mm = np.memmap(y_path, mode="w+", dtype=np.float32, shape=(max_rows,))

    rows_seen = 0
    rows_used = 0

    ingest_start = time.time()

    pf = pq.ParquetFile(parquet_path)
    cols = feature_cols + [target_col]
    if symbol_whitelist:
        if 'symbol' not in cols:
            cols.append('symbol')

    for batch in pf.iter_batches(batch_size=batch_rows, columns=cols):
        # Convert RecordBatch -> Table safely (NO to_table())
        tbl = pa.Table.from_batches([batch])

        # Build X matrix column-wise (safe for mixed dtypes)
        col_arrays = []
        for c in feature_cols:
            a = tbl.column(c).to_numpy(zero_copy_only=False)
            col_arrays.append(_safe_float32(a))

        X_new = np.stack(col_arrays, axis=1)

        y_new = tbl.column(target_col).to_numpy(zero_copy_only=False)
        y_new = _safe_float32(y_new)

        # Optional symbol whitelist filter
        if symbol_whitelist is not None:
            try:
                sym = tbl.column('symbol').to_numpy(zero_copy_only=False)
                sym_u = [str(x).upper() for x in sym]
                keep = np.fromiter((s in symbol_whitelist for s in sym_u), dtype=bool, count=len(sym_u))
                if not keep.all():
                    X_new = X_new[keep]
                    y_new = y_new[keep]
            except Exception:
                pass

        # Optional target clipping (quantile-based, per horizon)
        if y_clip_low is not None and y_clip_high is not None:
            y_new = np.clip(y_new, float(y_clip_low), float(y_clip_high))

        mask = _finite_row_mask(X_new, y_new)
        if not mask.all():
            X_new = X_new[mask]
            y_new = y_new[mask]

        if X_new.shape[0] == 0:
            continue

        for i in range(X_new.shape[0]):
            rows_seen += 1
            if rows_used < max_rows:
                X_mm[rows_used] = X_new[i]
                y_mm[rows_used] = y_new[i]
                rows_used += 1
            else:
                j = rng.integers(0, rows_seen)
                if j < max_rows:
                    X_mm[j] = X_new[i]
                    y_mm[j] = y_new[i]

    seconds_ingest = time.time() - ingest_start

    if rows_used < min_rows:
        if cleanup:
            _cleanup_dir(tmp_dir)
        raise RuntimeError(
            f"Not enough training rows for {target_col}: "
            f"rows_used={rows_used}, min_rows={min_rows}"
        )

    X_train = X_mm[:rows_used]
    y_train = y_mm[:rows_used]

    train_start = time.time()

    dtrain = lgb.Dataset(
        X_train,
        label=y_train,
        free_raw_data=False,
    )

    num_boost_round = int(lgb_params.get("num_boost_round", 800))

    model = lgb.train(
        params=lgb_params,
        train_set=dtrain,
        num_boost_round=num_boost_round,
    )

    seconds_train = time.time() - train_start

    if cleanup:
        _cleanup_dir(tmp_dir)

    return MemmapTrainResult(
        model=model,
        rows_seen=rows_seen,
        rows_used=rows_used,
        seconds_ingest=seconds_ingest,
        seconds_train=seconds_train,
        tmp_dir=tmp_dir,
    )


def _cleanup_dir(path: str) -> None:
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                except Exception:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except Exception:
                    pass
        try:
            os.rmdir(path)
        except Exception:
            pass
    except Exception:
        pass
