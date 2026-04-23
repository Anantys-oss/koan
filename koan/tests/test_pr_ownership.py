"""Tests for PR ownership checks in rebase, ci_check, and check_runner.

When a PR was opened by another koan instance (different branch prefix),
the skills should refuse to operate on it.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_handler(skill_name):
    """Load a skill handler module by name."""
    path = Path(__file__).parent.parent / "skills" / "core" / skill_name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"{skill_name}_handler", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="test",
        args="",
        send_message=MagicMock(),
    )


def _project_patches():
    """Common patches for project resolution."""
    return [
        patch("app.utils.resolve_project_path", return_value="/home/koan"),
        patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]),
    ]


# ---------------------------------------------------------------------------
# /ci_check — ownership
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stale module cache — simulates long-running bridge after code update
# ---------------------------------------------------------------------------

class TestStaleCachedModule:
    """Handlers must not crash when github_skill_helpers was cached before
    is_own_pr was added (e.g. bridge process not restarted after update)."""

    @pytest.mark.parametrize("skill_name", ["rebase", "ci_check"])
    def test_handler_recovers_when_is_own_pr_missing(self, skill_name, ctx):
        """Handler's inline hasattr guard reloads the module when is_own_pr
        is missing (stale sys.modules cache after auto-update)."""
        import json
        import sys

        ctx.args = "https://github.com/sukria/koan/pull/55"

        module = sys.modules.get("app.github_skill_helpers")
        if module is None:
            import importlib
            module = importlib.import_module("app.github_skill_helpers")

        original_fn = getattr(module, "is_own_pr", None)
        if original_fn is None:
            pytest.skip("is_own_pr already absent — cannot simulate stale cache")
        del module.is_own_pr
        assert not hasattr(module, "is_own_pr"), "setup: is_own_pr should be gone"

        handler = _load_handler(skill_name)
        try:
            with _project_patches()[0], _project_patches()[1], \
                 patch("app.github.run_gh",
                       return_value=json.dumps({"headRefName": "koan/fix-thing"})), \
                 patch("app.config.get_branch_prefix", return_value="koan/"), \
                 patch("app.utils.insert_pending_mission"):
                result = handler.handle(ctx)
                assert "has no attribute 'is_own_pr'" not in result, (
                    f"Handler exposed stale-module AttributeError: {result}"
                )
                assert "queued" in result.lower() or "Not my PR" in result, (
                    f"Unexpected response: {result}"
                )
        finally:
            module.is_own_pr = original_fn


class TestCiCheckOwnership:
    @pytest.fixture
    def handler(self):
        return _load_handler("ci_check")

    def test_rejects_pr_from_other_instance(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(False, "other-instance/branch")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "Not my PR" in result
            assert "other-instance/branch" in result
            mock_insert.assert_not_called()

    def test_accepts_pr_from_own_instance(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/55"
        with _project_patches()[0], _project_patches()[1], \
             patch("app.github_skill_helpers.is_own_pr",
                   return_value=(True, "koan/fix-ci")), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            mock_insert.assert_called_once()


# ---------------------------------------------------------------------------
# check_runner — ownership guard on auto-queued rebase
# ---------------------------------------------------------------------------

class TestCheckRunnerOwnership:
    def _make_pr_data(self, head_branch="koan/my-branch", mergeable="CONFLICTING"):
        return {
            "state": "OPEN",
            "mergeable": mergeable,
            "reviewDecision": None,
            "updatedAt": "2026-04-02T10:00:00Z",
            "headRefName": head_branch,
            "baseRefName": "main",
            "title": "Some PR",
            "isDraft": False,
            "author": {"login": "bot"},
            "url": "https://github.com/sukria/koan/pull/99",
        }

    def test_skips_rebase_for_foreign_pr(self, tmp_path):
        """When a PR needs rebase but isn't ours, check_runner should skip it."""
        from app.check_runner import run_check

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_md = instance_dir / "missions.md"
        missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")

        pr_data = self._make_pr_data(
            head_branch="other-bot/fix-thing",
            mergeable="CONFLICTING",
        )
        notify = MagicMock()

        with patch("app.check_runner._fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.config.get_branch_prefix", return_value="koan/"), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            success, msg = run_check(
                url="https://github.com/sukria/koan/pull/99",
                instance_dir=str(instance_dir),
                koan_root=str(tmp_path),
                notify_fn=notify,
            )
            assert success is True
            assert "not mine" in msg.lower()
            mock_insert.assert_not_called()

    def test_queues_rebase_for_own_pr(self, tmp_path):
        """When a PR needs rebase and IS ours, check_runner should queue it."""
        from app.check_runner import run_check

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_md = instance_dir / "missions.md"
        missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")

        pr_data = self._make_pr_data(
            head_branch="koan/fix-thing",
            mergeable="CONFLICTING",
        )
        notify = MagicMock()

        with patch("app.check_runner._fetch_pr_metadata", return_value=pr_data), \
             patch("app.check_tracker.has_changed", return_value=True), \
             patch("app.check_tracker.mark_checked"), \
             patch("app.config.get_branch_prefix", return_value="koan/"), \
             patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            success, msg = run_check(
                url="https://github.com/sukria/koan/pull/99",
                instance_dir=str(instance_dir),
                koan_root=str(tmp_path),
                notify_fn=notify,
            )
            assert success is True
            assert "Rebase queued" in msg
            mock_insert.assert_called_once()


# ---------------------------------------------------------------------------
# is_own_pr helper
# ---------------------------------------------------------------------------

class TestIsOwnPr:
    def test_returns_true_for_matching_prefix(self):
        from app.github_skill_helpers import is_own_pr

        mock_response = json.dumps({"headRefName": "koan.toddr.bot/fix-bug"})
        with patch("app.github.run_gh", return_value=mock_response), \
             patch("app.config.get_branch_prefix", return_value="koan.toddr.bot/"):
            owned, branch = is_own_pr("sukria", "koan", "42")
            assert owned is True
            assert branch == "koan.toddr.bot/fix-bug"

    def test_returns_false_for_different_prefix(self):
        from app.github_skill_helpers import is_own_pr

        mock_response = json.dumps({"headRefName": "other-bot/fix-bug"})
        with patch("app.github.run_gh", return_value=mock_response), \
             patch("app.config.get_branch_prefix", return_value="koan.toddr.bot/"):
            owned, branch = is_own_pr("sukria", "koan", "42")
            assert owned is False
            assert branch == "other-bot/fix-bug"

    def test_returns_false_for_human_branch(self):
        from app.github_skill_helpers import is_own_pr

        mock_response = json.dumps({"headRefName": "feature/new-thing"})
        with patch("app.github.run_gh", return_value=mock_response), \
             patch("app.config.get_branch_prefix", return_value="koan/"):
            owned, branch = is_own_pr("sukria", "koan", "42")
            assert owned is False
            assert branch == "feature/new-thing"
