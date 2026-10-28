"""Guard the script-launch import path used by `pid_manager`.

`_launch_python_process` starts most daemons as a *script*, so CPython puts the
script's own directory first on `sys.path` — ahead of `PYTHONPATH` and the
stdlib. A module named after a stdlib top-level package in one of those
directories therefore shadows it for the whole process, and the daemon dies on
the first third-party import that touches it. That is invisible to the test
suite, which imports the same entrypoints as package modules.

A `module_mode=True` launch (`python -m app.mcp`) leaves `sys.path[0]` at the
working directory, so it carries no such hazard and is exempt here.
"""

import ast
import sys
from pathlib import Path

KOAN_DIR = Path(__file__).resolve().parents[1]
PID_MANAGER = KOAN_DIR / "app" / "pid_manager.py"


def _launch_targets() -> dict[str, bool]:
    """Map each `_launch_python_process` target to whether it is module-mode."""
    tree = ast.parse(PID_MANAGER.read_text(encoding="utf-8"))
    targets = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "_launch_python_process" or len(node.args) < 2:
            continue
        target = node.args[1]
        if not (isinstance(target, ast.Constant) and isinstance(target.value, str)):
            continue
        module_mode = any(
            keyword.arg == "module_mode"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )
        targets[target.value] = module_mode
    return targets


def test_the_guard_still_finds_the_launch_sites():
    """A refactor that stops matching must fail here, not pass vacuously."""
    targets = _launch_targets()
    assert "app/awake.py" in targets
    assert len(targets) >= 4


def test_mcp_is_launched_as_a_module():
    """`app/mcp/` holds `http_transport.py`, one rename away from `http.py`.

    The module launch is what makes that directory's contents irrelevant to
    `sys.path[0]`. Reverting to a script path would reopen the shadowing
    hazard, so pin the launch form rather than only the current filenames.
    """
    targets = _launch_targets()
    assert targets.get("app.mcp") is True
    assert "app/mcp/__main__.py" not in targets


def test_no_script_launched_directory_shadows_a_stdlib_module():
    offenders = []
    for target, module_mode in sorted(_launch_targets().items()):
        if module_mode:
            continue
        script_dir = KOAN_DIR / Path(target).parent
        for module in sorted(script_dir.glob("*.py")):
            if module.stem in sys.stdlib_module_names:
                offenders.append(f"{target}: {module.relative_to(KOAN_DIR)}")

    assert not offenders, (
        "These modules sit in a directory that becomes sys.path[0] when the "
        "daemon is launched as a script, so they shadow a stdlib module for "
        "the whole process: " + ", ".join(offenders)
    )
