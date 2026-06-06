"""Tests for Gogs URL support across core skills.

Verifies that skills correctly detect and handle Gogs PR/issue URLs
instead of (or in addition to) GitHub URLs.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


GOGS_HOST = "https://git.example.com"


def _make_ctx(tmp_path, args="", command="test"):
    """Create a minimal SkillContext with missions.md."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name=command,
        args=args,
        send_message=MagicMock(),
    )


def _load_handler(skill_name):
    handler_path = (
        Path(__file__).parent.parent / "skills" / "core" / skill_name / "handler.py"
    )
    spec = importlib.util.spec_from_file_location(f"{skill_name}_handler", str(handler_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# github_skill_helpers — Gogs helper functions
# ---------------------------------------------------------------------------

class TestGogsHelpers:
    """Tests for try_extract_gogs_pr / try_extract_gogs_issue / try_extract_gogs_pr_or_issue."""

    def test_try_extract_gogs_pr_returns_tuple(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        from app.github_skill_helpers import try_extract_gogs_pr
        result = try_extract_gogs_pr(f"{GOGS_HOST}/owner/myrepo/pulls/42")
        assert result is not None
        owner, repo, number, url = result
        assert owner == "owner"
        assert repo == "myrepo"
        assert number == "42"
        assert "pulls/42" in url

    def test_try_extract_gogs_pr_returns_none_without_host(self, monkeypatch):
        monkeypatch.delenv("KOAN_GOGS_HOST", raising=False)
        from app.github_skill_helpers import try_extract_gogs_pr
        # Reload to pick up missing env var
        import importlib
        import app.github_skill_helpers as m
        importlib.reload(m)
        result = m.try_extract_gogs_pr("https://git.other.com/owner/repo/pulls/1")
        assert result is None

    def test_try_extract_gogs_pr_returns_none_for_github_url(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        from app.github_skill_helpers import try_extract_gogs_pr
        result = try_extract_gogs_pr("https://github.com/owner/repo/pull/42")
        assert result is None

    def test_try_extract_gogs_issue_returns_tuple(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        from app.github_skill_helpers import try_extract_gogs_issue
        result = try_extract_gogs_issue(f"{GOGS_HOST}/owner/myrepo/issues/7")
        assert result is not None
        owner, repo, number, url = result
        assert number == "7"
        assert "issues/7" in url

    def test_try_extract_gogs_pr_or_issue_prefers_pr(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        from app.github_skill_helpers import try_extract_gogs_pr_or_issue
        result = try_extract_gogs_pr_or_issue(f"{GOGS_HOST}/owner/myrepo/pulls/5")
        assert result is not None
        owner, repo, number, url, type_label = result
        assert type_label == "PR"

    def test_try_extract_gogs_pr_or_issue_falls_back_to_issue(self, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        from app.github_skill_helpers import try_extract_gogs_pr_or_issue
        result = try_extract_gogs_pr_or_issue(f"{GOGS_HOST}/owner/myrepo/issues/5")
        assert result is not None
        owner, repo, number, url, type_label = result
        assert type_label == "issue"


# ---------------------------------------------------------------------------
# /pr skill — Gogs routing
# ---------------------------------------------------------------------------

class TestPrSkillGogsRouting:
    """Tests that /pr detects and routes Gogs PR URLs correctly."""

    def test_gogs_pr_url_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("pr")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/42")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.gogs_pr_review.run_pr_review_gogs", return_value=(True, "ok")),
        ):
            result = handler.handle(ctx)
        assert result is None  # success path sends via send_message and returns None

    def test_github_pr_url_still_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("pr")
        ctx = _make_ctx(tmp_path, args="https://github.com/owner/myrepo/pull/1")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.pr_review.run_pr_review", return_value=(True, "ok")),
        ):
            result = handler.handle(ctx)
        assert result is None

    def test_unknown_url_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("pr")
        ctx = _make_ctx(tmp_path, args="https://gitlab.com/owner/repo/merge_requests/1")
        result = handler.handle(ctx)
        assert "No valid PR URL" in result


# ---------------------------------------------------------------------------
# /review skill — Gogs routing
# ---------------------------------------------------------------------------

class TestReviewSkillGogsRouting:
    def test_gogs_pr_queues_review_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("review")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/3", command="review")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
        ):
            result = handler.handle(ctx)
        assert "Review queued" in result
        assert "Gogs PR" in result
        assert "#3" in result

    def test_gogs_issue_queues_review_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("review")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/issues/5", command="review")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
        ):
            result = handler.handle(ctx)
        assert "Review queued" in result
        assert "Gogs issue" in result
        assert "#5" in result


# ---------------------------------------------------------------------------
# /rebase skill — Gogs routing
# ---------------------------------------------------------------------------

class TestRebaseSkillGogsRouting:
    def test_gogs_pr_queues_rebase_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("rebase")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/7", command="rebase")

        fake_pr_view = {"headRefName": "koan/fix-something", "baseRefName": "main"}
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
            patch("app.forge.gogs.GogsForge.pr_view", return_value=fake_pr_view),
            patch("app.config.get_branch_prefix", return_value="koan/"),
        ):
            result = handler.handle(ctx)
        assert "Rebase queued" in result
        assert "Gogs PR #7" in result

    def test_gogs_pr_foreign_branch_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("rebase")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/7", command="rebase")

        fake_pr_view = {"headRefName": "feature/someone-else", "baseRefName": "main"}
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
            patch("app.forge.gogs.GogsForge.pr_view", return_value=fake_pr_view),
            patch("app.config.get_branch_prefix", return_value="koan/"),
            patch("app.config.is_rebase_foreign_prs_allowed", return_value=False),
        ):
            result = handler.handle(ctx)
        assert "Not my PR" in result


# ---------------------------------------------------------------------------
# /squash skill — Gogs routing
# ---------------------------------------------------------------------------

class TestSquashSkillGogsRouting:
    def test_gogs_pr_queues_squash_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("squash")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/9", command="squash")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
        ):
            result = handler.handle(ctx)
        assert "Squash queued" in result
        assert "Gogs PR #9" in result


# ---------------------------------------------------------------------------
# /recreate skill — Gogs routing
# ---------------------------------------------------------------------------

class TestRecreateSkillGogsRouting:
    def test_gogs_pr_queues_recreate_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("recreate")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/11", command="recreate")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
        ):
            result = handler.handle(ctx)
        assert "Recreate queued" in result
        assert "Gogs PR #11" in result


# ---------------------------------------------------------------------------
# /review_rebase skill — Gogs routing
# ---------------------------------------------------------------------------

class TestReviewRebaseSkillGogsRouting:
    def test_gogs_pr_queues_both_missions(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("review_rebase")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/13", command="rr")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
        ):
            result = handler.handle(ctx)
        assert "Review + rebase combo queued" in result
        assert "Gogs PR #13" in result


# ---------------------------------------------------------------------------
# /check skill — Gogs routing
# ---------------------------------------------------------------------------

class TestCheckSkillGogsRouting:
    def test_gogs_pr_queues_check_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("check")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/pulls/15", command="check")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
        ):
            result = handler.handle(ctx)
        assert "Check queued" in result
        assert "Gogs PR" in result


# ---------------------------------------------------------------------------
# /plan skill — Gogs routing
# ---------------------------------------------------------------------------

class TestPlanSkillGogsRouting:
    def test_gogs_issue_queues_plan_mission(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_GOGS_HOST", GOGS_HOST)
        handler = _load_handler("plan")
        ctx = _make_ctx(tmp_path, args=f"{GOGS_HOST}/owner/myrepo/issues/17", command="plan")
        with (
            patch("app.utils.resolve_project_path", return_value=str(tmp_path / "myrepo")),
            patch("app.utils.project_name_for_path", return_value="myrepo"),
            patch("app.utils.get_known_projects", return_value=[("myrepo", str(tmp_path / "myrepo"))]),
        ):
            result = handler.handle(ctx)
        assert "Plan queued" in result
        assert "Gogs issue" in result
        assert "#17" in result
