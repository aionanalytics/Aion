#!/usr/bin/env python3
"""
Import Validation Script

Validates that all critical imports resolve correctly.
"""

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def test_import(module_path: str, description: str) -> bool:
    """Test if a module can be imported."""
    try:
        __import__(module_path)
        print(f"✅ {description}: {module_path}")
        return True
    except ImportError as e:
        print(f"❌ {description}: {module_path}")
        print(f"   Error: {e}")
        return False
    except Exception as e:
        print(f"⚠️  {description}: {module_path}")
        print(f"   Unexpected error: {e}")
        return False

def main():
    """Run all import tests."""
    print("=" * 60)
    print("IMPORT VALIDATION")
    print("=" * 60)
    print()
    
    results = []
    
    # Root config imports
    print("Root Configuration Imports:")
    print("-" * 60)
    results.append(test_import("config", "Root config"))
    results.append(test_import("settings", "Settings"))
    
    # Check if admin_keys can be imported (requires .env)
    try:
        import admin_keys
        print("✅ Admin keys: admin_keys")
        results.append(True)
    except Exception as e:
        print(f"⚠️  Admin keys: admin_keys (requires .env)")
        print(f"   Error: {e}")
        results.append(False)
    
    print()
    
    # Backend core imports
    print("Backend Core Imports:")
    print("-" * 60)
    results.append(test_import("backend.core.config", "Backend config"))
    results.append(test_import("backend.core.data_pipeline", "Data pipeline"))
    results.append(test_import("backend.core.regime_detector", "Regime detector"))
    results.append(test_import("backend.core.policy_engine", "Policy engine"))
    print()
    
    # Backend router imports
    print("Backend Router Imports:")
    print("-" * 60)
    results.append(test_import("backend.routers.bots_router", "Bots router"))
    results.append(test_import("backend.routers.insights_router_consolidated", "Insights router"))
    results.append(test_import("backend.routers.logs_router", "Logs router"))
    results.append(test_import("backend.routers.admin_router_final", "Admin router"))
    results.append(test_import("backend.routers.system_router", "System router"))
    print()
    
    # Backend service imports
    print("Backend Service Imports:")
    print("-" * 60)
    results.append(test_import("backend.services.bot_bootstrapper", "Bot bootstrapper"))
    results.append(test_import("backend.services.ml_data_builder", "ML data builder"))
    results.append(test_import("backend.services.metrics_fetcher", "Metrics fetcher"))
    results.append(test_import("backend.services.unified_cache_service", "Unified cache"))
    print()
    
    # Backend bot imports
    print("Backend Bot Imports:")
    print("-" * 60)
    results.append(test_import("backend.bots.base_swing_bot", "Base swing bot"))
    results.append(test_import("backend.bots.config_store", "Config store"))
    print()
    
    # DT backend imports
    print("DT Backend Imports:")
    print("-" * 60)
    results.append(test_import("dt_backend.core.config_dt", "DT config"))
    print()
    
    # Admin imports
    print("Admin Imports:")
    print("-" * 60)
    results.append(test_import("backend.admin.auth", "Admin auth"))
    print()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = len(results)
    passed = sum(results)
    failed = total - passed
    
    print(f"Total tests: {total}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    
    if failed == 0:
        print("\n🎉 All imports validated successfully!")
        return 0
    else:
        print(f"\n⚠️  {failed} import(s) failed. Check errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
