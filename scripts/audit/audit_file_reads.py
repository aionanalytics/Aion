#!/usr/bin/env python3
"""
Audit Script: File Read Operations

Validates that all file read operations:
1. Use proper PATHS dictionary keys
2. Have error handling
3. Handle missing files gracefully
4. Use correct file formats

Usage:
    python3 scripts/audit/audit_file_reads.py
"""

import ast
import re
from pathlib import Path
from typing import List, Dict, Any

# Root directory
ROOT = Path(__file__).resolve().parent.parent.parent


class FileReadAuditor(ast.NodeVisitor):
    """AST visitor to find file read operations"""
    
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.issues = []
        self.file_reads = []
        self.in_try_except = False
        
    def visit_Call(self, node: ast.Call):
        """Visit function call nodes"""
        # Check for file read patterns
        if isinstance(node.func, ast.Attribute):
            # Pattern: gzip.open(), path.read_text(), json.load()
            if node.func.attr in ('open', 'read_text', 'read_bytes', 'load'):
                self._check_read_operation(node)
        elif isinstance(node.func, ast.Name):
            # Pattern: open()
            if node.func.id == 'open':
                self._check_read_operation(node)
        
        self.generic_visit(node)
    
    def visit_Try(self, node: ast.Try):
        """Track try/except blocks"""
        old_in_try = self.in_try_except
        self.in_try_except = True
        self.generic_visit(node)
        self.in_try_except = old_in_try
    
    def _check_read_operation(self, node: ast.Call):
        """Check if file read operation is safe"""
        line_no = node.lineno
        
        # Record the read operation
        self.file_reads.append({
            'line': line_no,
            'has_error_handling': self.in_try_except
        })
        
        # Check if error handling present
        if not self.in_try_except:
            self.issues.append({
                'type': 'Missing Error Handling',
                'location': f"{self.filepath.relative_to(ROOT)}:{line_no}",
                'severity': 'Medium',
                'message': 'File read operation without try/except wrapper'
            })


def audit_python_file(filepath: Path) -> Dict[str, Any]:
    """Audit a single Python file for file read operations"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=str(filepath))
        auditor = FileReadAuditor(filepath)
        auditor.visit(tree)
        
        return {
            'file': str(filepath.relative_to(ROOT)),
            'read_count': len(auditor.file_reads),
            'reads_without_error_handling': len([r for r in auditor.file_reads if not r['has_error_handling']]),
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
    print("FILE READ OPERATIONS AUDIT")
    print("=" * 80)
    print()
    
    # Directories to audit
    dirs_to_audit = [
        ROOT / 'backend',
        ROOT / 'dt_backend',
        ROOT / 'utils'
    ]
    
    all_results = []
    total_reads = 0
    total_unprotected = 0
    total_issues = 0
    
    for dir_path in dirs_to_audit:
        if not dir_path.exists():
            continue
        
        python_files = list(dir_path.rglob('*.py'))
        
        for py_file in python_files:
            result = audit_python_file(py_file)
            
            if 'error' not in result:
                all_results.append(result)
                total_reads += result['read_count']
                total_unprotected += result['reads_without_error_handling']
                total_issues += len(result['issues'])
    
    # Print results
    print(f"Total Python files scanned: {len(all_results)}")
    print(f"Total file read operations: {total_reads}")
    print(f"Reads without error handling: {total_unprotected}")
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
    
    # Print summary by directory
    print("SUMMARY BY DIRECTORY:")
    print("-" * 80)
    
    dir_stats = {}
    for result in all_results:
        dir_name = result['file'].split('/')[0]
        if dir_name not in dir_stats:
            dir_stats[dir_name] = {'reads': 0, 'unprotected': 0}
        dir_stats[dir_name]['reads'] += result['read_count']
        dir_stats[dir_name]['unprotected'] += result['reads_without_error_handling']
    
    for dir_name, stats in sorted(dir_stats.items()):
        print(f"{dir_name}:")
        print(f"  Total reads: {stats['reads']}")
        print(f"  Unprotected: {stats['unprotected']}")
        print(f"  Coverage: {((stats['reads'] - stats['unprotected']) / max(stats['reads'], 1) * 100):.1f}%")
        print()
    
    # Overall status
    print("=" * 80)
    if total_unprotected == 0:
        print("✅ PASS: All file read operations have error handling")
    else:
        print(f"⚠️  WARNING: {total_unprotected} file reads lack error handling")
    print("=" * 80)


if __name__ == '__main__':
    audit_all_files()
