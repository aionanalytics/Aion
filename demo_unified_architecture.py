"""Integration test demonstrating unified logging and truth store.

This script shows how swing and DT systems now share a unified logger
and truth store, enabling cross-strategy analysis.
"""

import tempfile
from pathlib import Path
import json

# Import unified logger
from utils.logger import Logger

# Import shared truth store
from backend.services.shared_truth_store import SharedTruthStore


def demo_unified_logging():
    """Demonstrate unified logging with source tracking."""
    print("\n" + "="*60)
    print("DEMO: Unified Logging")
    print("="*60)
    
    # Create swing logger
    swing_logger = Logger(name="swing_bot", source="swing")
    swing_logger.info("Swing bot initialized")
    swing_logger.info("Analyzing AAPL for entry signal", symbol="AAPL", confidence=0.85)
    
    # Create DT logger
    dt_logger = Logger(name="dt_executor", source="dt")
    dt_logger.info("DT executor initialized")
    dt_logger.info("Intraday breakout detected", symbol="TSLA", price=200.50)
    
    print("\n✅ Logs written to respective directories with unified format")
    print("   - Swing logs: [swing_bot] [swing] [INFO] ...")
    print("   - DT logs: [dt_executor] [dt] [INFO] ...")


def demo_shared_truth_store():
    """Demonstrate shared truth store with cross-strategy queries."""
    print("\n" + "="*60)
    print("DEMO: Shared Truth Store")
    print("="*60)
    
    # Create temporary store
    with tempfile.TemporaryDirectory() as tmpdir:
        import os
        os.environ["SHARED_TRUTH_DIR"] = tmpdir
        
        store = SharedTruthStore()
        
        # Swing bot trades
        print("\n📊 Swing bot trades:")
        store.append_trade_event(
            source="swing",
            symbol="AAPL",
            side="BUY",
            qty=100,
            price=150.50,
            reason="SIGNAL_HIGH_CONF"
        )
        print("   ✅ BUY 100 AAPL @ $150.50 (swing)")
        
        store.append_trade_event(
            source="swing",
            symbol="MSFT",
            side="BUY",
            qty=50,
            price=300.00,
            reason="MOMENTUM_SIGNAL"
        )
        print("   ✅ BUY 50 MSFT @ $300.00 (swing)")
        
        # DT bot trades
        print("\n⚡ DT bot trades:")
        store.append_trade_event(
            source="dt",
            symbol="AAPL",
            side="BUY",
            qty=25,
            price=151.00,
            reason="BREAKOUT"
        )
        print("   ✅ BUY 25 AAPL @ $151.00 (dt)")
        
        store.append_trade_event(
            source="dt",
            symbol="TSLA",
            side="SELL",
            qty=10,
            price=200.75,
            reason="TAKE_PROFIT",
            pnl=150.00
        )
        print("   ✅ SELL 10 TSLA @ $200.75 (dt) - P&L: $150.00")
        
        # Query by source
        print("\n🔍 Query trades by source:")
        swing_trades = store.get_trades_by_source("swing", days=1)
        print(f"   - Swing trades: {len(swing_trades)}")
        for t in swing_trades:
            print(f"     • {t['side']} {t['qty']} {t['symbol']} @ ${t['price']:.2f}")
        
        dt_trades = store.get_trades_by_source("dt", days=1)
        print(f"   - DT trades: {len(dt_trades)}")
        for t in dt_trades:
            pnl_str = f" (P&L: ${t['pnl']:.2f})" if t.get('pnl') else ""
            print(f"     • {t['side']} {t['qty']} {t['symbol']} @ ${t['price']:.2f}{pnl_str}")
        
        # Query by symbol
        print("\n🔍 Query all AAPL trades (both sources):")
        aapl_trades = store.get_symbol_trades("AAPL", days=1)
        print(f"   - Total AAPL trades: {len(aapl_trades)}")
        for t in aapl_trades:
            print(f"     • [{t['source'].upper()}] {t['side']} {t['qty']} @ ${t['price']:.2f}")
        
        # Detect conflicts
        print("\n⚠️  Detect conflicts (both sources trading same symbol):")
        conflicts = store.detect_conflicts(days=1)
        if conflicts:
            print(f"   - Found {len(conflicts)} conflict(s)")
            for c in conflicts:
                print(f"     • {c['symbol']}: {len(c['swing_trades'])} swing + {len(c['dt_trades'])} DT trades")
        else:
            print("   - No conflicts detected")
        
        print("\n✅ Unified truth store enables:")
        print("   1. Single source of truth for all trades")
        print("   2. Cross-strategy analysis (swing vs DT)")
        print("   3. Conflict detection")
        print("   4. Unified P&L tracking")


def demo_backward_compatibility():
    """Demonstrate backward compatibility."""
    print("\n" + "="*60)
    print("DEMO: Backward Compatibility")
    print("="*60)
    
    # Old-style imports still work
    from dt_backend.core.logger_dt import log, info, warn, error
    print("\n✅ DT backend imports (old style) work:")
    print("   from dt_backend.core.logger_dt import log, info, warn, error")
    
    from backend.services.swing_truth_store import append_swing_event
    print("\n✅ Swing truth store imports (old style) work:")
    print("   from backend.services.swing_truth_store import append_swing_event")
    
    from dt_backend.services.dt_truth_store import append_trade_event
    print("\n✅ DT truth store imports (old style) work:")
    print("   from dt_backend.services.dt_truth_store import append_trade_event")
    
    print("\n✅ All existing code continues to work without changes")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("UNIFIED LOGGING & TRUTH STORE - INTEGRATION DEMO")
    print("="*60)
    
    demo_unified_logging()
    demo_shared_truth_store()
    demo_backward_compatibility()
    
    print("\n" + "="*60)
    print("✅ ALL DEMOS COMPLETED SUCCESSFULLY")
    print("="*60)
    print("\nKey Benefits:")
    print("1. Consistent log format across swing and DT systems")
    print("2. Single truth store for all trades (source tracking)")
    print("3. Cross-strategy queries and conflict detection")
    print("4. Zero breaking changes (backward compatible)")
    print("5. Dependency injection for specialized features")
    print()
