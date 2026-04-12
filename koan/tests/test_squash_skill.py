"""Tests for the /squash core skill -- handler, SKILL.md, runner, and registry."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "squash" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("squash_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="squash",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() -- usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/squash" in result

    def test_invalid_url_returns_error(self, handler, ctx):
        ctx.args = "not-a-url"
        result = handler.handle(ctx)
        assert "\u274c" in result
        assert "No valid" in result

    def test_non_pr_url_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/issues/42"
        result = handler.handle(ctx)
        assert "\u274c" in result

    def test_unknown_repo_returns_error(self, handler, ctx):
        ctx.args = "https://github.com/unknown/repo/pull/1"
        with patch("app.utils.resolve_project_path", return_value=None), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler.handle(ctx)
            assert "\u274c" in result
            assert "repo" in result.lower()


# ---------------------------------------------------------------------------
# handle() -- mission queuing
# ---------------------------------------------------------------------------

class TestMissionQueuing:
    def test_valid_url_queues_mission(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "#42" in result
            mock_insert.assert_called_once()
            mission_entry = mock_insert.call_args[0][1]
            assert "[project:koan]" in mission_entry
            assert "/squash https://github.com/sukria/koan/pull/42" in mission_entry

    def test_returns_ack_message(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission"):
            result = handler.handle(ctx)
            assert result == "Squash queued for PR #42 (sukria/koan)"

    def test_mission_uses_squash_not_rebase(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            handler.handle(ctx)
            entry = mock_insert.call_args[0][1]
            assert "/squash " in entry
            assert "/rebase " not in entry


# ---------------------------------------------------------------------------
# SKILL.md -- structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "squash" / "SKILL.md"
        )
        assert skill is not None
        assert skill.name == "squash"
        assert skill.scope == "core"
        assert skill.group == "pr"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "squash"

    def test_skill_has_alias(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "squash" / "SKILL.md"
        )
        assert "sq" in skill.commands[0].aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("squash")
        assert skill is not None
        assert skill.name == "squash"

    def test_alias_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("sq")
        assert skill is not None
        assert skill.name == "squash"

    def test_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_prompt_template_exists(self):
        prompt_path = (
            Path(__file__).parent.parent
            / "skills" / "core" / "squash" / "prompts" / "squash.md"
        )
        assert prompt_path.exists()

    def test_prompt_has_placeholders(self):
        prompt_path = (
            Path(__file__).parent.parent
            / "skills" / "core" / "squash" / "prompts" / "squash.md"
        )
        content = prompt_path.read_text()
        assert "{TITLE}" in content or "{{TITLE}}" in content
        assert "{DIFF}" in content or "{{DIFF}}" in content
        assert "{BASE}" in content or "{{BASE}}" in content


# ---------------------------------------------------------------------------
# skill_dispatch -- registration
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_squash_in_skill_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS
        assert "squash" in _SKILL_RUNNERS
        assert _SKILL_RUNNERS["squash"] == "app.squash_pr"

    def test_squash_validates_pr_url(self):
        from app.skill_dispatch import validate_skill_args
        error = validate_skill_args("squash", "no url here")
        assert error is not None
        assert "PR URL" in error

    def test_squash_accepts_valid_url(self):
        from app.skill_dispatch import validate_skill_args
        error = validate_skill_args(
            "squash", "https://github.com/owner/repo/pull/42"
        )
        assert error is None

    def test_squash_builds_command(self):
        from app.skill_dispatch import build_skill_command
        cmd = build_skill_command(
            command="squash",
            args="https://github.com/owner/repo/pull/42",
            project_name="myproj",
            project_path="/path/to/proj",
            koan_root="/root",
            instance_dir="/instance",
        )
        assert cmd is not None
        assert "app.squash_pr" in " ".join(cmd)
        assert "https://github.com/owner/repo/pull/42" in cmd
        assert "--project-path" in cmd
        assert "/path/to/proj" in cmd


# ---------------------------------------------------------------------------
# squash_pr -- runner unit tests
# ---------------------------------------------------------------------------

class TestSquashRunner:
    def test_extract_between(self):
        from app.squash_pr import _extract_between
        text = "before===START===content here===END===after"
        assert _extract_between(text, "===START===", "===END===") == "content here"

    def test_extract_between_no_end(self):
        from app.squash_pr import _extract_between
        text = "before===START===content here"
        assert _extract_between(text, "===START===", "===END===") == "content here"

    def test_extract_between_no_start(self):
        from app.squash_pr import _extract_between
        text = "no markers here"
        assert _extract_between(text, "===START===", "===END===") == ""

    def test_parse_squash_output(self):
        from app.squash_pr import _parse_squash_output
        output = (
            "===COMMIT_MESSAGE===\n"
            "feat: add new feature\n\n"
            "This adds X and Y.\n"
            "===PR_TITLE===\n"
            "feat: add new feature\n"
            "===PR_DESCRIPTION===\n"
            "## What\nAdded a feature.\n"
            "===END==="
        )
        result = _parse_squash_output(output, {"title": "old"})
        assert "feat: add new feature" in result["commit_message"]
        assert result["pr_title"] == "feat: add new feature"
        assert "Added a feature" in result["pr_description"]

    def test_parse_squash_output_fallback(self):
        from app.squash_pr import _parse_squash_output
        result = _parse_squash_output("garbage output", {"title": "fallback"})
        assert result["commit_message"] == "fallback"
        assert result["pr_title"] == "fallback"

    def test_build_squash_comment(self):
        from app.squash_pr import _build_squash_comment
        comment = _build_squash_comment(
            pr_number="42",
            branch="feature-x",
            base="main",
            commit_count=5,
            actions_log=["Squashed 5 commits into 1", "Force-pushed"],
            squash_text={"commit_message": "feat: add feature x"},
        )
        assert "5 commits" in comment
        assert "feature-x" in comment
        assert "feat: add feature x" in comment
        assert "Koan" in comment

    def test_run_squash_merged_pr_skips(self):
        """Squash should skip if PR is already merged."""
        from app.squash_pr import run_squash

        mock_context = {
            "title": "test",
            "body": "",
            "branch": "feat",
            "base": "main",
            "state": "MERGED",
            "author": "me",
            "head_owner": "me",
            "url": "",
            "diff": "",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
            "has_pending_reviews": False,
        }

        with patch("app.squash_pr.fetch_pr_context", return_value=mock_context):
            ok, summary = run_squash(
                "owner", "repo", "1", "/tmp/proj",
                notify_fn=MagicMock(),
            )
            assert ok is True
            assert "merged" in summary.lower()

    def test_run_squash_single_commit_skips(self):
        """Squash should skip if PR already has 1 commit."""
        from app.squash_pr import run_squash

        mock_context = {
            "title": "test",
            "body": "",
            "branch": "feat",
            "base": "main",
            "state": "OPEN",
            "author": "me",
            "head_owner": "me",
            "url": "",
            "diff": "",
            "review_comments": "",
            "reviews": "",
            "issue_comments": "",
            "has_pending_reviews": False,
        }

        with patch("app.squash_pr.fetch_pr_context", return_value=mock_context), \
             patch("app.squash_pr._get_current_branch", return_value="main"), \
             patch("app.squash_pr._checkout_pr_branch", return_value="origin"), \
             patch("app.squash_pr._run_git", return_value=""), \
             patch("app.squash_pr._fetch_branch", return_value=""), \
             patch("app.squash_pr._count_commits_since_base", return_value=1), \
             patch("app.squash_pr._safe_checkout"), \
             patch("app.squash_pr._find_remote_for_repo", return_value="origin"):
            ok, summary = run_squash(
                "owner", "repo", "1", "/tmp/proj",
                notify_fn=MagicMock(),
            )
            assert ok is True
            assert "nothing to squash" in summary.lower()

    def test_main_cli_entry(self):
        """CLI entry point should parse URL and invoke run_squash."""
        from app.squash_pr import main

        with patch("app.squash_pr.run_squash", return_value=(True, "done")) as mock_run:
            code = main([
                "https://github.com/owner/repo/pull/42",
                "--project-path", "/tmp/proj",
            ])
            assert code == 0
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == "owner"
            assert mock_run.call_args[0][1] == "repo"
            assert mock_run.call_args[0][2] == "42"

    def test_main_cli_invalid_url(self):
        from app.squash_pr import main
        code = main(["not-a-url", "--project-path", "/tmp"])
        assert code == 1


class TestSquashHelpers:
    def test_count_commits_since_base_happy_path(self):
        from app.squash_pr import _count_commits_since_base
        with patch("app.squash_pr._run_git") as mock_git:
            mock_git.side_effect = ["abc123\n", "sha1\nsha2\nsha3\n"]
            assert _count_commits_since_base("origin/main", "/tmp") == 3

    def test_count_commits_since_base_no_commits(self):
        from app.squash_pr import _count_commits_since_base
        with patch("app.squash_pr._run_git") as mock_git:
            mock_git.side_effect = ["abc\n", "\n"]
            assert _count_commits_since_base("origin/main", "/tmp") == 0

    def test_count_commits_since_base_error_returns_zero(self):
        from app.squash_pr import _count_commits_since_base
        with patch("app.squash_pr._run_git", side_effect=RuntimeError("boom")):
            assert _count_commits_since_base("origin/main", "/tmp") == 0

    def test_squash_commits_runs_reset_and_commit(self):
        from app.squash_pr import _squash_commits
        with patch("app.squash_pr._run_git") as mock_git:
            mock_git.side_effect = ["abc123\n", "", ""]
            assert _squash_commits("origin/main", "/tmp", "feat: x") is True
            calls = [c.args[0] for c in mock_git.call_args_list]
            assert ["git", "merge-base", "origin/main", "HEAD"] in calls
            assert ["git", "reset", "--soft", "abc123"] in calls
            assert ["git", "commit", "-m", "feat: x"] in calls

    def test_generate_squash_text_success(self):
        from app.squash_pr import _generate_squash_text
        output = (
            "===COMMIT_MESSAGE===\nfeat: hello\n"
            "===PR_TITLE===\nfeat: hello\n"
            "===PR_DESCRIPTION===\ndesc here\n===END==="
        )
        context = {"title": "old", "body": "oldbody", "branch": "f", "base": "main"}
        with patch("app.squash_pr.load_prompt_or_skill", return_value="prompt"), \
             patch("app.squash_pr.get_model_config", return_value={
                 "lightweight": "m1", "mission": "m2", "fallback": "m3"}), \
             patch("app.squash_pr.build_full_command", return_value=["fake"]), \
             patch("app.squash_pr.run_claude", return_value={"success": True, "output": output}):
            result = _generate_squash_text(context, "diff body")
        assert "feat: hello" in result["commit_message"]

    def test_generate_squash_text_fallback_on_failure(self):
        from app.squash_pr import _generate_squash_text
        context = {"title": "fb-title", "body": "fb-body", "branch": "f", "base": "main"}
        with patch("app.squash_pr.load_prompt_or_skill", return_value="p"), \
             patch("app.squash_pr.get_model_config", return_value={
                 "lightweight": "m1", "mission": "m2", "fallback": "m3"}), \
             patch("app.squash_pr.build_full_command", return_value=["fake"]), \
             patch("app.squash_pr.run_claude", return_value={"success": False, "output": ""}):
            result = _generate_squash_text(context, "")
        assert result["commit_message"] == "fb-title"

    def test_force_push_first_remote_succeeds(self):
        from app.squash_pr import _force_push
        with patch("app.squash_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.squash_pr._run_git") as mock_git:
            mock_git.return_value = ""
            assert _force_push("feat", "/tmp") == "origin"

    def test_force_push_falls_back_to_plain_force(self):
        from app.squash_pr import _force_push
        def fake_git(cmd, cwd=None, **kw):
            if "--force-with-lease" in cmd:
                raise RuntimeError("rejected")
            return ""
        with patch("app.squash_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.squash_pr._run_git", side_effect=fake_git):
            assert _force_push("feat", "/tmp") == "origin"

    def test_force_push_all_fail_raises(self):
        from app.squash_pr import _force_push
        with patch("app.squash_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.squash_pr._run_git", side_effect=RuntimeError("nope")):
            with pytest.raises(RuntimeError, match="all remotes rejected"):
                _force_push("feat", "/tmp")

    def test_checkout_pr_branch_first_remote_wins(self):
        from app.squash_pr import _checkout_pr_branch
        with patch("app.squash_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.squash_pr._fetch_branch"), \
             patch("app.squash_pr._run_git", return_value=""):
            assert _checkout_pr_branch("feat", "/tmp") == "origin"

    def test_checkout_pr_branch_fork_fallback(self):
        from app.squash_pr import _checkout_pr_branch
        calls = []
        def fake_fetch(remote, branch, cwd=None):
            calls.append(remote)
            if remote in ("origin",):
                raise RuntimeError("not found")
        with patch("app.squash_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.squash_pr._fetch_branch", side_effect=fake_fetch), \
             patch("app.squash_pr._run_git", return_value=""):
            r = _checkout_pr_branch("feat", "/tmp", head_owner="alice", repo="koan")
            assert r == "fork-alice"

    def test_checkout_pr_branch_all_fail_raises(self):
        from app.squash_pr import _checkout_pr_branch
        with patch("app.squash_pr._ordered_remotes", return_value=["origin"]), \
             patch("app.squash_pr._fetch_branch", side_effect=RuntimeError("nope")), \
             patch("app.squash_pr._run_git", return_value=""):
            with pytest.raises(RuntimeError, match="not found on any remote"):
                _checkout_pr_branch("feat", "/tmp")


class TestRunSquashFlow:
    @pytest.fixture
    def base_context(self):
        return {
            "title": "old title", "body": "old body", "branch": "feat",
            "base": "main", "state": "OPEN", "author": "me",
            "head_owner": "me", "url": "", "diff": "",
            "review_comments": "", "reviews": "", "issue_comments": "",
            "has_pending_reviews": False,
        }

    def _std_patches(self, ctx, commit_count=3, **overrides):
        squash_text = {"commit_message": "m", "pr_title": "t", "pr_description": "d"}
        defaults = dict(
            fetch_pr_context=ctx,
            _get_current_branch="main",
            _checkout_pr_branch="origin",
            _find_remote_for_repo="origin",
            _fetch_branch="",
            _run_git="",
            _count_commits_since_base=commit_count,
            _generate_squash_text=squash_text,
            _squash_commits=True,
            _force_push="origin",
            run_gh="ok",
            _safe_checkout=None,
            sanitize_github_comment=lambda s: s,
        )
        defaults.update(overrides)
        patches = []
        for name, val in defaults.items():
            target = f"app.squash_pr.{name}"
            if callable(val) and not isinstance(val, MagicMock):
                patches.append(patch(target, side_effect=val))
            elif val is None:
                patches.append(patch(target))
            else:
                patches.append(patch(target, return_value=val))
        return patches

    def _run(self, patches, *args, **kwargs):
        from app.squash_pr import run_squash
        for p in patches:
            p.start()
        try:
            return run_squash(*args, **kwargs)
        finally:
            for p in patches:
                p.stop()

    def test_full_success(self, base_context):
        ok, summary = self._run(
            self._std_patches(base_context),
            "o", "r", "42", "/tmp", notify_fn=MagicMock(),
        )
        assert ok is True
        assert "#42" in summary and "Squashed 3 commits" in summary

    def test_fetch_context_fails(self):
        from app.squash_pr import run_squash
        with patch("app.squash_pr.fetch_pr_context", side_effect=RuntimeError("down")):
            ok, s = run_squash("o", "r", "1", "/tmp", notify_fn=MagicMock())
        assert ok is False and "down" in s

    def test_empty_branch_returns_error(self, base_context):
        base_context["branch"] = ""
        with patch("app.squash_pr.fetch_pr_context", return_value=base_context):
            ok, s = self._run([], "o", "r", "1", "/tmp", notify_fn=MagicMock())
        assert ok is False and "branch" in s.lower()

    def test_checkout_failure(self, base_context):
        p = self._std_patches(
            base_context,
            _checkout_pr_branch=RuntimeError("no branch"),
        )
        # Override _checkout_pr_branch to raise
        for i, pp in enumerate(p):
            if "_checkout_pr_branch" in str(pp.attribute):
                p[i] = patch("app.squash_pr._checkout_pr_branch",
                             side_effect=RuntimeError("no branch"))
        ok, s = self._run(p, "o", "r", "1", "/tmp", notify_fn=MagicMock())
        assert ok is False and "checkout" in s.lower()

    def test_squash_step_fails(self, base_context):
        p = self._std_patches(base_context)
        # Replace _squash_commits patch with one that raises
        new_patches = []
        for pp in p:
            if hasattr(pp, 'attribute') and pp.attribute == '_squash_commits':
                new_patches.append(
                    patch("app.squash_pr._squash_commits",
                          side_effect=RuntimeError("conflict")))
            else:
                new_patches.append(pp)
        ok, s = self._run(new_patches, "o", "r", "7", "/tmp", notify_fn=MagicMock())
        assert ok is False and "Squash failed" in s

    def test_force_push_fails(self, base_context):
        p = self._std_patches(base_context)
        new_patches = []
        for pp in p:
            if hasattr(pp, 'attribute') and pp.attribute == '_force_push':
                new_patches.append(
                    patch("app.squash_pr._force_push",
                          side_effect=RuntimeError("auth")))
            else:
                new_patches.append(pp)
        ok, s = self._run(new_patches, "o", "r", "7", "/tmp", notify_fn=MagicMock())
        assert ok is False and "Push failed" in s

    def test_pr_edit_failures_non_fatal(self, base_context):
        def fake_gh(*args, **kw):
            if "edit" in args:
                raise RuntimeError("gh exploded")
            return "ok"
        p = self._std_patches(base_context, run_gh=fake_gh)
        ok, s = self._run(p, "o", "r", "11", "/tmp", notify_fn=MagicMock())
        assert ok is True and "non-fatal" in s


# ---------------------------------------------------------------------------
# GitHub @mention integration
# ---------------------------------------------------------------------------

class TestGitHubMention:
    """Verify squash is discoverable and usable via GitHub @mentions."""

    def test_skill_has_github_enabled(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "squash" / "SKILL.md"
        )
        assert skill.github_enabled is True

    def test_skill_has_github_context_aware(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(
            Path(__file__).parent.parent / "skills" / "core" / "squash" / "SKILL.md"
        )
        assert skill.github_context_aware is True

    def test_handler_accepts_url_with_trailing_context(self, handler, ctx):
        """Simulates the GitHub mention path where URL + optional context are injected."""
        ctx.args = "https://github.com/sukria/koan/pull/42 keep the first commit message"
        with patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]), \
             patch("app.utils.insert_pending_mission") as mock_insert:
            result = handler.handle(ctx)
            assert "queued" in result.lower()
            assert "#42" in result
            mock_insert.assert_called_once()

    def test_build_mission_from_command_includes_squash(self):
        """build_mission_from_command produces correct mission for squash."""
        from app.skills import SkillRegistry
        from app.github_command_handler import build_mission_from_command

        registry = SkillRegistry(
            Path(__file__).parent.parent / "skills" / "core"
        )
        skill = registry.find_by_command("squash")
        assert skill is not None

        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/99"}}
        mission = build_mission_from_command(skill, "squash", "", notif, "koan")
        assert "/squash https://github.com/o/r/pull/99" in mission
        assert "[project:koan]" in mission

    def test_build_mission_with_context(self):
        """Extra context from @mention is appended to the mission."""
        from app.skills import SkillRegistry
        from app.github_command_handler import build_mission_from_command

        registry = SkillRegistry(
            Path(__file__).parent.parent / "skills" / "core"
        )
        skill = registry.find_by_command("squash")

        notif = {"subject": {"url": "https://api.github.com/repos/o/r/pulls/99"}}
        mission = build_mission_from_command(
            skill, "squash", "keep the first commit message", notif, "koan"
        )
        assert "keep the first commit message" in mission
        assert "/squash" in mission
