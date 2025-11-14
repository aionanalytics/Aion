from pydantic import BaseModel, Field
from typing import Optional, List

class PredictRequest(BaseModel):
    asof_date: Optional[str] = None
    tickers: Optional[List[str]] = None
    horizons: Optional[List[int]] = None

class PredictLog(BaseModel):
    asof_date: str
    ticker: str
    horizon_days: int
    y_pred: float
    p_up: Optional[float] = None
    uncertainty: Optional[float] = None
    features_hash: Optional[str] = None
    model_version: Optional[str] = None
    status: str = Field(default="open")

class HarvestResponse(BaseModel):
    realized_count: int

class RetrainPolicy(BaseModel):
    max_days: int = 14
    min_new_pairs: int = 1000
    ic_floor: float = 0.02
    drift_threshold: float = 0.2
