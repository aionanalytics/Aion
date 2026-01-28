"""
EOD data snapshot system for historical replay.

Saves complete market state daily:
- Price bars (OHLCV)
- Fundamentals
- Macro indicators
- News/sentiment
- Rolling cache

Ensures zero look-ahead bias in replay.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import pandas as pd
import json
import gzip


@dataclass
class EODSnapshot:
    """Complete market state for a single day."""
    date: str
    bars: pd.DataFrame
    fundamentals: Dict[str, Any]
    macro: Dict[str, Any]
    news: List[Dict[str, Any]]
    sentiment: Dict[str, Any]
    rolling_state: Dict[str, Any]


class SnapshotManager:
    """Manages EOD snapshots for replay."""
    
    def __init__(self, snapshots_root: Path):
        self.snapshots_root = Path(snapshots_root)
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
    
    def save_snapshot(self, snapshot: EODSnapshot) -> None:
        """Save EOD snapshot."""
        date_dir = self.snapshots_root / snapshot.date
        date_dir.mkdir(parents=True, exist_ok=True)
        
        # Save bars as parquet
        if not snapshot.bars.empty:
            snapshot.bars.to_parquet(date_dir / "bars.parquet")
        
        # Save JSON data compressed
        self._write_gzip_json(date_dir / "fundamentals.json.gz", snapshot.fundamentals)
        self._write_gzip_json(date_dir / "macro.json.gz", snapshot.macro)
        self._write_gzip_json(date_dir / "news.json.gz", snapshot.news)
        self._write_gzip_json(date_dir / "sentiment.json.gz", snapshot.sentiment)
        self._write_gzip_json(date_dir / "rolling.json.gz", snapshot.rolling_state)
        
        # Manifest
        manifest = {
            "date": snapshot.date,
            "created_at": datetime.utcnow().isoformat(),
            "bars_count": len(snapshot.bars) if not snapshot.bars.empty else 0,
            "symbols": list(snapshot.bars['symbol'].unique()) if not snapshot.bars.empty and 'symbol' in snapshot.bars.columns else [],
        }
        self._write_json(date_dir / "manifest.json", manifest)
    
    def load_snapshot(self, date: str) -> EODSnapshot:
        """Load snapshot for date."""
        date_dir = self.snapshots_root / date
        
        if not date_dir.exists():
            raise FileNotFoundError(f"No snapshot for {date}")
        
        bars_file = date_dir / "bars.parquet"
        bars = pd.read_parquet(bars_file) if bars_file.exists() else pd.DataFrame()
        
        return EODSnapshot(
            date=date,
            bars=bars,
            fundamentals=self._read_gzip_json(date_dir / "fundamentals.json.gz"),
            macro=self._read_gzip_json(date_dir / "macro.json.gz"),
            news=self._read_gzip_json(date_dir / "news.json.gz"),
            sentiment=self._read_gzip_json(date_dir / "sentiment.json.gz"),
            rolling_state=self._read_gzip_json(date_dir / "rolling.json.gz"),
        )
    
    def snapshot_exists(self, date: str) -> bool:
        """Check if snapshot exists for date."""
        return (self.snapshots_root / date / "manifest.json").exists()
    
    def list_snapshots(self) -> List[str]:
        """List all available snapshot dates."""
        return sorted([d.name for d in self.snapshots_root.iterdir() if d.is_dir()])
    
    def _write_gzip_json(self, path: Path, data: Any):
        """Write JSON data with gzip compression."""
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(data, f)
    
    def _read_gzip_json(self, path: Path) -> Any:
        """Read gzipped JSON data."""
        if not path.exists():
            # Return appropriate default based on filename
            if "fundamentals" in path.name or "macro" in path.name or "sentiment" in path.name or "rolling" in path.name:
                return {}
            else:  # news
                return []
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    
    def _write_json(self, path: Path, data: Any):
        """Write JSON data (uncompressed)."""
        path.write_text(json.dumps(data, indent=2))


def capture_eod_snapshot() -> EODSnapshot:
    """Capture current market state as snapshot."""
    from backend.core.data_pipeline import _read_rolling
    
    date_str = datetime.now(timezone.utc).date().isoformat()
    
    # Get rolling state
    rolling_state = _read_rolling() or {}
    
    # Extract bars from rolling state
    bars_data = []
    for sym, node in rolling_state.items():
        if str(sym).startswith("_"):
            continue
        hist = node.get("history", [])
        for bar in hist:
            if isinstance(bar, dict):
                bar_copy = bar.copy()
                bar_copy["symbol"] = sym
                bars_data.append(bar_copy)
    
    bars_df = pd.DataFrame(bars_data) if bars_data else pd.DataFrame()
    
    # Extract fundamentals from rolling state
    fundamentals = {}
    for sym, node in rolling_state.items():
        if str(sym).startswith("_"):
            continue
        fund = node.get("fundamentals", {})
        if fund:
            fundamentals[sym] = fund
    
    # Load macro state
    try:
        from backend.core.config import PATHS
        macro_path = Path(PATHS.get("macro_state"))
        if macro_path.exists():
            macro = json.loads(macro_path.read_text(encoding="utf-8"))
        else:
            macro = {}
    except Exception:
        macro = {}
    
    # Get news/sentiment from rolling state context
    news = []
    sentiment = {}
    for sym, node in rolling_state.items():
        if str(sym).startswith("_"):
            continue
        ctx = node.get("context", {})
        if isinstance(ctx, dict):
            if "news" in ctx:
                news.extend(ctx.get("news", []))
            if "social" in ctx:
                sentiment[sym] = ctx.get("social", {})
    
    return EODSnapshot(
        date=date_str,
        bars=bars_df,
        fundamentals=fundamentals,
        macro=macro,
        news=news,
        sentiment=sentiment,
        rolling_state=rolling_state,
    )


__all__ = [
    "EODSnapshot",
    "SnapshotManager",
    "capture_eod_snapshot",
]
