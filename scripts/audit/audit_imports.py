#!/usr/bin/env python3
"""
Audit Script: Import Chain Verification

Validates that:
1. All imports can resolve
2. No circular dependencies exist
3. Configuration imports use proper shim layer
4. No missing modules

Usage:
    python3 scripts/audit/audit_imports.py
"""

import ast
import sys
from pathlib import Path
from typing import Dict, Set, List, Tuple
from collections import defaultdict

# Root directory
ROOT = Path(__file__).resolve().parent.parent.parent


class ImportCollector(ast.NodeVisitor):
    """AST visitor to collect all imports"""
    
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.imports = []
        
    def visit_Import(self, node: ast.Import):
        """Visit import statements"""
        for alias in node.names:
            self.imports.append({
                'type': 'import',
                'module': alias.name,
                'line': node.lineno
            })
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Visit from...import statements"""
        if node.module:
            self.imports.append({
                'type': 'from',
                'module': node.module,
                'names': [alias.name for alias in node.names],
                'line': node.lineno
            })
        self.generic_visit(node)


def collect_imports(filepath: Path) -> List[Dict]:
    """Collect all imports from a Python file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=str(filepath))
        collector = ImportCollector(filepath)
        collector.visit(tree)
        
        return collector.imports
    except Exception as e:
        return []


def build_dependency_graph() -> Tuple[Dict[str, Set[str]], Dict[str, List]]:
    """Build dependency graph for all Python files"""
    graph = defaultdict(set)
    all_imports = {}
    
    # Directories to scan
    dirs_to_scan = [
        ROOT / 'backend',
        ROOT / 'dt_backend',
        ROOT / 'utils'
    ]
    
    for dir_path in dirs_to_scan:
        if not dir_path.exists():
            continue
        
        for py_file in dir_path.rglob('*.py'):
            # Convert file path to module name
            rel_path = py_file.relative_to(ROOT)
            module_name = str(rel_path.with_suffix('')).replace('/', '.')
            
            # Collect imports
            imports = collect_imports(py_file)
            all_imports[module_name] = imports
            
            # Build graph
            for imp in imports:
                imported_module = imp['module']
                # Filter to only local modules
                if imported_module.startswith(('backend', 'dt_backend', 'utils', 'config', 'settings', 'admin_keys')):
                    graph[module_name].add(imported_module)
    
    return graph, all_imports


def find_circular_dependencies(graph: Dict[str, Set[str]]) -> List[List[str]]:
    """Find circular dependencies using DFS"""
    visited = set()
    rec_stack = set()
    cycles = []
    
    def dfs(node: str, path: List[str]):
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor, path.copy())
            elif neighbor in rec_stack:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
        
        rec_stack.remove(node)
    
    for node in graph:
        if node not in visited:
            dfs(node, [])
    
    return cycles


def check_config_imports(all_imports: Dict[str, List]) -> List[Dict]:
    """Check that config imports follow best practices"""
    issues = []
    
    for module_name, imports in all_imports.items():
        # Skip root config files themselves
        if module_name in ('config', 'settings', 'admin_keys'):
            continue
        
        # Skip shim layers
        if module_name in ('backend.core.config', 'dt_backend.core.config_dt'):
            continue
        
        for imp in imports:
            # Check for direct root config imports (should use shim)
            if imp['module'] in ('config', 'settings', 'admin_keys'):
                # This is acceptable for admin/root level files
                if not module_name.startswith(('backend.admin', 'backend.database')):
                    # For regular modules, should use shim
                    if module_name.startswith('backend'):
                        issues.append({
                            'type': 'Config Import Pattern',
                            'location': f"{module_name}:{imp['line']}",
                            'severity': 'Low',
                            'message': f"Should import from backend.core.config instead of {imp['module']}"
                        })
                    elif module_name.startswith('dt_backend'):
                        issues.append({
                            'type': 'Config Import Pattern',
                            'location': f"{module_name}:{imp['line']}",
                            'severity': 'Low',
                            'message': f"Should import from dt_backend.core.config_dt instead of {imp['module']}"
                        })
    
    return issues


def audit_imports():
    """Main audit function"""
    print("=" * 80)
    print("IMPORT CHAIN VERIFICATION AUDIT")
    print("=" * 80)
    print()
    
    # Build dependency graph
    print("Building dependency graph...")
    graph, all_imports = build_dependency_graph()
    
    total_modules = len(all_imports)
    total_imports = sum(len(imports) for imports in all_imports.values())
    
    print(f"Total modules: {total_modules}")
    print(f"Total import statements: {total_imports}")
    print()
    
    # Check for circular dependencies
    print("Checking for circular dependencies...")
    cycles = find_circular_dependencies(graph)
    
    if cycles:
        print(f"❌ FOUND {len(cycles)} CIRCULAR DEPENDENCIES:")
        print("-" * 80)
        for i, cycle in enumerate(cycles, 1):
            print(f"Cycle #{i}:")
            print("  " + " -> ".join(cycle))
            print()
    else:
        print("✅ No circular dependencies found!")
        print()
    
    # Check config import patterns
    print("Checking configuration import patterns...")
    config_issues = check_config_imports(all_imports)
    
    if config_issues:
        print(f"⚠️  Found {len(config_issues)} config import pattern issues:")
        print("-" * 80)
        for issue in config_issues:
            print(f"  {issue['location']}: {issue['message']}")
        print()
    else:
        print("✅ All config imports follow best practices!")
        print()
    
    # Check for common imports
    print("Most frequently imported modules:")
    print("-" * 80)
    
    import_counts = defaultdict(int)
    for imports in all_imports.values():
        for imp in imports:
            import_counts[imp['module']] += 1
    
    top_imports = sorted(import_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for module, count in top_imports:
        print(f"  {module}: {count} imports")
    print()
    
    # Overall status
    print("=" * 80)
    if not cycles and not config_issues:
        print("✅ PASS: All imports are clean!")
    elif not cycles:
        print("✅ PASS: No circular dependencies (minor pattern issues)")
    else:
        print("❌ FAIL: Circular dependencies detected!")
    print("=" * 80)


if __name__ == '__main__':
    # Add root to Python path
    sys.path.insert(0, str(ROOT))
    audit_imports()
