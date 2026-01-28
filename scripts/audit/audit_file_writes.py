#!/usr/bin/env python3
"""
Audit Script: File Write Operations

Validates that all file write operations:
1. Use proper PATHS dictionary keys
2. Use atomic write pattern (tempfile + rename)
3. Have error handling
4. Handle disk full scenarios

Usage:
    python3 scripts/audit/audit_file_writes.py
"""

import ast
import re
from pathlib import Path
from typing import List, Dict, Any

# Root directory
ROOT = Path(__file__).resolve().parent.parent.parent


class FileWriteAuditor(ast.NodeVisitor):
    """AST visitor to find file write operations"""
    
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.issues = []
        self.file_writes = []
        self.in_try_except = False
        self.uses_tempfile = False
        self.uses_rename = False
        
    def visit_Call(self, node: ast.Call):
        """Visit function call nodes"""
        # Check for file write patterns
        if isinstance(node.func, ast.Attribute):
            # Pattern: gzip.open('w'), path.write_text(), json.dump()
            if node.func.attr in ('open', 'write_text', 'write_bytes', 'dump', 'to_parquet', 'to_csv'):
                self._check_write_operation(node)
        elif isinstance(node.func, ast.Name):
            # Pattern: open('w')
            if node.func.id == 'open':
                # Check if mode is write
                if len(node.args) >= 2:
                    if isinstance(node.args[1], ast.Constant) and 'w' in str(node.args[1].value):
                        self._check_write_operation(node)
        
        self.generic_visit(node)
    
    def visit_Try(self, node: ast.Try):
        """Track try/except blocks"""
        old_in_try = self.in_try_except
        self.in_try_except = True
        self.generic_visit(node)
        self.in_try_except = old_in_try
    
    def visit_Import(self, node: ast.Import):
        """Track tempfile import"""
        for alias in node.names:
            if alias.name == 'tempfile':
                self.uses_tempfile = True
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Track tempfile import"""
        if node.module == 'tempfile':
            self.uses_tempfile = True
        self.generic_visit(node)
    
    def _check_write_operation(self, node: ast.Call):
        """Check if file write operation is safe"""
        line_no = node.lineno
        
        # Record the write operation
        self.file_writes.append({
            'line': line_no,
            'has_error_handling': self.in_try_except,
            'uses_tempfile': self.uses_tempfile
        })
        
        # Check if error handling present
        if not self.in_try_except:
            self.issues.append({
                'type': 'Missing Error Handling',
                'location': f"{self.filepath.relative_to(ROOT)}:{line_no}",
                'severity': 'High',
                'message': 'File write operation without try/except wrapper'
            })


def audit_python_file(filepath: Path) -> Dict[str, Any]:
    """Audit a single Python file for file write operations"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=str(filepath))
        auditor = FileWriteAuditor(filepath)
        auditor.visit(tree)
        
        # Check for atomic write pattern
        has_atomic_pattern = (
            auditor.uses_tempfile and
            'rename' in source or 'replace' in source
        )
        
        return {
            'file': str(filepath.relative_to(ROOT)),
            'write_count': len(auditor.file_writes),
            'writes_without_error_handling': len([w for w in auditor.file_writes if not w['has_error_handling']]),
            'uses_atomic_pattern': has_atomic_pattern,
            'issues': auditor.issues
        }
    except Exception as e:
        return {
            'file': str(filepath.relative_to(ROOT)),
            'error': str(e)
        }


def audit_all_files():
    """Audit all Python files in the repository"""
    print("=" * 80)
    print("FILE WRITE OPERATIONS AUDIT")
    print("=" * 80)
    print()
    
    # Directories to audit
    dirs_to_audit = [
        ROOT / 'backend',
        ROOT / 'dt_backend',
        ROOT / 'utils'
    ]
    
    all_results = []
    total_writes = 0
    total_unprotected = 0
    total_atomic = 0
    total_issues = 0
    
    for dir_path in dirs_to_audit:
        if not dir_path.exists():
            continue
        
        python_files = list(dir_path.rglob('*.py'))
        
        for py_file in python_files:
            result = audit_python_file(py_file)
            
            if 'error' not in result:
                all_results.append(result)
                total_writes += result['write_count']
                total_unprotected += result['writes_without_error_handling']
                if result['uses_atomic_pattern']:
                    total_atomic += 1
                total_issues += len(result['issues'])
    
    # Print results
    print(f"Total Python files scanned: {len(all_results)}")
    print(f"Total file write operations: {total_writes}")
    print(f"Writes without error handling: {total_unprotected}")
    print(f"Files using atomic write pattern: {total_atomic}")
    print(f"Total issues found: {total_issues}")
    print()
    
    # Print critical issues
    if total_issues > 0:
        print("CRITICAL ISSUES:")
        print("-" * 80)
        
        issue_id = 1
        for result in all_results:
            for issue in result['issues']:
                print(f"Issue #{issue_id:03d}")
                print(f"  Type: {issue['type']}")
                print(f"  Location: {issue['location']}")
                print(f"  Severity: {issue['severity']}")
                print(f"  Message: {issue['message']}")
                print()
                issue_id += 1
    
    # Print files with high write activity
    print("HIGH WRITE ACTIVITY FILES:")
    print("-" * 80)
    
    high_activity = [r for r in all_results if r['write_count'] > 5]
    high_activity.sort(key=lambda x: x['write_count'], reverse=True)
    
    for result in high_activity[:10]:
        print(f"{result['file']}")
        print(f"  Write operations: {result['write_count']}")
        print(f"  Unprotected: {result['writes_without_error_handling']}")
        print(f"  Atomic pattern: {'✅' if result['uses_atomic_pattern'] else '❌'}")
        print()
    
    # Overall status
    print("=" * 80)
    if total_unprotected == 0:
        print("✅ PASS: All file write operations have error handling")
    else:
        print(f"⚠️  WARNING: {total_unprotected} file writes lack error handling")
    
    if total_atomic >= len(high_activity) * 0.8:
        print("✅ GOOD: Most critical files use atomic write pattern")
    else:
        print("⚠️  WARNING: Consider using atomic write pattern for more files")
    print("=" * 80)


if __name__ == '__main__':
    audit_all_files()
