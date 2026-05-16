"""Shared fixtures for koan tests."""

import os
import tempfile
from pathlib import Path

import pytest


# --- per-worker KOAN_ROOT isolation (must run before any app.* import) ---
# Many app modules (utils.py, awake.py, …) snapshot the KOAN_ROOT env var at
# import time. When pytest-xdist spins up multiple workers in the same process
# group, they all inherit the same KOAN_ROOT and start racing on shared files
# under that directory (missions.md, .koan-* state, journal/, …).
#
# Give each xdist worker its own KOAN_ROOT before any koan module is imported.
# Tests that override KOAN_ROOT via monkeypatch or tmp_path remain unaffected;
# tests that rely on the ambient KOAN_ROOT now see a worker-private directory.
_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")
if _xdist_worker and _xdist_worker != "master":
    _per_worker_root = Path(tempfile.gettempdir()) / f"test-koan-{_xdist_worker}"
    (_per_worker_root / "instance").mkdir(parents=True, exist_ok=True)
    os.environ["KOAN_ROOT"] = str(_per_worker_root)


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Ensure tests don't touch real instance/ or send real Telegram messages."""
    monkeypatch.setenv("KOAN_TELEGRAM_TOKEN", "fake-token")
    monkeypatch.setenv("KOAN_TELEGRAM_CHAT_ID", "123456")
    monkeypatch.delenv("KOAN_PROJECTS", raising=False)
    # Prevent host CLI provider env vars from leaking into tests
    monkeypatch.delenv("CLI_PROVIDER", raising=False)
    monkeypatch.delenv("KOAN_CLI_PROVIDER", raising=False)
    # Reset projects_merged module-level cache so parallel workers don't
    # see stale project lists from a prior test's KOAN_ROOT.
    try:
        import app.projects_merged as pm
        pm._cached_projects = None
        pm._cached_root = None
        pm._cached_yaml_mtime = None
        pm._cached_workspace_mtime = None
    except Exception:
        pass


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory structure."""
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "soul.md").write_text("# Test Soul")
    (inst / "memory").mkdir()
    (inst / "memory" / "summary.md").write_text("Test summary.")
    (inst / "journal").mkdir()
    (inst / "outbox.md").write_text("")
    missions = inst / "missions.md"
    missions.write_text(
        "# Missions\n\n"
        "## Pending\n\n"
        "(none)\n\n"
        "## In Progress\n\n"
        "## Done\n\n"
    )
    return inst
