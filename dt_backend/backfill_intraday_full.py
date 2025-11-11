# backfill_intraday_yf.py
import yfinance as yf
import json, gzip, datetime, os
from tqdm import tqdm

ROLLING_PATH = r"data/stock_cache/master/rolling.json.gz"
OUT_PATH = r"data_dt/rolling_intraday.json.gz"

# Load your symbol universe
print("📥 Loading symbol list from rolling.json.gz ...")
with gzip.open(ROLLING_PATH, "rt", encoding="utf-8") as f:
    rolling = json.load(f)
symbols = list(rolling.keys())[:500]  # limit for test, increase if stable
print(f"✅ Loaded {len(symbols)} symbols.")

# Fetch today's bars (1m) for each
today = datetime.date.today().strftime("%Y-%m-%d")
bars_out = {}
for sym in tqdm(symbols, desc="Fetching"):
    try:
        df = yf.download(
            sym,
            interval="1m",
            period="1d",
            auto_adjust=True,
            progress=False
        )
        if df.empty:
            continue
        bars = []
        for ts, row in df.iterrows():
            bars.append({
                "t": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": round(float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"]), 2),
                "h": round(float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]), 2),
                "l": round(float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]), 2),
                "c": round(float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]), 2),
                "v": int(row["Volume"].iloc[0] if hasattr(row["Volume"], "iloc") else row["Volume"]),
            })
        if bars:
            bars_out[sym] = bars
    except Exception as e:
        print(f"⚠️ {sym}: {e}")

# Save in the same structure as your Alpaca file
payload = {
    "timestamp": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "symbols": len(bars_out),
    "bars": bars_out,
}
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with gzip.open(OUT_PATH, "wt", encoding="utf-8") as f:
    json.dump(payload, f)

print(f"💾 Saved {len(bars_out)} symbols → {OUT_PATH}")
print("✅ Done.")
