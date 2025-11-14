from __future__ import annotations
"""
ml_helpers.py
Merged helpers: feature_utils, target_engineering, metrics, model_registry, meta_model
NOTE: This file was auto-generated to reduce file count while preserving public APIs.
"""
# flake8: noqa

from backend.config import ML_DATA_DIR  # ✅ use centralized paths

# BEGIN: feature_utils.py
import pandas as pd, numpy as np
try:
    import pandas_ta as ta
except Exception:
    ta=None

# ------------------------------------------------------------------
# Safe numeric conversion helper
# ------------------------------------------------------------------
def _safe_float(val, default: float = 0.0) -> float:
    """Convert val to float safely, returning default if it fails."""
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        return float(str(val).replace(",", "").strip())
    except Exception:
        return default

def add_tech_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df=df.copy(); close=df['close']
    if ta is not None:
        df['rsi_14']=ta.rsi(close, length=14)
        macd=ta.macd(close)
        if hasattr(macd,'iloc'):
            df['macd']=macd.iloc[:,0]; df['macd_signal']=macd.iloc[:,1]; df['macd_hist']=macd.iloc[:,2]
        bb=ta.bbands(close, length=20, std=2.0)
        if hasattr(bb,'iloc'):
            df['bb_upper']=bb.iloc[:,0]; df['bb_mid']=bb.iloc[:,1]; df['bb_lower']=bb.iloc[:,2]
    else:
        delta=close.diff(); up=delta.clip(lower=0); down=-delta.clip(upper=0)
        rs=up.rolling(14).mean()/(down.rolling(14).mean().replace(0,np.nan))
        df['rsi_14']=100-(100/(1+rs))
        ema12=close.ewm(span=12, adjust=False).mean(); ema26=close.ewm(span=26, adjust=False).mean()
        macd=ema12-ema26; signal=macd.ewm(span=9, adjust=False).mean()
        df['macd']=macd; df['macd_signal']=signal; df['macd_hist']=macd-signal
        mid=close.rolling(20).mean(); std=close.rolling(20).std()
        df['bb_mid']=mid; df['bb_upper']=mid+2*std; df['bb_lower']=mid-2*std
    df['volatility_10d']=close.pct_change().rolling(10).std(); df['momentum_5d']=close.pct_change(5)
    return df

def add_lags_and_deltas(df: pd.DataFrame, cols:list[str], lags=(1,3,5,10)) -> pd.DataFrame:
    df=df.copy()
    for c in cols:
        if c in df.columns:
            for L in lags:
                df[f'{c}_lag{L}']=df[c].shift(L)
                df[f'{c}_chg{L}']=df[c].pct_change(L)
    return df

def sector_zscore(df: pd.DataFrame, value_cols:list[str], sector_col:str='sector')->pd.DataFrame:
    df=df.copy()
    if sector_col not in df.columns or 'date' not in df.columns: return df
    df[value_cols]=df.groupby([sector_col,'date'], observed=True)[value_cols].transform(lambda x:(x-x.mean())/(x.std(ddof=0)+1e-9))
    return df

def add_pca(df: pd.DataFrame, cols:list[str], n_components:int=8, prefix:str='pca_')->pd.DataFrame:
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA
    X=df[cols].replace([np.inf,-np.inf],np.nan).fillna(0.0).values
    if X.shape[1]==0: return df
    Xs=StandardScaler().fit_transform(X)
    k=min(n_components, Xs.shape[1])
    if k<=0: return df
    comps=PCA(n_components=k).fit_transform(Xs)
    for i in range(comps.shape[1]): df[f'{prefix}{i+1}']=comps[:,i]
    return df

# END: feature_utils.py
# BEGIN: target_engineering.py

import pandas as pd

def add_return_targets(df: pd.DataFrame, horizons=(5,20,60), price_col='close')->pd.DataFrame:
    df=df.copy()
    for h in horizons:
        df[f'target_{h}d_ret']=df[price_col].shift(-h)/df[price_col]-1.0
    return df

def bucketize_returns(df: pd.DataFrame, horizons=(5,20,60), thresholds=(0.03,-0.02))->pd.DataFrame:
    up,down=thresholds; df=df.copy()
    for h in horizons:
        r=df[f'target_{h}d_ret']
        cls=(r.gt(up).astype(int)*2 + r.between(down,up,inclusive='both').astype(int)*1)
        cls[r.lt(down)]=0
        df[f'target_{h}d_cls']=cls.astype('Int64')
    return df

# END: target_engineering.py
# BEGIN: metrics.py

import numpy as np
from scipy.stats import spearmanr

def information_coefficient(y_true, y_pred):
    if len(y_true) == 0: return np.nan
    return spearmanr(y_true, y_pred, nan_policy="omit").correlation

def hit_ratio(y_true, y_pred):
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    m = (np.sign(y_true) == np.sign(y_pred))
    return float(np.nanmean(m))

def precision_at_k(y_true, y_pred, k=0.1):
    y_true = np.asarray(y_true); y_pred = np.asarray(y_pred)
    thr = np.nanquantile(y_pred, 1-k)
    mask = y_pred >= thr
    return float(np.nanmean(np.sign(y_true[mask]) == 1))

def brier_score(y_bin, p_up):
    y_bin = np.asarray(y_bin); p_up = np.asarray(p_up)
    return float(np.nanmean((p_up - y_bin)**2))

# END: metrics.py
# BEGIN: model_registry.py

"""
Lightweight Model Registry for StockAnalyzerPro.
Tracks model versions, metrics, and feature schema hashes.
"""

import json, os, hashlib, time

REGISTRY_FILE = os.path.join(ML_DATA_DIR, "model_registry.jsonl")
os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)

def hash_features(features):
    return hashlib.md5("".join(sorted(features)).encode()).hexdigest()[:8]

def register_model(model_name, metrics, features):
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_name": model_name,
        "feature_hash": hash_features(features),
        "metrics": metrics,
    }
    with open(REGISTRY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"📘 Registered model {model_name} ({entry['feature_hash']})")

# END: model_registry.py
# BEGIN: meta_model.py

from typing import List
def fit_meta(df, feature_cols: List[str]):
    from sklearn.ensemble import GradientBoostingRegressor
    cols=["y_pred","proba"]+[c for c in feature_cols if c in df.columns]; cols=[c for c in cols if c in df.columns]
    X=df[cols].values; y=df["y_true"].values
    gbr=GradientBoostingRegressor().fit(X,y)
    return {"model":gbr,"features":cols}
def apply_meta(bundle, df):
    X=df[bundle["features"]].values
    return bundle["model"].predict(X)

# END: meta_model.py
# BEGIN: plotting.py

import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd

def plot_target_distributions(df):
    targets = [c for c in df.columns if c.startswith("target_")]
    plt.figure(figsize=(10, 5))
    for t in targets:
        sns.kdeplot(df[t].dropna(), label=t)
    plt.title("Target Distributions (multi-horizon)")
    plt.legend()
    plt.show()

def plot_target_correlation(df):
    targets = [c for c in df.columns if c.startswith("target_")]
    corr = df[targets].corr()
    sns.heatmap(corr, annot=True, cmap="coolwarm", fmt=".2f")
    plt.title("Correlation Between Target Horizons")
    plt.show()

def plot_feature_importances(importances: pd.Series, top_n=20):
    importances.nlargest(top_n).sort_values().plot(kind="barh")
    plt.title(f"Top {top_n} Feature Importances")
    plt.tight_layout()
    plt.show()

# END: plotting.py
# BEGIN: explainability_tools.py

"""
Explainability tools for StockAnalyzerPro.
Provides SHAP-based feature interpretation utilities.
"""
import shap

def explain_model(model, X_sample, max_display=10):
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample)
    shap.summary_plot(shap_values, X_sample, max_display=max_display)

# END: explainability_tools.py
