"""Test helpers shared across CLI tests."""

import sys
import runpy


def run_module(module_name, **kwargs):
    """Run a module via runpy without triggering RuntimeWarning.

    When a module is already in sys.modules (e.g. imported for patching),
    runpy.run_module() emits a RuntimeWarning about unpredictable behaviour.
    Removing it first avoids that.
    """
    saved = sys.modules.pop(module_name, None)
    try:
        return runpy.run_module(module_name, **kwargs)
    finally:
        if saved is not None:
            sys.modules[module_name] = saved
