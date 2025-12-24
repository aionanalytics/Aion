from __future__ import annotations

from typing import Dict, List, Optional
import numpy as np
import pandas as pd

def infer_sector_labels_from_features(X_df: pd.DataFrame) -> pd.Series:
    """Infer sector labels using one-hot encoded columns like sector_TECHNOLOGY, sector_FINANCIALS, etc.
    Falls back to UNKNOWN if no sector columns exist."""
    sector_cols = [c for c in X_df.columns if isinstance(c, str) and c.startswith("sector_")]
    if not sector_cols:
        return pd.Series(["UNKNOWN"] * len(X_df), index=X_df.index, dtype=object)

    mat = X_df[sector_cols].to_numpy(dtype=float, copy=False)
    idx = np.argmax(mat, axis=1)
    labels = [str(sector_cols[int(i)])[len("sector_"):] for i in idx]
    labels = [str(x).upper().strip() if x else "UNKNOWN" for x in labels]
    return pd.Series(labels, index=X_df.index, dtype=object)
