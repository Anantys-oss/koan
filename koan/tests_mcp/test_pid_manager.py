"""Subprocess regression tests for how `pid_manager` boots the MCP daemon.

`start_mcp()` launches the entrypoint as a **module** (`python -m app.mcp`).
That is not a style choice: CPython puts a *script's* own directory at
`sys.path[0]`, ahead of `PYTHONPATH` and the stdlib, so under a script launch
(`python app/mcp/__main__.py`) every module in `app/mcp/` shadows the stdlib
package of the same name for the whole process. That is how an earlier
`app/mcp/http.py` broke Starlette's `import http.cookies` and left the HTTP
transport unbootable while every module-level unit test stayed green.

These tests spawn real interpreters from the `koan/` working directory, because
`sys.path[0]` is decided by how the process starts and cannot be reproduced by
importing the same entrypoint as a package module.
"""

import os
import subprocess
import sys
from pathlib import Path

KOAN_DIR = Path(__file__).resolve().parents[1]


def _spawn(argv, timeout=60):
    return subprocess.run(
        argv,
        cwd=str(KOAN_DIR),
        env={
            # A filtered env keeps the subprocess deterministic. The entrypoint
            # reads mcp.enabled from a config that is not there, which is the
            # graceful "disabled" exit rather than an import error — so an
            # import error is unambiguously the thing under test.
            **{
                k: v
                for k, v in os.environ.items()
                if k.startswith("PYTHON") or k in {"PATH", "LD_LIBRARY_PATH"}
            },
            "KOAN_ROOT": "/tmp/test-koan",
            "PYTHONPATH": str(KOAN_DIR),
        },
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_entrypoint_boots_as_module():
    """The argv `start_mcp()` actually uses."""
    result = _spawn([sys.executable, "-m", "app.mcp"])

    assert result.returncode == 1, result.stderr
    assert "disabled" in result.stderr, result.stderr
    assert "ModuleNotFoundError" not in result.stderr, result.stderr


def test_script_form_still_reaches_the_disabled_exit():
    """The legacy argv must stay bootable too.

    Nothing launches this form today, but an operator's hand-written systemd
    unit might, and `docs/operations/mcp-server.md` tells them not to. This
    only reaches the config gate — the SDK import that a shadowed stdlib module
    actually breaks happens later, so the rename guard is the next test, not
    this one.
    """
    result = _spawn([sys.executable, "app/mcp/__main__.py"])

    assert result.returncode == 1, result.stderr
    assert "disabled" in result.stderr, result.stderr
    assert "ModuleNotFoundError" not in result.stderr, result.stderr


def test_http_transport_imports_with_package_dir_first():
    """Import the transport with `app/mcp/` forced onto the front of sys.path.

    This is the script launch's path layout without the launch, so it fails the
    moment the package shadows a stdlib module uvicorn or starlette imports.
    """
    result = _spawn(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'app/mcp'); "
            "import app.mcp.http_transport",
        ]
    )

    assert result.returncode == 0, result.stderr
