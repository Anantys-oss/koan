"""Shared fixtures for koan tests."""

import os
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# --- per-worker KOAN_ROOT isolation (must run before any app.* import) ---
# Many app modules (utils.py, awake.py, …) snapshot the KOAN_ROOT env var at
# import time. When pytest-xdist spins up multiple workers in the same process
# group, they all inherit the same KOAN_ROOT and start racing on shared files
# under that directory (missions.md, .koan-* state, journal/, …).
#
# Give each xdist worker its own KOAN_ROOT before any koan module is imported.
# Wipe any leftover state from a prior run on the same worker name — otherwise
# missions.md fragments, .koan-* state, journal entries etc. carry over and
# can reintroduce exactly the cross-run pollution this fixture prevents.
# Tests that override KOAN_ROOT via monkeypatch or tmp_path remain unaffected;
# tests that rely on the ambient KOAN_ROOT now see a worker-private directory.
_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")
if _xdist_worker and _xdist_worker != "master":
    _per_worker_root = Path(tempfile.gettempdir()) / f"test-koan-{_xdist_worker}"
    shutil.rmtree(_per_worker_root, ignore_errors=True)
    (_per_worker_root / "instance").mkdir(parents=True, exist_ok=True)
    os.environ["KOAN_ROOT"] = str(_per_worker_root)


@pytest.fixture(autouse=True)
def _reset_run_module_state():
    """Reset module-level mission flags in `app.run` before each test.

    `_maybe_retry_mission` short-circuits on `_last_mission_timed_out`,
    `_last_mission_aborted`, or `_last_mission_stagnated`. Several test
    files (e.g. test_run.py) leave these flags set; under pytest-xdist
    that pollution leaks into whatever test runs next on the same worker.
    Resetting globally keeps every test starting from a clean state.
    """
    try:
        import app.run as run_mod
        run_mod._last_mission_timed_out = False
        run_mod._last_mission_aborted = False
        run_mod._last_mission_stagnated.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _mock_resolve_pr_location():
    """Bypass the gh CLI lookup at the start of run_rebase/run_recreate/run_review/run_squash.

    These pipelines call ``resolve_pr_location()`` at Step 0 to verify the
    PR exists at the given owner/repo (and probe other remotes if not). The
    helper shells out to ``gh pr view``, which:

    * requires ``gh`` to be installed and authenticated in the test env, and
    * becomes flaky under pytest-xdist when many workers shell out to ``gh``
      concurrently (auth contention, rate limiting).

    Tests that pass placeholder owners/repos like ``("o", "r", "1", "/p")``
    don't care about this resolution step — they only exercise the code
    *after* the PR location is known. Make the lookup a no-op everywhere so
    the test outcome doesn't depend on the host ``gh`` install or on xdist
    scheduling.

    Tests for ``resolve_pr_location()`` itself live in test_claude_step.py
    and use ``app.claude_step.resolve_pr_location`` directly — patching only
    the call-site bindings here leaves those unaffected.
    """
    targets = (
        "app.recreate_pr.resolve_pr_location",
        "app.rebase_pr.resolve_pr_location",
        "app.review_runner.resolve_pr_location",
        "app.squash_pr.resolve_pr_location",
    )
    passthrough = lambda owner, repo, pr_number, project_path: (owner, repo)  # noqa: E731
    with ExitStack() as stack:
        for target in targets:
            try:
                stack.enter_context(patch(target, side_effect=passthrough))
            except (AttributeError, ModuleNotFoundError):
                # Module not importable in this test run (e.g. minimal sys.path);
                # nothing to patch.
                continue
        yield


@pytest.fixture(autouse=True)
def _mock_fetch_pr_state():
    """Default PR state to OPEN so review tests don't need gh CLI.

    ``run_review()`` checks PR state at Step 0a and skips closed/merged PRs.
    Most review tests don't care about this gate — they exercise the review
    pipeline itself.  Mock returns ``"OPEN"`` universally; tests that
    specifically verify the closed-PR guard patch this target themselves.
    """
    try:
        with patch("app.review_runner._fetch_pr_state", return_value="OPEN"):
            yield
    except (AttributeError, ModuleNotFoundError):
        yield


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


@pytest.fixture(autouse=True)
def _reset_reply_breaker():
    """Clear per-thread reply circuit-breaker state between tests.

    The breaker (``github_reply._enforce_reply_budget``) persists reply
    counts under ``KOAN_ROOT/instance``. Without a reset these counts
    accumulate across the whole session — many tests reuse the same
    ``owner/repo#42`` — and eventually trip the cap, making unrelated
    posting tests fail. Mirror the other module-cache resets above.
    """
    try:
        import app.github_reply as gr
        gr._last_budget_warning.clear()
    except Exception:
        pass
    root = os.environ.get("KOAN_ROOT", "")
    if root:
        tracker = Path(root) / "instance" / ".koan-github-processed-threads.json"
        try:
            # Reply-breaker counts live in their own file; drop it wholesale.
            (Path(root) / "instance" / ".koan-github-reply-counts.json").unlink(
                missing_ok=True,
            )
            # Legacy safety: scrub any stray reply: keys from the shared tracker.
            if tracker.exists():
                import json
                data = json.loads(tracker.read_text())
                if isinstance(data, dict):
                    cleaned = {
                        k: v for k, v in data.items()
                        if not k.startswith("reply:")
                    }
                    tracker.write_text(json.dumps(cleaned))
        except Exception:
            pass
    yield


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


def make_iteration_plan(**overrides):
    """Build a minimal plan dict for _run_iteration tests."""
    plan = {
        "action": "mission",
        "project_name": "testproj",
        "autonomous_mode": "implement",
        "available_pct": 50,
        "display_lines": [],
        "mission_title": "test mission",
        "focus_area": "",
        "decision_reason": "",
        "recurring_injected": [],
    }
    plan.update(overrides)
    plan.setdefault("project_path", f"/tmp/{plan['project_name']}")
    return plan


@contextmanager
def patched_run_iteration(prep_result, extra_patches=None):
    """Patch all _run_iteration dependencies, yield mock for prepare_project_branch.

    Use extra_patches dict to override or add specific mocks (e.g. fire_hook).
    """
    mock_prep = MagicMock(return_value=prep_result)
    patches = {
        "app.run.plan_iteration": MagicMock(return_value=make_iteration_plan()),
        "app.run.run_claude_task": MagicMock(return_value=0),
        "app.run._run_preflight_check": MagicMock(return_value=False),
        "app.run._handle_skill_dispatch": MagicMock(return_value=(False, "test mission")),
        "app.run._start_mission_in_file": MagicMock(return_value=True),
        "app.run._finalize_mission": MagicMock(),
        "app.run._notify": MagicMock(),
        "app.run._notify_mission_end": MagicMock(),
        "app.run._commit_instance": MagicMock(),
        "app.run._sleep_between_runs": MagicMock(),
        "app.run._cleanup_temp": MagicMock(),
        "app.run_log._reset_terminal": MagicMock(),
        "app.git_prep.prepare_project_branch": mock_prep,
        "app.prompt_builder.build_agent_prompt": MagicMock(return_value="prompt"),
        "app.loop_manager.create_pending_file": MagicMock(),
        "app.mission_runner.build_mission_command": MagicMock(return_value=(["echo"], [])),
        "app.mission_runner.run_post_mission": MagicMock(return_value={}),
        "app.mission_runner.parse_claude_output": MagicMock(return_value="ok"),
    }
    if extra_patches:
        patches.update(extra_patches)
    with ExitStack() as stack:
        for target, mock_obj in patches.items():
            stack.enter_context(patch(target, mock_obj))
        yield mock_prep
