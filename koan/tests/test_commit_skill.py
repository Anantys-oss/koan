"""Tests for the /commit core skill — SKILL.md, handler, prompt, runner."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.prompts import load_skill_prompt
from app.skills import SkillContext, build_registry


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).parent.parent / "skills" / "core" / "commit"
HANDLER_PATH = SKILL_DIR / "handler.py"
RUNNER_PATH = SKILL_DIR / "commit_runner.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_module(HANDLER_PATH, "commit_handler")


@pytest.fixture
def runner():
    return _load_module(RUNNER_PATH, "commit_runner")


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_path = instance_dir / "missions.md"
    missions_path.write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
    )
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="commit",
        args="",
        send_message=MagicMock(),
    )


def _init_git_repo(path: Path, *, with_change: bool = True) -> Path:
    """Create a throwaway git repo; optionally leave an unstaged change."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, check=True, capture_output=True,
    )
    # Start on a feature branch so preflight allows commits.
    subprocess.run(
        ["git", "checkout", "-b", "koan/feature"],
        cwd=path, check=True, capture_output=True,
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: init"],
        cwd=path, check=True, capture_output=True,
    )
    if with_change:
        (path / "hello.py").write_text("print('hi')\n")
    return path


# ---------------------------------------------------------------------------
# SKILL.md / registry
# ---------------------------------------------------------------------------


class TestSkillMetadata:
    def test_skill_md_exists(self):
        assert (SKILL_DIR / "SKILL.md").is_file()

    def test_registry_discovers_commit(self):
        registry = build_registry()
        skill = registry.find_by_command("commit")
        assert skill is not None
        assert skill.name == "commit"
        assert skill.group == "code"
        assert skill.audience in ("hybrid", "bridge", "agent")

    def test_alias_cm_resolves(self):
        registry = build_registry()
        skill = registry.find_by_command("cm")
        assert skill is not None
        assert skill.name == "commit"

    def test_prompt_file_loadable(self):
        prompt = load_skill_prompt(
            SKILL_DIR,
            "commit",
            PROJECT_NAME="demo",
            MESSAGE_HINT="fix login",
        )
        assert "conventional" in prompt.lower() or "feat" in prompt
        assert "demo" in prompt
        assert "fix login" in prompt or "fix login" in prompt
        # Safety invariants must appear in the prompt body.
        assert ".env" in prompt
        assert "main" in prompt.lower() or "master" in prompt.lower()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class TestHandleHelp:
    def test_help_flag(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/commit" in result

    def test_help_short_flag(self, handler, ctx):
        ctx.args = "-h"
        result = handler.handle(ctx)
        assert "Usage:" in result


class TestHandleQueueMission:
    def test_no_projects_returns_error(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[]):
            result = handler.handle(ctx)
        assert "\u274c" in result
        assert "No projects" in result

    def test_unknown_token_treated_as_hint_on_default(self, handler, ctx, tmp_path):
        """First token that is not a known project becomes the message hint."""
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("myproject", str(repo))],
        ), patch(
            "app.utils.resolve_project_name_and_path",
            return_value=("nosuch", None),
        ), patch("app.utils.insert_pending_mission") as mock_insert:
            ctx.args = "nosuch"
            result = handler.handle(ctx)
        assert "Commit queued for myproject" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "nosuch" in mission_entry

    def test_clean_tree_returns_error(self, handler, ctx, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=False)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("myproject", str(repo))],
        ), patch(
            "app.utils.resolve_project_name_and_path",
            return_value=("myproject", str(repo)),
        ), patch("app.utils.insert_pending_mission") as mock_insert:
            ctx.args = "myproject"
            result = handler.handle(ctx)
        assert "\u274c" in result
        assert "No changes" in result
        mock_insert.assert_not_called()

    def test_queues_mission_for_project_with_changes(self, handler, ctx, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("myproject", str(repo))],
        ), patch(
            "app.utils.resolve_project_name_and_path",
            return_value=("myproject", str(repo)),
        ), patch("app.utils.insert_pending_mission") as mock_insert:
            ctx.args = "myproject"
            result = handler.handle(ctx)

        assert "Commit queued" in result
        assert "myproject" in result
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:myproject]" in mission_entry
        assert "/commit" in mission_entry

    def test_message_hint_included_in_mission(self, handler, ctx, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("myproject", str(repo))],
        ), patch(
            "app.utils.resolve_project_name_and_path",
            return_value=("myproject", str(repo)),
        ), patch("app.utils.insert_pending_mission") as mock_insert:
            ctx.args = "myproject fix the login bug"
            result = handler.handle(ctx)

        assert "hint: fix the login bug" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "fix the login bug" in mission_entry

    def test_hint_without_project_uses_default(self, handler, ctx, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        with patch(
            "app.utils.get_known_projects",
            return_value=[("myproject", str(repo))],
        ), patch(
            # First token is not a project name.
            "app.utils.resolve_project_name_and_path",
            return_value=("fix", None),
        ), patch("app.utils.insert_pending_mission") as mock_insert:
            ctx.args = "fix the login bug"
            result = handler.handle(ctx)

        assert "Commit queued for myproject" in result
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:myproject]" in mission_entry
        assert "fix the login bug" in mission_entry


# ---------------------------------------------------------------------------
# Runner preflight + prompt build
# ---------------------------------------------------------------------------


class TestRunnerPreflight:
    def test_preflight_rejects_clean_tree(self, runner, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=False)
        reason = runner._preflight_git_state(str(repo))
        assert reason is not None
        assert "clean" in reason.lower() or "nothing" in reason.lower()

    def test_preflight_rejects_main_branch(self, runner, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        # Rename feature branch to main.
        subprocess.run(
            ["git", "branch", "-m", "main"],
            cwd=repo, check=True, capture_output=True,
        )
        reason = runner._preflight_git_state(str(repo))
        assert reason is not None
        assert "main" in reason.lower() or "protected" in reason.lower()

    def test_preflight_ok_with_changes(self, runner, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        reason = runner._preflight_git_state(str(repo))
        assert reason is None

    def test_build_commit_prompt_includes_hint(self, runner):
        prompt = runner.build_commit_prompt(
            project_name="demo",
            message_hint="fix timeout",
            skill_dir=SKILL_DIR,
        )
        assert "demo" in prompt
        assert "fix timeout" in prompt

    def test_run_commit_aborts_on_clean_tree(self, runner, tmp_path):
        repo = _init_git_repo(tmp_path / "proj", with_change=False)
        notify = MagicMock()
        success, summary = runner.run_commit(
            project_path=str(repo),
            project_name="demo",
            instance_dir=str(tmp_path / "instance"),
            notify_fn=notify,
            skill_dir=SKILL_DIR,
        )
        assert success is False
        assert "clean" in summary.lower() or "nothing" in summary.lower()
        notify.assert_called()

    def test_preflight_rejects_conflict_probe_failure(self, runner, tmp_path):
        """Conflict-check git failure is a hard abort, not 'no conflicts'."""
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        real_git = runner._git

        def _fake_git(project_path, args, timeout=15):
            if args[:1] == ["diff"] and "--diff-filter=U" in args:
                return 1, "", "diff failed"
            return real_git(project_path, args, timeout=timeout)

        with patch.object(runner, "_git", side_effect=_fake_git):
            reason = runner._preflight_git_state(str(repo))
        assert reason is not None
        assert "conflict" in reason.lower()

    def test_run_commit_fails_when_head_unchanged(self, runner, tmp_path):
        """Model text claiming COMMITTED is not enough — HEAD must advance."""
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        notify = MagicMock()
        fake_report = (
            "COMMITTED\n"
            "branch: koan/feature\n"
            "sha: deadbeef\n"
            "message: feat: pretend\n"
        )
        with patch.object(
            runner, "_run_claude_commit", return_value=fake_report,
        ), patch("app.messaging_level.notify_outcome") as mock_outcome:
            success, summary = runner.run_commit(
                project_path=str(repo),
                project_name="demo",
                instance_dir=str(tmp_path / "instance"),
                notify_fn=notify,
                skill_dir=SKILL_DIR,
            )
        assert success is False
        assert "HEAD unchanged" in summary or "no new commit" in summary.lower()
        mock_outcome.assert_not_called()
        # Failure path uses the error notifier, not a success outcome.
        assert any(
            "\u274c" in str(c) or "failed" in str(c).lower()
            for c in (call.args[0] for call in notify.call_args_list if call.args)
        )

    def test_run_commit_succeeds_only_when_head_advances(self, runner, tmp_path):
        """Success requires a real new commit (HEAD SHA change)."""
        repo = _init_git_repo(tmp_path / "proj", with_change=True)
        notify = MagicMock()

        def _do_commit(_prompt, project_path):
            subprocess.run(
                ["git", "add", "hello.py"],
                cwd=project_path, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "feat: add hello"],
                cwd=project_path, check=True, capture_output=True,
            )
            return (
                "COMMITTED\n"
                "branch: koan/feature\n"
                "message: feat: add hello\n"
            )

        with patch.object(runner, "_run_claude_commit", side_effect=_do_commit), patch(
            "app.messaging_level.notify_outcome",
        ) as mock_outcome:
            success, summary = runner.run_commit(
                project_path=str(repo),
                project_name="demo",
                instance_dir=str(tmp_path / "instance"),
                notify_fn=notify,
                skill_dir=SKILL_DIR,
            )
        assert success is True
        assert "feat: add hello" in summary or "Commit for demo" in summary
        mock_outcome.assert_called_once()

    def test_read_context_file_logs_on_oserror(self, runner, tmp_path, capsys):
        missing = tmp_path / "no-such-hint.txt"
        result = runner._read_context_file(str(missing))
        assert result == ""
        err = capsys.readouterr().err
        assert "failed to read context file" in err
        assert str(missing) in err


class TestSkillDispatch:
    def test_commit_registered_in_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS

        assert "commit" in _SKILL_RUNNERS
        assert "cm" in _SKILL_RUNNERS
        assert _SKILL_RUNNERS["cm"] == _SKILL_RUNNERS["commit"]

    def test_build_skill_command_includes_project_args(self, tmp_path):
        from app.skill_dispatch import build_skill_command

        cmd = build_skill_command(
            command="commit",
            args="",
            project_path="/path/to/proj",
            project_name="proj",
            instance_dir=str(tmp_path),
            koan_root=str(tmp_path),
        )
        assert cmd is not None
        assert "skills.core.commit.commit_runner" in " ".join(cmd)
        assert "--project-path" in cmd
        assert "--project-name" in cmd
        assert "--instance-dir" in cmd

    def test_build_skill_command_passes_hint_via_context_file(self, tmp_path):
        from app.skill_dispatch import build_skill_command, cleanup_skill_temp_files

        cmd = build_skill_command(
            command="commit",
            args="fix the login",
            project_path="/path/to/proj",
            project_name="proj",
            instance_dir=str(tmp_path),
            koan_root=str(tmp_path),
        )
        assert cmd is not None
        assert "--context-file" in cmd
        idx = cmd.index("--context-file")
        ctx_path = Path(cmd[idx + 1])
        assert ctx_path.is_file()
        assert "fix the login" in ctx_path.read_text()
        cleanup_skill_temp_files(cmd)
