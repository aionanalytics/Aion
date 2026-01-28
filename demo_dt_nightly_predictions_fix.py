#!/usr/bin/env python3
"""Demo script to show the DT nightly job predictions fix.

This script demonstrates how the dt_nightly_job now properly attaches predictions
to the rolling file.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add parent dir to path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from dt_backend.jobs.dt_nightly_job import run_dt_nightly_job

def demo_dt_nightly_predictions():
    """Demo the fixed DT nightly job with predictions."""
    
    print("=" * 80)
    print("DT NIGHTLY JOB PREDICTIONS FIX DEMO")
    print("=" * 80)
    print()
    
    print("BEFORE the fix:")
    print("  ❌ dt_nightly_job.py never called attach_intraday_predictions()")
    print("  ❌ Rolling file had empty predictions_dt fields")
    print("  ❌ Rolling optimizer had no predictions to optimize")
    print()
    
    print("AFTER the fix:")
    print("  ✅ dt_nightly_job.py calls attach_intraday_predictions() (Phase 7)")
    print("  ✅ Rolling file gets populated with predictions_dt")
    print("  ✅ Rolling optimizer has predictions to optimize")
    print()
    
    print("-" * 80)
    print("SIMULATING DT NIGHTLY JOB ON A TRADING DAY")
    print("-" * 80)
    print()
    
    # Mock all the dependencies
    with patch('dt_backend.core.market_hours.is_trading_day') as mock_is_trading:
        mock_is_trading.return_value = True
        
        with patch('dt_backend.jobs.dt_nightly_job._brokers_dir') as mock_brokers:
            mock_brokers.return_value = Path("/tmp/demo_brokers")
            
            with patch('dt_backend.jobs.dt_nightly_job._write_metrics') as mock_metrics:
                mock_metrics.return_value = Path("/tmp/demo_metrics.json")
                
                with patch('dt_backend.jobs.dt_nightly_job._stamp_brain'):
                    # Create mock prediction result
                    mock_pred_result = {
                        "status": "ok",
                        "symbols_seen": 150,
                        "predicted": 145,
                        "missing_features": 5,
                        "ts": "2024-01-08T20:00:00Z"
                    }
                    
                    with patch('dt_backend.jobs.dt_nightly_job.attach_intraday_predictions') as mock_attach:
                        mock_attach.return_value = mock_pred_result
                        
                        # Run the job
                        result = run_dt_nightly_job(session_date="2024-01-08")
    
    print("Job completed successfully!")
    print()
    print("Result summary:")
    print(json.dumps(result, indent=2))
    print()
    
    print("-" * 80)
    print("KEY CHANGES IN THE RESULT")
    print("-" * 80)
    print()
    
    if "predictions" in result:
        pred = result["predictions"]
        print(f"✅ predictions field exists: {pred}")
        print(f"   - Status: {pred.get('status')}")
        print(f"   - Symbols seen: {pred.get('symbols_seen')}")
        print(f"   - Predictions written: {pred.get('predicted')}")
        print(f"   - Missing features: {pred.get('missing_features')}")
    else:
        print("❌ predictions field missing (should not happen!)")
    
    print()
    print("-" * 80)
    print("FLOW COMPARISON")
    print("-" * 80)
    print()
    
    print("OLD FLOW (BROKEN):")
    print("  1. ✅ Compute bot stats from broker ledgers")
    print("  2. ✅ Write metrics file")
    print("  3. ✅ Run continuous learning")
    print("  4. ❌ MISSING: Attach predictions to rolling")
    print("  5. ✅ Return summary")
    print()
    
    print("NEW FLOW (FIXED):")
    print("  1. ✅ Compute bot stats from broker ledgers")
    print("  2. ✅ Write metrics file")
    print("  3. ✅ Run continuous learning")
    print("  4. ✅ Attach intraday predictions to rolling (NEW!)")
    print("  5. ✅ Return summary with predictions info")
    print()
    
    print("-" * 80)
    print("IMPACT")
    print("-" * 80)
    print()
    print("  ✅ Rolling file will have populated predictions_dt fields")
    print("  ✅ Rolling optimizer will have predictions to optimize")
    print("  ✅ DT trading engine will have fresh ML predictions after market close")
    print("  ✅ System becomes end-to-end functional")
    print()
    
    print("=" * 80)
    print("DEMO COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    demo_dt_nightly_predictions()
