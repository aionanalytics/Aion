#!/usr/bin/env python3
"""
Full System Audit Script

Runs all audit scripts and generates comprehensive report

Usage:
    python3 scripts/audit/full_system_audit.py
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent.parent


def run_script(script_path: Path) -> tuple[int, str, str]:
    """Run an audit script and return exit code, stdout, stderr"""
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Script timed out after 5 minutes"
    except Exception as e:
        return 1, "", str(e)


def main():
    """Run all audit scripts"""
    print("=" * 80)
    print("FULL SYSTEM AUDIT")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 80)
    print()
    
    # Define audit scripts to run
    scripts = [
        ('audit_file_reads.py', 'File Read Operations'),
        ('audit_file_writes.py', 'File Write Operations'),
        ('audit_imports.py', 'Import Chain Verification'),
    ]
    
    results = {}
    
    for script_name, description in scripts:
        script_path = Path(__file__).parent / script_name
        
        if not script_path.exists():
            print(f"⚠️  Skipping {description}: Script not found")
            continue
        
        print(f"Running {description}...")
        print("-" * 80)
        
        exit_code, stdout, stderr = run_script(script_path)
        
        results[script_name] = {
            'description': description,
            'exit_code': exit_code,
            'stdout': stdout,
            'stderr': stderr
        }
        
        # Print output
        if stdout:
            print(stdout)
        if stderr:
            print(f"ERRORS:\n{stderr}", file=sys.stderr)
        
        print()
    
    # Summary
    print("=" * 80)
    print("AUDIT SUMMARY")
    print("=" * 80)
    print()
    
    all_passed = True
    for script_name, result in results.items():
        status = "✅ PASS" if result['exit_code'] == 0 else "❌ FAIL"
        print(f"{status} - {result['description']}")
        if result['exit_code'] != 0:
            all_passed = False
    
    print()
    print("=" * 80)
    if all_passed:
        print("✅ ALL AUDITS PASSED")
    else:
        print("❌ SOME AUDITS FAILED - Review output above")
    print(f"Completed: {datetime.now().isoformat()}")
    print("=" * 80)
    
    # Exit with failure if any script failed
    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()
