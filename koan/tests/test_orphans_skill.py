"""Tests for the /orphans core skill — handler, SKILL.md, and orphan hint."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "orphans" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("orphans_handler", str(HANDLER_PATH))
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
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="orphans",
        args="",
        send_message=MagicMock(),
    )


class TestHandleRouting:
    def test_no_args_no_projects_returns_usage(self, handler, ctx):
        with patch.object(handler, "_resolve_project", return_value=("", None)):
            result = handler.handle(ctx)
        assert "Usage:" in result

    def test_no_args_multi_project_prompts_selection(self, handler, ctx):
        with patch.object(handler, "_resolve_project",
                          return_value=("_prompt_a, b", None)):
            result = handler.handle(ctx)
        assert "Which project?" in result
        assert "a, b" in result

    def test_no_orphans_returns_clean(self, handler, ctx):
        ctx.args = "myproject"
        with patch.object(handler, "_resolve_project",
                          return_value=("myproject", "/path/to/proj")):
            with patch.object(handler, "_find_orphans", return_value=[]):
                result = handler.handle(ctx)
        assert "No orphan branches" in result

    def test_orphan_detection_error(self, handler, ctx):
        ctx.args = "myproject"
        with patch.object(handler, "_resolve_project",
                          return_value=("myproject", "/path/to/proj")):
            with patch.object(handler, "_find_orphans", return_value=None):
                result = handler.handle(ctx)
        assert "Failed" in result


class TestResolveProject:
    def test_single_project_auto_selects(self, handler):
        with patch("app.utils.get_known_projects",
                   return_value=[("myproj", "/p")]):
            name, path = handler._resolve_project("", MagicMock())
        assert name == "myproj"
        assert path == "/p"

    def test_explicit_project_resolves(self, handler):
        with patch("app.utils.get_known_projects",
                   return_value=[("myproj", "/p"), ("other", "/o")]):
            with patch("app.utils.resolve_project_from_list",
                       return_value=("myproj", "/p")):
                name, path = handler._resolve_project("myproj", MagicMock())
        assert name == "myproj"

    def test_unknown_project_returns_none(self, handler):
        with patch("app.utils.get_known_projects",
                   return_value=[("myproj", "/p")]):
            with patch("app.utils.resolve_project_from_list",
                       return_value=(None, None)):
                name, path = handler._resolve_project("bad", MagicMock())
        assert path is None


class TestFindOrphans:
    def test_returns_orphan_list(self, handler, tmp_path):
        mock_sync = MagicMock()
        mock_sync.get_orphan_branches.return_value = ["koan/branch-a", "koan/branch-b"]
        with patch("app.git_utils.run_git", return_value=(0, "", "")):
            with patch("app.git_sync.GitSync", return_value=mock_sync):
                result = handler._find_orphans("proj", str(tmp_path), str(tmp_path))
        assert result == ["koan/branch-a", "koan/branch-b"]

    def test_returns_none_on_error(self, handler, tmp_path):
        mock_sync = MagicMock()
        mock_sync.get_orphan_branches.side_effect = RuntimeError("fail")
        with patch("app.git_utils.run_git", return_value=(0, "", "")):
            with patch("app.git_sync.GitSync", return_value=mock_sync):
                result = handler._find_orphans("proj", str(tmp_path), str(tmp_path))
        assert result is None


class TestRecoverOne:
    def test_successful_rebase_and_pr(self, handler):
        with patch("app.git_prep.detect_remote_default_branch", return_value="main"):
            with patch("app.git_utils.run_git") as mock_git:
                mock_git.return_value = (0, "", "")
                with patch("app.github.pr_create",
                           return_value="https://github.com/o/r/pull/42"):
                    result = handler._recover_one("koan/my-fix", "/project")
        assert result["rebased"] is True
        assert result["pr_url"] == "https://github.com/o/r/pull/42"
        assert result["error"] is None

    def test_rebase_failure_still_creates_pr(self, handler):
        call_count = {"n": 0}

        def mock_git(*args, **kwargs):
            call_count["n"] += 1
            if args[0] == "rebase" and args[1] == "origin/main":
                return (1, "", "conflict")
            return (0, "", "")

        with patch("app.git_prep.detect_remote_default_branch", return_value="main"):
            with patch("app.git_utils.run_git", side_effect=mock_git):
                with patch("app.github.pr_create",
                           return_value="https://github.com/o/r/pull/99"):
                    result = handler._recover_one("koan/broken", "/project")
        assert result["rebased"] is False
        assert result["pr_url"] == "https://github.com/o/r/pull/99"

    def test_checkout_failure(self, handler):
        def mock_git(*args, **kwargs):
            if args[0] == "checkout":
                return (1, "", "error: pathspec")
            return (0, "", "")

        with patch("app.git_prep.detect_remote_default_branch", return_value="main"):
            with patch("app.git_utils.run_git", side_effect=mock_git):
                result = handler._recover_one("koan/bad", "/project")
        assert result["error"] is not None
        assert "checkout" in result["error"]

    def test_push_failure(self, handler):
        def mock_git(*args, **kwargs):
            if args[0] == "push":
                return (1, "", "permission denied")
            return (0, "", "")

        with patch("app.git_prep.detect_remote_default_branch", return_value="main"):
            with patch("app.git_utils.run_git", side_effect=mock_git):
                result = handler._recover_one("koan/nopush", "/project")
        assert result["error"] is not None
        assert "push" in result["error"]
        assert result["pr_url"] is None

    def test_pr_creation_failure(self, handler):
        with patch("app.git_prep.detect_remote_default_branch", return_value="main"):
            with patch("app.git_utils.run_git", return_value=(0, "", "")):
                with patch("app.github.pr_create",
                           side_effect=RuntimeError("API error")):
                    result = handler._recover_one("koan/fail-pr", "/project")
        assert result["error"] is not None
        assert "PR creation" in result["error"]

    def test_force_push_used_after_rebase(self, handler):
        git_calls = []

        def mock_git(*args, **kwargs):
            git_calls.append(args)
            return (0, "", "")

        with patch("app.git_prep.detect_remote_default_branch", return_value="main"):
            with patch("app.git_utils.run_git", side_effect=mock_git):
                with patch("app.github.pr_create", return_value="url"):
                    handler._recover_one("koan/rebased", "/project")

        push_call = [c for c in git_calls if c[0] == "push"]
        assert len(push_call) == 1
        assert "--force-with-lease" in push_call[0]


class TestFormatResults:
    def test_all_success(self, handler):
        results = [
            {"branch": "koan/a", "rebased": True, "pr_url": "https://pr/1", "error": None},
            {"branch": "koan/b", "rebased": False, "pr_url": "https://pr/2", "error": None},
        ]
        output = handler._format_results("myproj", results)
        assert "myproj" in output
        assert "2 branch(es)" in output
        assert "koan/a" in output
        assert "rebased" in output
        assert "as-is" in output
        assert "https://pr/1" in output
        assert "2 draft PR(s)" in output

    def test_mixed_results(self, handler):
        results = [
            {"branch": "koan/ok", "rebased": True, "pr_url": "https://pr/1", "error": None},
            {"branch": "koan/bad", "rebased": False, "pr_url": None, "error": "push failed"},
        ]
        output = handler._format_results("myproj", results)
        assert "✅" in output
        assert "❌" in output
        assert "push failed" in output
        assert "1 draft PR(s)" in output


class TestOrphanHintInGitSync:
    """Verify that git_sync.py includes a /orphans hint when reporting orphans."""

    def test_sync_report_includes_orphan_hint(self):
        from app.git_sync import GitSync
        sync = GitSync("/tmp/inst", "myproj", "/tmp/proj")
        with patch.object(sync, "get_merged_branches", return_value=[]):
            with patch.object(sync, "get_github_merged_branches", return_value=[]):
                with patch.object(sync, "get_unmerged_branches", return_value=["koan/x"]):
                    with patch.object(sync, "get_recent_main_commits", return_value=[]):
                        with patch.object(sync, "_should_run_cleanup", return_value=True):
                            with patch.object(sync, "get_orphan_branches",
                                              return_value=["koan/x"]):
                                with patch.object(sync, "_record_cleanup"):
                                    with patch("app.git_sync.run_git", return_value=""):
                                        with patch("app.git_sync.get_branch_cleanup_config",
                                                   return_value={
                                                       "enabled": True,
                                                       "delete_remote_branches": False,
                                                       "notify_orphans": True,
                                                   }):
                                            with patch.object(sync, "cleanup_merged_branches",
                                                              return_value=[]):
                                                with patch.object(sync,
                                                                  "_split_branches_by_recency",
                                                                  return_value=(["koan/x"], [])):
                                                    report = sync.build_sync_report()
        assert "/orphans myproj" in report

    def test_outbox_notification_includes_orphan_hint(self, tmp_path):
        from app.git_sync import GitSync
        outbox = tmp_path / "outbox.md"
        outbox.write_text("")
        sync = GitSync(str(tmp_path), "myproj", str(tmp_path))
        sync._last_cleaned = []
        sync._last_orphans = ["koan/x"]
        with patch("app.utils.append_to_outbox") as mock_outbox:
            sync._notify_cleanup_results()
            call_args = mock_outbox.call_args
            msg = call_args[0][1]
        assert "/orphans myproj" in msg
