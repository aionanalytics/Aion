#!/usr/bin/env python3
"""
Complete System Validation Script

Runs all validation checks in sequence:
1. Import validation
2. Path validation
3. Configuration validation
4. Environment variable validation
"""

import sys
import subprocess
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPTS_DIR = Path(__file__).parent

def run_script(script_name: str, description: str) -> bool:
    """Run a validation script and return success status."""
    print("=" * 80)
    print(f"Running: {description}")
    print("=" * 80)
    print()
    
    script_path = SCRIPTS_DIR / script_name
    
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=False,
            text=True,
            cwd=ROOT
        )
        
        print()
        if result.returncode == 0:
            print(f"✅ {description} PASSED")
            return True
        else:
            print(f"❌ {description} FAILED")
            return False
    except Exception as e:
        print(f"❌ {description} ERROR: {e}")
        return False

def main():
    """Run all validation scripts."""
    print("\n")
    print("*" * 80)
    print("AION ANALYTICS - COMPLETE SYSTEM VALIDATION")
    print("*" * 80)
    print()
    
    results = {}
    
    # 1. Import validation
    results['imports'] = run_script(
        "validate_imports.py",
        "Import Validation"
    )
    
    print("\n")
    
    # 2. Path validation
    results['paths'] = run_script(
        "validate_paths.py",
        "Path Validation"
    )
    
    print("\n")
    
    # 3. Configuration validation
    results['config'] = run_script(
        "validate_config.py",
        "Configuration Validation"
    )
    
    print("\n")
    
    # Final summary
    print("=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print()
    
    for name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {name.title()} Validation")
    
    print()
    
    total = len(results)
    passed = sum(results.values())
    failed = total - passed
    
    print(f"Total: {total} validation suites")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    
    print()
    
    if failed == 0:
        print("🎉 " * 20)
        print("ALL VALIDATIONS PASSED!")
        print("System is ready to run.")
        print("🎉 " * 20)
        print()
        print("Next steps:")
        print("  1. Start backend: python3 run_backend.py")
        print("  2. Start frontend: cd frontend && npm run dev")
        print("  3. Start scheduler: python3 backend/scheduler_runner.py")
        return 0
    else:
        print("⚠️ " * 20)
        print(f"{failed} VALIDATION SUITE(S) FAILED")
        print("Review errors above and fix issues before running the system.")
        print("⚠️ " * 20)
        print()
        print("Common fixes:")
        print("  - Missing .env: cp .env.example .env && edit .env")
        print("  - Missing dependencies: pip install -r requirements.txt")
        print("  - Set PYTHONPATH: export PYTHONPATH=$PWD:$PYTHONPATH")
        return 1

if __name__ == "__main__":
    sys.exit(main())
