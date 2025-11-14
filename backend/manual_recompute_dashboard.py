# backend/manual_recompute_dashboard.py
"""
Manual recompute script for Dashboard Intelligence v1.5.0
Run:
  (.venv) python backend/manual_recompute_dashboard.py
"""

from __future__ import annotations
from dashboard_builder import compute_accuracy, compute_top_performers

def main():
    print("🧩 Manual Dashboard Recompute — StockAnalyzerPro v1.5.0")
    acc = compute_accuracy(days=30, tolerance=10, horizons=("1w","1m"))
    print(f"📊 Accuracy (30d, ±{acc.get('tolerance', 10)}% tol): {acc.get('accuracy_30d')} | Sample: {acc.get('sample_size')}")
    for h in ("1w", "1m"):
        res = compute_top_performers(h)
        print(f"🏆 {res.get('summary')}")
    print("✅ Dashboard recompute complete — JSONs updated.")

if __name__ == "__main__":
    main()
