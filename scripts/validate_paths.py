#!/usr/bin/env python3
"""
Path Validation Script

Validates that all PATHS and DT_PATHS resolve correctly and directories exist.
"""

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def validate_path(key: str, path: Path, should_exist: bool = False) -> bool:
    """Validate a single path."""
    if not isinstance(path, Path):
        # Skip non-Path values (like int settings)
        print(f"⚪ {key}: {path} (not a path)")
        return True
    
    exists = path.exists()
    is_dir = path.is_dir() if exists else (path.suffix == "")
    
    if should_exist:
        if exists:
            print(f"✅ {key}: {path}")
            return True
        else:
            print(f"❌ {key}: {path} (does not exist)")
            return False
    else:
        if exists:
            print(f"✅ {key}: {path}")
        else:
            print(f"⚠️  {key}: {path} (will be created on demand)")
        return True

def main():
    """Run all path validation tests."""
    print("=" * 80)
    print("PATH VALIDATION")
    print("=" * 80)
    print()
    
    # Import config
    try:
        from config import PATHS, DT_PATHS, ROOT, ensure_project_structure
    except ImportError as e:
        print(f"❌ Failed to import config: {e}")
        return 1
    
    print(f"Project Root: {ROOT}")
    print()
    
    results = []
    
    # Critical paths that must exist
    critical_paths = [
        "root",
        "ml_data",
        "brains_root",
        "logs",
    ]
    
    # Validate PATHS
    print("PATHS Dictionary Validation:")
    print("-" * 80)
    for key, value in PATHS.items():
        if not isinstance(value, Path):
            continue
        
        is_critical = key in critical_paths
        result = validate_path(key, value, should_exist=is_critical)
        results.append(result)
    
    print()
    
    # Validate DT_PATHS
    print("DT_PATHS Dictionary Validation:")
    print("-" * 80)
    dt_critical = ["root", "dt_backend", "da_brains"]
    
    for key, value in DT_PATHS.items():
        if not isinstance(value, Path):
            continue
        
        is_critical = key in dt_critical
        result = validate_path(key, value, should_exist=is_critical)
        results.append(result)
    
    print()
    
    # Test ensure_project_structure
    print("Testing ensure_project_structure():")
    print("-" * 80)
    try:
        ensure_project_structure()
        print("✅ ensure_project_structure() completed successfully")
        results.append(True)
    except Exception as e:
        print(f"❌ ensure_project_structure() failed: {e}")
        results.append(False)
    
    print()
    
    # Verify critical files can be created
    print("Verifying Critical Config Files:")
    print("-" * 80)
    
    critical_files = [
        PATHS.get("bots_config"),
        PATHS.get("bots_ui_overrides"),
        DT_PATHS.get("intraday_ui_store"),
    ]
    
    for file_path in critical_files:
        if file_path and isinstance(file_path, Path):
            if file_path.exists():
                print(f"✅ {file_path.name}: exists")
                results.append(True)
            else:
                print(f"⚠️  {file_path.name}: will be created on first use")
                results.append(True)
    
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    total = len(results)
    passed = sum(results)
    failed = total - passed
    
    print(f"Total paths checked: {total}")
    print(f"✅ Valid: {passed}")
    print(f"❌ Invalid: {failed}")
    
    if failed == 0:
        print("\n🎉 All paths validated successfully!")
        print("\nNote: Some paths marked with ⚠️  will be created automatically on first use.")
        return 0
    else:
        print(f"\n⚠️  {failed} path(s) failed validation. Check errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
