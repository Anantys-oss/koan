"""
Kōan — Sanity check runner.

Discovers and runs all sanity check modules in this directory,
in alphabetical order. Each module must expose:

    def run(instance_dir: str) -> Tuple[bool, List[str]]
        Returns (was_modified, list_of_changes_made).

Modules are discovered by filename: any .py file that isn't __init__.py
is treated as a sanity check module.
"""

import importlib
import pkgutil
from pathlib import Path
from typing import List, Tuple


def discover_checks() -> List[str]:
    """Return sorted list of sanity check module names in this package."""
    package_dir = Path(__file__).parent
    modules = [
        info.name
        for info in pkgutil.iter_modules([str(package_dir)])
        if not info.ispkg
    ]
    return sorted(modules)


def run_all(instance_dir: str) -> List[Tuple[str, bool, List[str]]]:
    """Run all sanity checks in alphabetical order.

    Returns list of (module_name, was_modified, changes) tuples.
    """
    results = []
    for name in discover_checks():
        module = importlib.import_module(f"sanity.{name}")
        run_fn = getattr(module, "run", None)
        if run_fn is None:
            continue
        modified, changes = run_fn(instance_dir)
        results.append((name, modified, changes))
    return results
