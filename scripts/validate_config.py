#!/usr/bin/env python3
"""
Configuration Validation Script

Validates all configuration sources and settings.
"""

import sys
import os
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def check_env_var(var_name: str, required: bool = False) -> bool:
    """Check if environment variable is set."""
    value = os.getenv(var_name)
    if value:
        print(f"✅ {var_name}: SET (length: {len(value)})")
        return True
    else:
        if required:
            print(f"❌ {var_name}: MISSING (required)")
            return False
        else:
            print(f"⚠️  {var_name}: NOT SET (optional)")
            return True

def validate_config_file(file_path: Path, description: str) -> bool:
    """Validate a configuration file exists and is readable."""
    if not file_path.exists():
        print(f"❌ {description}: {file_path} (does not exist)")
        return False
    
    try:
        content = file_path.read_text()
        print(f"✅ {description}: {file_path} ({len(content)} bytes)")
        return True
    except Exception as e:
        print(f"❌ {description}: {file_path} (read error: {e})")
        return False

def main():
    """Run all configuration validation tests."""
    print("=" * 80)
    print("CONFIGURATION VALIDATION")
    print("=" * 80)
    print()
    
    results = []
    
    # Check .env file
    print("Environment File:")
    print("-" * 80)
    env_file = ROOT / ".env"
    results.append(validate_config_file(env_file, ".env file"))
    print()
    
    # Check required environment variables
    print("Required Environment Variables:")
    print("-" * 80)
    results.append(check_env_var("ALPACA_API_KEY_ID", required=True))
    results.append(check_env_var("ALPACA_SECRET_KEY", required=True))
    print()
    
    # Check optional environment variables
    print("Optional Environment Variables:")
    print("-" * 80)
    results.append(check_env_var("ADMIN_PASSWORD_HASH", required=False))
    results.append(check_env_var("ADMIN_TOKEN_TTL_SECONDS", required=False))
    results.append(check_env_var("AION_TZ", required=False))
    results.append(check_env_var("SUPABASE_URL", required=False))
    results.append(check_env_var("SUPABASE_KEY", required=False))
    results.append(check_env_var("OPENAI_API_KEY", required=False))
    results.append(check_env_var("FRED_API_KEY", required=False))
    results.append(check_env_var("DATABASE_URL", required=False))
    print()
    
    # Check config files
    print("Configuration Files:")
    print("-" * 80)
    results.append(validate_config_file(ROOT / "config.py", "Root config"))
    results.append(validate_config_file(ROOT / "settings.py", "Settings"))
    results.append(validate_config_file(ROOT / "requirements.txt", "Requirements"))
    print()
    
    # Check optional config files
    print("Optional Configuration Files:")
    print("-" * 80)
    knobs_env = ROOT / "knobs.env"
    if knobs_env.exists():
        results.append(validate_config_file(knobs_env, "Swing knobs"))
    else:
        print(f"⚠️  Swing knobs: {knobs_env} (using defaults)")
        results.append(True)
    
    dt_knobs_env = ROOT / "dt_knobs.env"
    if dt_knobs_env.exists():
        results.append(validate_config_file(dt_knobs_env, "DT knobs"))
    else:
        print(f"⚠️  DT knobs: {dt_knobs_env} (using defaults)")
        results.append(True)
    
    print()
    
    # Test config imports
    print("Configuration Imports:")
    print("-" * 80)
    
    try:
        from config import PATHS, DT_PATHS, ROOT as CONFIG_ROOT
        print(f"✅ config.py: PATHS ({len([k for k, v in PATHS.items() if isinstance(v, Path)])} paths)")
        print(f"✅ config.py: DT_PATHS ({len([k for k, v in DT_PATHS.items() if isinstance(v, Path)])} paths)")
        print(f"✅ config.py: ROOT = {CONFIG_ROOT}")
        results.append(True)
    except Exception as e:
        print(f"❌ config.py import failed: {e}")
        results.append(False)
    
    try:
        from settings import TIMEZONE, BOT_KNOBS_DEFAULTS, BOT_KNOBS_SCHEMA
        print(f"✅ settings.py: TIMEZONE = {TIMEZONE}")
        print(f"✅ settings.py: BOT_KNOBS_DEFAULTS ({len(BOT_KNOBS_DEFAULTS)} bots)")
        print(f"✅ settings.py: BOT_KNOBS_SCHEMA ({len(BOT_KNOBS_SCHEMA)} fields)")
        results.append(True)
    except Exception as e:
        print(f"❌ settings.py import failed: {e}")
        results.append(False)
    
    print()
    
    # Test backend config
    print("Backend Configuration:")
    print("-" * 80)
    
    try:
        from backend.core.config import PATHS as BACKEND_PATHS
        print(f"✅ backend.core.config: PATHS accessible")
        results.append(True)
    except Exception as e:
        print(f"❌ backend.core.config import failed: {e}")
        results.append(False)
    
    print()
    
    # Test DT backend config
    print("DT Backend Configuration:")
    print("-" * 80)
    
    try:
        from dt_backend.core.config_dt import DT_PATHS as DT_BACKEND_PATHS
        print(f"✅ dt_backend.core.config_dt: DT_PATHS accessible")
        results.append(True)
    except Exception as e:
        print(f"❌ dt_backend.core.config_dt import failed: {e}")
        results.append(False)
    
    print()
    
    # Validate knob defaults
    print("Bot Knob Validation:")
    print("-" * 80)
    
    try:
        from settings import BOT_KNOBS_DEFAULTS, BOT_KNOBS_SCHEMA
        
        # Check swing knobs
        swing = BOT_KNOBS_DEFAULTS.get("swing", {})
        required_keys = ["aggression", "max_alloc", "max_positions", "stop_loss", "take_profit", "min_confidence"]
        
        all_present = True
        for key in required_keys:
            if key in swing:
                print(f"✅ swing.{key}: {swing[key]}")
            else:
                print(f"❌ swing.{key}: MISSING")
                all_present = False
        
        results.append(all_present)
        
        # Check intraday knobs
        intraday = BOT_KNOBS_DEFAULTS.get("intraday", {})
        for key in required_keys:
            if key in intraday:
                print(f"✅ intraday.{key}: {intraday[key]}")
            else:
                print(f"❌ intraday.{key}: MISSING")
                all_present = False
        
        results.append(all_present)
        
    except Exception as e:
        print(f"❌ Knob validation failed: {e}")
        results.append(False)
    
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    total = len(results)
    passed = sum(results)
    failed = total - passed
    
    print(f"Total checks: {total}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    
    if failed == 0:
        print("\n🎉 All configurations validated successfully!")
        print("\nNote: Some environment variables are optional and can be configured later.")
        return 0
    else:
        print(f"\n⚠️  {failed} check(s) failed. Review errors above.")
        print("\nCritical errors (MISSING required items) must be fixed before running the system.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
