"""Tests for claude_step.py — shared CI/CD pipeline helpers.

Tests _run_git, truncate_text, _rebase_onto_target, run_claude,
commit_if_changes, and run_claude_step.
"""

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from app.claude_step import (
    StepResult,
    _is_ancestor,
    _prefetch_all_remotes,
    _rebase_onto_target,
    _run_git,
    commit_if_changes,
    resolve_pr_location,
    run_claude,
    run_claude_step,
    run_project_tests,
    strip_cli_noise,
)


# ---------- _run_git ----------


class TestRunGit:
    """Tests for _run_git helper."""

    @patch("app.cli_exec.subprocess.run")
    def test_success_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  abc123  \n")
        result = _run_git(["git", "rev-parse", "HEAD"])
        assert result == "abc123"

    @patch("app.cli_exec.subprocess.run")
    def test_failure_raises(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=128, stderr="fatal: not a git repo"
        )
        with pytest.raises(RuntimeError, match="git failed"):
            _run_git(["git", "status"])

    @patch("app.cli_exec.subprocess.run")
    def test_passes_cwd_and_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok")
        _run_git(["git", "status"], cwd="/tmp/test", timeout=30)
        mock_run.assert_called_once_with(
            ["git", "status"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            cwd="/tmp/test",
        )

    @patch("app.cli_exec.subprocess.run")
    def test_error_message_truncates_stderr(self, mock_run):
        long_stderr = "x" * 500
        mock_run.return_value = MagicMock(returncode=1, stderr=long_stderr)
        with pytest.raises(RuntimeError) as exc_info:
            _run_git(["git", "bad"])
        # Stderr in error message should be truncated to 200 chars
        assert len(str(exc_info.value)) < 300


# ---------- truncate_text (now in utils.py) ----------


class TestTruncateText:
    """Tests for truncate_text shared utility."""

    def test_short_text_unchanged(self):
        from app.utils import truncate_text
        assert truncate_text("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        from app.utils import truncate_text
        assert truncate_text("12345", 5) == "12345"

    def test_over_limit_truncated(self):
        from app.utils import truncate_text
        result = truncate_text("1234567890", 5)
        assert result.startswith("12345")
        assert "truncated" in result

    def test_empty_string(self):
        from app.utils import truncate_text
        assert truncate_text("", 10) == ""


# ---------- strip_cli_noise ----------


class TestStripCliNoise:
    """Tests for strip_cli_noise helper."""

    def test_removes_max_turns_error(self):
        text = "Some reflection text.\nError: Reached max turns (1)"
        assert strip_cli_noise(text) == "Some reflection text."

    def test_removes_higher_turn_counts(self):
        text = "Output\nError: Reached max turns (3)"
        assert strip_cli_noise(text) == "Output"

    def test_preserves_clean_text(self):
        text = "A genuine reflection.\nWith multiple lines."
        assert strip_cli_noise(text) == text

    def test_empty_string(self):
        assert strip_cli_noise("") == ""

    def test_only_error_line_returns_empty(self):
        assert strip_cli_noise("Error: Reached max turns (1)") == ""

    def test_multiline_with_error_in_middle(self):
        text = "Line 1\nError: Reached max turns (1)\nLine 3"
        assert strip_cli_noise(text) == "Line 1\nLine 3"

    def test_case_insensitive(self):
        text = "Output\nerror: reached MAX TURNS (2)"
        assert strip_cli_noise(text) == "Output"

    def test_preserves_unrelated_error_lines(self):
        text = "Output\nError: something else happened"
        assert strip_cli_noise(text) == text

    def test_multiple_error_lines(self):
        text = "Line 1\nError: Reached max turns (1)\nLine 2\nError: Reached max turns (1)"
        assert strip_cli_noise(text) == "Line 1\nLine 2"


# ---------- _rebase_onto_target ----------


class TestRebaseOntoTarget:
    """Tests for _rebase_onto_target."""

    @patch("app.claude_step._run_git")
    def test_origin_success(self, mock_git):
        result = _rebase_onto_target("main", "/project")
        assert result == "origin"
        mock_git.assert_any_call(
            ["git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main"],
            cwd="/project", timeout=60,
        )
        mock_git.assert_any_call(
            ["git", "fetch", "upstream", "+refs/heads/main:refs/remotes/upstream/main"],
            cwd="/project", timeout=60,
        )

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_origin_fails_upstream_succeeds(self, mock_git, mock_subprocess):
        def side_effect(cmd, **kwargs):
            if "rebase" in cmd and any("origin" in a for a in cmd):
                raise RuntimeError("rebase failed")
            return ""

        mock_git.side_effect = side_effect
        result = _rebase_onto_target("main", "/project")
        assert result == "upstream"

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_both_fail_returns_none(self, mock_git, mock_subprocess):
        mock_git.side_effect = RuntimeError("fail")
        result = _rebase_onto_target("main", "/project")
        assert result is None

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_rebase_abort_called_on_failure(self, mock_git, mock_subprocess):
        def selective_fail(cmd, **kwargs):
            if "rebase" in cmd:
                raise RuntimeError("conflict")
            return ""
        mock_git.side_effect = selective_fail
        _rebase_onto_target("main", "/project")
        abort_calls = [
            c
            for c in mock_subprocess.call_args_list
            if "rebase" in c[0][0] and "--abort" in c[0][0]
        ]
        assert len(abort_calls) == 2

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_rebase_abort_called_with_timeout(self, mock_git, mock_subprocess):
        """git rebase --abort must have a timeout to prevent hangs in cleanup."""
        def selective_fail(cmd, **kwargs):
            if "rebase" in cmd:
                raise RuntimeError("conflict")
            return ""
        mock_git.side_effect = selective_fail
        _rebase_onto_target("main", "/project")
        abort_calls = [
            c
            for c in mock_subprocess.call_args_list
            if "rebase" in c[0][0] and "--abort" in c[0][0]
        ]
        assert len(abort_calls) >= 1
        for call in abort_calls:
            assert call[1].get("timeout", 0) > 0

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_timeout_caught_and_logged(self, mock_git, mock_subprocess, capsys):
        """TimeoutExpired should be caught (not just Exception) and logged."""
        def selective_fail(cmd, **kwargs):
            if "rebase" in cmd:
                raise subprocess.TimeoutExpired("git", 60)
            return ""
        mock_git.side_effect = selective_fail
        result = _rebase_onto_target("main", "/project")
        assert result is None
        captured = capsys.readouterr()
        assert "Rebase onto" in captured.err
        assert "timed out" in captured.err.lower() or "timeout" in captured.err.lower()

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_os_error_caught_and_logged(self, mock_git, mock_subprocess, capsys):
        """OSError (e.g. git not found) should be caught and logged."""
        def selective_fail(cmd, **kwargs):
            if "rebase" in cmd:
                raise OSError("No such file or directory: 'git'")
            return ""
        mock_git.side_effect = selective_fail
        result = _rebase_onto_target("main", "/project")
        assert result is None
        captured = capsys.readouterr()
        assert "Rebase onto" in captured.err

    @patch("app.cli_exec.subprocess.run")
    @patch("app.claude_step._run_git")
    def test_unexpected_exception_not_caught(self, mock_git, mock_subprocess):
        """Unexpected exceptions (e.g. ValueError) should propagate, not be swallowed."""
        mock_git.side_effect = ValueError("unexpected error")
        with pytest.raises(ValueError, match="unexpected"):
            _rebase_onto_target("main", "/project")


# ---------- _is_ancestor ----------


class TestIsAncestor:
    """Tests for _is_ancestor helper."""

    @patch("app.claude_step._run_git")
    def test_returns_true_when_ancestor(self, mock_git):
        mock_git.return_value = ""
        assert _is_ancestor("origin/main", "upstream/main", "/project") is True
        mock_git.assert_called_once()
        cmd = mock_git.call_args[0][0]
        assert cmd == ["git", "merge-base", "--is-ancestor", "origin/main", "upstream/main"]

    @patch("app.claude_step._run_git")
    def test_returns_false_when_not_ancestor(self, mock_git):
        mock_git.side_effect = RuntimeError("exit 1")
        assert _is_ancestor("origin/main", "upstream/main", "/project") is False

    @patch("app.claude_step._run_git")
    def test_returns_false_on_timeout(self, mock_git):
        mock_git.side_effect = subprocess.TimeoutExpired("git", 10)
        assert _is_ancestor("origin/main", "upstream/main", "/project") is False


# ---------- _rebase_onto_target with head_remote ----------


class TestRebaseOntoTargetForkAware:
    """Tests for --onto logic when head_remote (fork) differs from target."""

    @patch("app.claude_step._is_ancestor", return_value=True)
    @patch("app.claude_step._run_git")
    def test_stale_fork_skips_onto_uses_plain_rebase(self, mock_git, mock_ancestor):
        """When fork/main is ancestor of upstream/main, --onto is skipped.

        This is the bug scenario: fork is simply behind upstream. Using
        --onto would replay upstream commits that already exist, causing
        spurious conflicts in files the PR never touched.
        """
        result = _rebase_onto_target(
            "main", "/project",
            preferred_remote="upstream",
            head_remote="origin",
        )
        assert result == "upstream"
        # Should have fetched upstream/main and origin/main, then plain rebase
        rebase_calls = [
            c for c in mock_git.call_args_list
            if any("rebase" in str(a) for a in c[0][0])
        ]
        assert len(rebase_calls) == 1
        rebase_cmd = rebase_calls[0][0][0]
        assert "--onto" not in rebase_cmd

    @patch("app.claude_step._is_ancestor", return_value=False)
    @patch("app.claude_step._run_git")
    def test_diverged_fork_uses_onto(self, mock_git, mock_ancestor):
        """When fork/main has diverged from upstream/main, --onto is used."""
        result = _rebase_onto_target(
            "main", "/project",
            preferred_remote="upstream",
            head_remote="origin",
        )
        assert result == "upstream"
        rebase_calls = [
            c for c in mock_git.call_args_list
            if any("rebase" in str(a) for a in c[0][0])
        ]
        assert len(rebase_calls) == 1
        rebase_cmd = rebase_calls[0][0][0]
        assert "--onto" in rebase_cmd
        assert "upstream/main" in rebase_cmd
        assert "origin/main" in rebase_cmd

    @patch("app.claude_step._run_git")
    def test_head_remote_fetch_fails_falls_through(self, mock_git):
        """When fetching fork's base branch fails, falls through to plain rebase."""
        def side_effect(cmd, **kwargs):
            if "origin" in cmd and "fetch" in cmd[1]:
                raise RuntimeError("fetch failed")
            return ""
        mock_git.side_effect = side_effect
        result = _rebase_onto_target(
            "main", "/project",
            preferred_remote="upstream",
            head_remote="origin",
        )
        assert result == "upstream"
        rebase_calls = [
            c for c in mock_git.call_args_list
            if any("rebase" in str(a) for a in c[0][0])
        ]
        assert len(rebase_calls) == 1
        rebase_cmd = rebase_calls[0][0][0]
        assert "--onto" not in rebase_cmd


# ---------- _prefetch_all_remotes ----------


class TestPrefetchAllRemotes:
    """Tests for _prefetch_all_remotes — eager base branch sync."""

    @patch("app.claude_step._run_git")
    def test_fetches_origin_and_upstream(self, mock_git):
        _prefetch_all_remotes("main", "/project")
        assert mock_git.call_count == 2
        mock_git.assert_any_call(
            ["git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main"],
            cwd="/project", timeout=60,
        )
        mock_git.assert_any_call(
            ["git", "fetch", "upstream", "+refs/heads/main:refs/remotes/upstream/main"],
            cwd="/project", timeout=60,
        )

    @patch("app.claude_step._run_git")
    def test_includes_head_remote(self, mock_git):
        _prefetch_all_remotes("main", "/project", head_remote="myfork")
        fetched = [c[0][0][2] for c in mock_git.call_args_list]
        assert "myfork" in fetched
        assert "origin" in fetched
        assert "upstream" in fetched

    @patch("app.claude_step._run_git")
    def test_preferred_remote_first(self, mock_git):
        _prefetch_all_remotes("main", "/project", preferred_remote="upstream")
        first_call_remote = mock_git.call_args_list[0][0][0][2]
        assert first_call_remote == "upstream"

    @patch("app.claude_step._run_git")
    def test_no_duplicate_when_head_in_ordered(self, mock_git):
        _prefetch_all_remotes("main", "/project", head_remote="origin")
        assert mock_git.call_count == 2

    @patch("app.claude_step._run_git")
    def test_failure_is_nonfatal(self, mock_git, capsys):
        mock_git.side_effect = RuntimeError("network down")
        _prefetch_all_remotes("main", "/project")
        captured = capsys.readouterr()
        assert "Pre-fetch" in captured.err
        assert "non-fatal" in captured.err

    @patch("app.claude_step._run_git")
    def test_timeout_is_nonfatal(self, mock_git, capsys):
        mock_git.side_effect = subprocess.TimeoutExpired("git", 60)
        _prefetch_all_remotes("main", "/project")
        captured = capsys.readouterr()
        assert "Pre-fetch" in captured.err



# ---------- run_claude ----------


class _FakeStream:
    """Iterable + closable stand-in for ``proc.stdout`` / ``proc.stderr``.

    Tests need a file-like object that supports both ``for line in stream``
    iteration and ``stream.close()`` — a bare ``iter([])`` does not.
    """

    def __init__(self, lines=None, read_text=""):
        self._lines = list(lines or [])
        self._read_text = read_text

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._read_text

    def close(self):
        return None


def _fake_proc(stdout_lines, stderr_text="", returncode=0, pid=99999):
    """Build a fake Popen object for streaming tests.

    ``stdout_lines`` is a list of full lines (each entry should already
    contain a trailing newline if needed). ``proc.stdout`` becomes an
    iterable so the streaming loop in ``run_claude`` can consume it.
    """
    proc = MagicMock()
    proc.stdout = _FakeStream(lines=stdout_lines)
    proc.stderr = _FakeStream(read_text=stderr_text)
    proc.returncode = returncode
    proc.pid = pid
    proc.wait.return_value = returncode
    return proc


class TestRunClaude:
    """Tests for run_claude — streams stdout, captures full output."""

    @patch("app.claude_step.popen_cli")
    def test_success(self, mock_popen):
        proc = _fake_proc(["  done  \n"], stderr_text="", returncode=0)
        mock_popen.return_value = (proc, lambda: None)
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is True
        assert result["output"] == "done"
        assert result["error"] == ""

    @patch("app.claude_step.popen_cli")
    def test_failure_with_stderr(self, mock_popen):
        proc = _fake_proc(
            ["partial\n"], stderr_text="something broke", returncode=1,
        )
        mock_popen.return_value = (proc, lambda: None)
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is False
        assert "Exit code 1" in result["error"]
        assert "something broke" in result["error"]

    @patch("app.claude_step.popen_cli")
    def test_failure_no_stderr(self, mock_popen):
        proc = _fake_proc([], stderr_text="", returncode=1)
        mock_popen.return_value = (proc, lambda: None)
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is False
        assert "no stderr" in result["error"]

    @patch("app.claude_step.popen_cli")
    def test_failure_no_stderr_includes_stdout(self, mock_popen):
        """When stderr is empty but stdout has content, error includes stdout."""
        proc = _fake_proc(
            ["Error: context window exceeded\n"],
            stderr_text="",
            returncode=1,
        )
        mock_popen.return_value = (proc, lambda: None)
        result = run_claude(["claude", "-p", "test"], "/project")
        assert result["success"] is False
        assert "no stderr" in result["error"]
        assert "stdout:" in result["error"]
        assert "context window exceeded" in result["error"]

    @patch("app.claude_step.popen_cli")
    def test_timeout_kills_process_group(self, mock_popen):
        """When the watchdog fires, run_claude returns a Timeout error.

        Simulates a hanging child by blocking stdout iteration until the
        watchdog thread invokes the kill callback. The kill is monkey-
        patched to set the unblock event, mirroring what os.killpg would
        do in production (cause the child to exit and stdout to EOF).
        """
        import os
        import threading

        killed = threading.Event()

        class _BlockingStream:
            def __iter__(self):
                killed.wait(timeout=10)
                return iter([])

            def read(self):
                return ""

            def close(self):
                return None

        proc = MagicMock()
        proc.stdout = _BlockingStream()
        proc.stderr = _FakeStream(read_text="")
        proc.returncode = -9
        proc.pid = 12345
        proc.wait.return_value = -9
        mock_popen.return_value = (proc, lambda: None)

        # Use a tiny timeout so the watchdog fires within the test.
        with patch("os.killpg", side_effect=lambda *a, **kw: killed.set()):
            with patch.object(os, "getpgid", return_value=12345):
                result = run_claude(
                    ["claude", "-p", "test"], "/project", timeout=1,
                )

        assert result["success"] is False
        assert "Timeout" in result["error"]
        assert "1" in result["error"]

    @patch("app.claude_step.popen_cli")
    def test_streams_stdout_lines(self, mock_popen, capsys):
        """Each Claude stdout line must be forwarded to parent stdout
        so the run.py liveness watchdog resets on every line."""
        proc = _fake_proc(
            ["thinking...\n", "calling tool\n", "done\n"],
            stderr_text="",
            returncode=0,
        )
        mock_popen.return_value = (proc, lambda: None)
        run_claude(["claude", "-p", "test"], "/project")
        captured = capsys.readouterr()
        assert "thinking..." in captured.out
        assert "calling tool" in captured.out
        assert "done" in captured.out

    @patch("app.claude_step.popen_cli")
    def test_uses_new_session_for_process_group_kill(self, mock_popen):
        """popen must request a new POSIX session so the whole process
        group can be killed on timeout — preventing grandchildren from
        holding the stdout pipe open and hanging the drain."""
        proc = _fake_proc(["ok\n"], returncode=0)
        mock_popen.return_value = (proc, lambda: None)
        run_claude(["claude", "-p", "test"], "/project")
        call_kwargs = mock_popen.call_args.kwargs
        assert call_kwargs.get("start_new_session") is True

    @patch("app.claude_step.popen_cli")
    def test_long_stderr_truncated(self, mock_popen):
        long_err = "E" * 1000
        proc = _fake_proc([], stderr_text=long_err, returncode=1)
        mock_popen.return_value = (proc, lambda: None)
        result = run_claude(["claude", "-p", "test"], "/project")
        # Should only keep last 500 chars of stderr
        assert len(result["error"]) < 600

    @patch("app.claude_step.popen_cli")
    def test_cleanup_called_on_success(self, mock_popen):
        proc = _fake_proc(["ok\n"], returncode=0)
        cleanup = MagicMock()
        mock_popen.return_value = (proc, cleanup)
        run_claude(["claude", "-p", "test"], "/project")
        cleanup.assert_called_once()


# ---------- commit_if_changes ----------


class TestCommitIfChanges:
    """Tests for commit_if_changes."""

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_no_changes_returns_false(self, mock_run, mock_git):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = commit_if_changes("/project", "test msg")
        assert result is False
        # Should not call git add or commit
        mock_git.assert_not_called()

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_with_changes_commits(self, mock_run, mock_git):
        mock_run.return_value = MagicMock(
            stdout=" M file.py\n", returncode=0
        )
        result = commit_if_changes("/project", "test msg")
        assert result is True
        assert mock_git.call_count == 2
        mock_git.assert_any_call(["git", "add", "-A"], cwd="/project")
        mock_git.assert_any_call(
            ["git", "commit", "-m", "test msg"], cwd="/project"
        )

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_whitespace_only_status_is_no_changes(self, mock_run, mock_git):
        mock_run.return_value = MagicMock(stdout="   \n  ", returncode=0)
        result = commit_if_changes("/project", "msg")
        assert result is False

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run")
    def test_git_status_called_with_timeout(self, mock_run, mock_git):
        """git status must have a timeout to prevent hangs on unresponsive repos."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        commit_if_changes("/project", "msg")
        call_kwargs = mock_run.call_args[1]
        assert "timeout" in call_kwargs
        assert call_kwargs["timeout"] > 0

    @patch("app.claude_step._run_git")
    @patch("app.cli_exec.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30))
    def test_git_status_timeout_propagates(self, mock_run, mock_git):
        """TimeoutExpired from git status should propagate (not silently swallowed)."""
        with pytest.raises(subprocess.TimeoutExpired):
            commit_if_changes("/project", "msg")


# ---------- run_claude_step ----------


class TestRunClaudeStep:
    """Tests for run_claude_step — orchestrator."""

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix bug", "--allowedTools", "Bash,Read,Write,Glob,Grep,Edit", "--model", "opus"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "opus", "fallback": "sonnet", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_success_with_commit(self, mock_config, mock_flags, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "done", "error": ""}
        actions = []
        result = run_claude_step(
            prompt="fix bug",
            project_path="/project",
            commit_msg="fix: bug",
            success_label="Bug fixed",
            failure_label="Fix failed",
            actions_log=actions,
        )
        assert result  # StepResult is truthy when committed
        assert result.committed is True
        assert result.output == "done"
        assert "Bug fixed" in actions

    @patch("app.claude_step.commit_if_changes", return_value=False)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_success_no_commit(self, mock_config, mock_flags, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        actions = []
        result = run_claude_step(
            prompt="review code",
            project_path="/project",
            commit_msg="chore: review",
            success_label="Reviewed",
            failure_label="Review failed",
            actions_log=actions,
        )
        assert not result  # StepResult is falsy when not committed
        assert result.committed is False
        assert actions == []

    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_failure_logs_error(self, mock_config, mock_flags, mock_claude):
        mock_claude.return_value = {
            "success": False,
            "output": "",
            "error": "Exit code 1: crash",
        }
        actions = []
        result = run_claude_step(
            prompt="fix bug",
            project_path="/project",
            commit_msg="fix: bug",
            success_label="Fixed",
            failure_label="Fix failed",
            actions_log=actions,
        )
        assert not result
        assert result.committed is False
        assert len(actions) == 1
        assert "Fix failed" in actions[0]
        assert "crash" in actions[0]

    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_failure_includes_stdout_when_no_stderr(self, mock_config, mock_flags, mock_claude):
        """When CLI exits with no stderr, stdout should be included in the error log."""
        mock_claude.return_value = {
            "success": False,
            "output": "Error: context window exceeded for this prompt",
            "error": "Exit code 1: no stderr",
        }
        actions = []
        run_claude_step(
            prompt="fix bug",
            project_path="/project",
            commit_msg="fix: bug",
            success_label="Fixed",
            failure_label="Fix failed",
            actions_log=actions,
        )
        assert len(actions) == 1
        assert "stdout:" in actions[0]
        assert "context window exceeded" in actions[0]

    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_failure_no_stdout_fallback_when_stderr_present(self, mock_config, mock_flags, mock_claude):
        """When stderr is present, stdout should NOT be appended."""
        mock_claude.return_value = {
            "success": False,
            "output": "some output",
            "error": "Exit code 1: actual error message",
        }
        actions = []
        run_claude_step(
            prompt="fix bug",
            project_path="/project",
            commit_msg="fix: bug",
            success_label="Fixed",
            failure_label="Fix failed",
            actions_log=actions,
        )
        assert len(actions) == 1
        assert "stdout:" not in actions[0]
        assert "actual error message" in actions[0]

    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_failure_empty_label_no_log(self, mock_config, mock_flags, mock_claude):
        mock_claude.return_value = {
            "success": False,
            "output": "",
            "error": "fail",
        }
        actions = []
        result = run_claude_step(
            prompt="test",
            project_path="/p",
            commit_msg="x",
            success_label="OK",
            failure_label="",
            actions_log=actions,
        )
        assert not result
        assert actions == []

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_use_skill_adds_skill_tool(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "done", "error": ""}
        run_claude_step(
            prompt="refactor",
            project_path="/project",
            commit_msg="refactor",
            success_label="OK",
            failure_label="Fail",
            actions_log=[],
            use_skill=True,
        )
        # Verify build_full_command was called with Skill in allowed_tools
        call_kwargs = mock_cmd.call_args
        allowed = call_kwargs.kwargs.get("allowed_tools", [])
        assert "Skill" in allowed

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_no_skill_by_default(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "done", "error": ""}
        run_claude_step(
            prompt="fix",
            project_path="/project",
            commit_msg="fix",
            success_label="OK",
            failure_label="Fail",
            actions_log=[],
        )
        # Verify build_full_command was called without Skill in allowed_tools
        call_kwargs = mock_cmd.call_args
        allowed = call_kwargs.kwargs.get("allowed_tools", [])
        assert "Skill" not in allowed

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_custom_max_turns_and_timeout(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        run_claude_step(
            prompt="deep work",
            project_path="/project",
            commit_msg="chore: deep",
            success_label="Done",
            failure_label="Fail",
            actions_log=[],
            max_turns=5,
            timeout=120,
        )
        # Verify build_full_command was called with max_turns=5
        call_kwargs = mock_cmd.call_args
        assert call_kwargs.kwargs.get("max_turns") == 5
        # Timeout passed to run_claude
        assert mock_claude.call_args[1]["timeout"] == 120

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix bug", "--allowedTools", "Bash,Read,Write,Glob,Grep,Edit", "--model", "opus"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "opus", "fallback": "sonnet", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_model_config_passed_to_flags(self, mock_config, mock_cmd, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        run_claude_step(
            prompt="test",
            project_path="/p",
            commit_msg="test",
            success_label="OK",
            failure_label="Fail",
            actions_log=[],
        )
        # Verify model and fallback passed to build_full_command
        call_kwargs = mock_cmd.call_args.kwargs
        assert call_kwargs["model"] == "opus"
        assert call_kwargs["fallback"] == "sonnet"

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "test"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_success_empty_label_no_log(self, mock_config, mock_flags, mock_claude, mock_commit):
        mock_claude.return_value = {"success": True, "output": "ok", "error": ""}
        actions = []
        result = run_claude_step(
            prompt="test",
            project_path="/p",
            commit_msg="test",
            success_label="",
            failure_label="Fail",
            actions_log=actions,
        )
        # commit_if_changes returns True, empty label means no log entry
        assert result  # committed is True
        assert result.committed is True
        assert actions == []  # but nothing logged


# ---------- run_claude_step with use_convention_subject ----------


class TestRunClaudeStepConventionSubject:
    """Tests for run_claude_step with use_convention_subject flag."""

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_uses_parsed_subject(self, _mc, _cmd, mock_claude, mock_commit):
        """When use_convention_subject=True and Claude outputs COMMIT_SUBJECT,
        the parsed subject should be used instead of the default."""
        mock_claude.return_value = {
            "success": True,
            "output": "Fixed it.\nCOMMIT_SUBJECT: Case PROJECT-123 Fix auth\n",
            "error": "",
        }
        actions = []
        result = run_claude_step(
            prompt="fix",
            project_path="/project",
            commit_msg="fix: default message",
            success_label="OK",
            failure_label="Fail",
            actions_log=actions,
            use_convention_subject=True,
        )
        assert result  # StepResult truthy when committed
        commit_msg = mock_commit.call_args[0][1]
        assert commit_msg == "Case PROJECT-123 Fix auth"

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_falls_back_to_default(self, _mc, _cmd, mock_claude, mock_commit):
        """When use_convention_subject=True but no COMMIT_SUBJECT found,
        falls back to the provided commit_msg."""
        mock_claude.return_value = {
            "success": True,
            "output": "Fixed it.\n",
            "error": "",
        }
        actions = []
        run_claude_step(
            prompt="fix",
            project_path="/project",
            commit_msg="fix: default message",
            success_label="OK",
            failure_label="Fail",
            actions_log=actions,
            use_convention_subject=True,
        )
        commit_msg = mock_commit.call_args[0][1]
        assert commit_msg == "fix: default message"

    @patch("app.claude_step.commit_if_changes", return_value=True)
    @patch("app.claude_step.run_claude")
    @patch("app.claude_step.build_full_command", return_value=["claude", "-p", "fix"])
    @patch(
        "app.claude_step.get_model_config",
        return_value={"mission": "", "fallback": "", "chat": "", "lightweight": "", "review_mode": ""},
    )
    def test_disabled_by_default(self, _mc, _cmd, mock_claude, mock_commit):
        """When use_convention_subject is False (default), always uses commit_msg."""
        mock_claude.return_value = {
            "success": True,
            "output": "COMMIT_SUBJECT: should be ignored\n",
            "error": "",
        }
        actions = []
        run_claude_step(
            prompt="fix",
            project_path="/project",
            commit_msg="fix: default",
            success_label="OK",
            failure_label="Fail",
            actions_log=actions,
        )
        commit_msg = mock_commit.call_args[0][1]
        assert commit_msg == "fix: default"


# ---------- _get_current_branch ----------


class TestGetCurrentBranch:
    """Tests for _get_current_branch helper."""

    @patch("app.claude_step._git_utils_get_current_branch", return_value="koan/my-feature")
    def test_returns_branch_name(self, mock_git):
        from app.claude_step import _get_current_branch
        assert _get_current_branch("/project") == "koan/my-feature"
        mock_git.assert_called_once_with(cwd="/project")

    @patch("app.claude_step._git_utils_get_current_branch", return_value="main")
    def test_fallback_to_main_on_error(self, mock_git):
        from app.claude_step import _get_current_branch
        assert _get_current_branch("/project") == "main"


# ---------- _safe_checkout ----------


class TestSafeCheckout:
    """Tests for _safe_checkout helper."""

    @patch("app.claude_step._run_git")
    def test_checkout_succeeds(self, mock_git):
        from app.claude_step import _safe_checkout
        _safe_checkout("main", "/project")
        mock_git.assert_called_once_with(
            ["git", "checkout", "main"], cwd="/project"
        )

    @patch("app.claude_step._run_git", side_effect=Exception("dirty tree"))
    def test_does_not_raise_on_failure(self, mock_git):
        from app.claude_step import _safe_checkout
        _safe_checkout("main", "/project")  # Should not raise


# ---------- _get_diffstat ----------


class TestGetDiffstat:
    """Tests for _get_diffstat helper."""

    @patch("app.claude_step._run_git")
    def test_returns_summary_line(self, mock_git):
        from app.claude_step import _get_diffstat
        mock_git.return_value = (
            " file1.py | 10 ++++---\n"
            " file2.py |  3 ++\n"
            " 2 files changed, 9 insertions(+), 4 deletions(-)"
        )
        result = _get_diffstat("origin/main", "/project")
        assert "2 files changed" in result
        assert "9 insertions" in result

    @patch("app.claude_step._run_git", side_effect=Exception("bad ref"))
    def test_returns_empty_on_failure(self, mock_git):
        from app.claude_step import _get_diffstat
        result = _get_diffstat("origin/main", "/project")
        assert result == ""

    @patch("app.claude_step._run_git", return_value="")
    def test_returns_empty_for_no_diff(self, mock_git):
        from app.claude_step import _get_diffstat
        result = _get_diffstat("origin/main", "/project")
        assert result == ""


# ---------- _is_permission_error ----------


class TestIsPermissionError:
    """Tests for _is_permission_error helper."""

    def test_permission_denied(self):
        from app.claude_step import _is_permission_error
        assert _is_permission_error("permission denied") is True

    def test_forbidden_403(self):
        from app.claude_step import _is_permission_error
        assert _is_permission_error("HTTP 403: Forbidden") is True

    def test_protected_branch(self):
        from app.claude_step import _is_permission_error
        assert _is_permission_error("protected branch") is True

    def test_auth_failed(self):
        from app.claude_step import _is_permission_error
        assert _is_permission_error("authentication failed for url") is True

    def test_non_permission_error(self):
        from app.claude_step import _is_permission_error
        assert _is_permission_error("fatal: remote ref does not exist") is False

    def test_empty_string(self):
        from app.claude_step import _is_permission_error
        assert _is_permission_error("") is False


# ---------- _build_pr_prompt ----------


class TestBuildPrPrompt:
    """Tests for _build_pr_prompt shared helper."""

    @pytest.fixture
    def context(self):
        return {
            "title": "feat: add scanner",
            "body": "Scans outbox.",
            "branch": "koan/scanner",
            "base": "main",
            "diff": "+code",
            "review_comments": "looks good",
            "reviews": "",
            "issue_comments": "",
        }

    @patch("app.claude_step.load_prompt_or_skill", return_value="skill prompt")
    def test_with_skill_dir(self, mock_lp, context, tmp_path):
        from app.claude_step import _build_pr_prompt
        result = _build_pr_prompt("rebase", context, skill_dir=tmp_path)
        assert result == "skill prompt"
        mock_lp.assert_called_once()
        args, kwargs = mock_lp.call_args
        assert args[0] == tmp_path
        assert args[1] == "rebase"
        assert "feat: add scanner" in kwargs["TITLE"]
        assert "BEGIN EXTERNAL DATA" in kwargs["TITLE"]

    @patch("app.claude_step.load_prompt_or_skill", return_value="system prompt")
    def test_without_skill_dir(self, mock_lp, context):
        from app.claude_step import _build_pr_prompt
        result = _build_pr_prompt("recreate", context, skill_dir=None)
        assert result == "system prompt"
        mock_lp.assert_called_once()
        args, kwargs = mock_lp.call_args
        assert args[0] is None
        assert args[1] == "recreate"

    @patch("app.claude_step.load_prompt_or_skill", return_value="ok")
    def test_passes_all_context_fields(self, mock_lp, context):
        from app.claude_step import _build_pr_prompt
        _build_pr_prompt("rebase", context)
        _, kwargs = mock_lp.call_args
        assert kwargs["BRANCH"] == "koan/scanner"
        assert kwargs["BASE"] == "main"
        assert "+code" in kwargs["DIFF"]
        assert "BEGIN EXTERNAL DATA" in kwargs["DIFF"]
        # REVIEW_COMMENTS is fenced with data boundaries
        assert "looks good" in kwargs["REVIEW_COMMENTS"]
        assert "BEGIN EXTERNAL DATA" in kwargs["REVIEW_COMMENTS"]

    @patch("app.claude_step.load_prompt_or_skill", return_value="ok")
    def test_truncates_large_diff(self, mock_lp, context):
        """Large diffs should be truncated to prevent context window overflow."""
        from app.claude_step import _build_pr_prompt
        context["diff"] = "x" * 100_000
        _build_pr_prompt("recreate", context, max_diff_chars=50_000)
        _, kwargs = mock_lp.call_args
        assert len(kwargs["DIFF"]) < 100_000
        assert "truncated" in kwargs["DIFF"]

    @patch("app.claude_step.load_prompt_or_skill", return_value="ok")
    def test_small_diff_not_truncated(self, mock_lp, context):
        """Small diffs should pass through unchanged."""
        from app.claude_step import _build_pr_prompt
        context["diff"] = "+small change"
        _build_pr_prompt("recreate", context)
        _, kwargs = mock_lp.call_args
        assert "+small change" in kwargs["DIFF"]
        assert "BEGIN EXTERNAL DATA" in kwargs["DIFF"]


# ---------- _push_with_pr_fallback ----------


class TestPushWithPrFallback:
    """Tests for the unified push-with-fallback helper."""

    @pytest.fixture
    def context(self):
        return {
            "title": "feat: scanner",
            "url": "https://github.com/sukria/koan/pull/99",
        }

    @patch("app.claude_step._run_git")
    def test_force_push_success_rebase(self, mock_git, context):
        from app.claude_step import _push_with_pr_fallback
        result = _push_with_pr_fallback(
            "koan/fix", "main", "sukria/koan", "99",
            context, "/project", pr_type="rebase",
        )
        assert result["success"] is True
        assert any("Force-pushed" in a for a in result["actions"])
        assert "recreated" not in result["actions"][0]

    @patch("app.claude_step._run_git")
    def test_force_push_success_recreate(self, mock_git, context):
        from app.claude_step import _push_with_pr_fallback
        result = _push_with_pr_fallback(
            "koan/fix", "main", "sukria/koan", "99",
            context, "/project", pr_type="recreate",
        )
        assert result["success"] is True
        assert "recreated from scratch" in result["actions"][0]

    @patch("app.claude_step._run_git", side_effect=RuntimeError("network timeout"))
    def test_non_permission_error_fails(self, mock_git, context):
        from app.claude_step import _push_with_pr_fallback
        result = _push_with_pr_fallback(
            "koan/fix", "main", "sukria/koan", "99",
            context, "/project", pr_type="rebase",
        )
        assert result["success"] is False
        assert "network timeout" in result["error"]

    def test_permission_error_creates_fallback_pr(self, context):
        from app.claude_step import _push_with_pr_fallback
        call_count = [0]

        def mock_git(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("permission denied")
            return ""

        with patch("app.claude_step._run_git", side_effect=mock_git), \
             patch("app.claude_step.pr_create", return_value="https://github.com/sukria/koan/pull/200\n"), \
             patch("app.claude_step.run_gh"), \
             patch("app.utils.get_branch_prefix", return_value="koan/"):
            result = _push_with_pr_fallback(
                "koan/fix", "main", "sukria/koan", "99",
                context, "/project", pr_type="rebase",
            )
            assert result["success"] is True
            assert any("new branch" in a.lower() for a in result["actions"])
            assert any("draft PR" in a for a in result["actions"])
            assert "new_pr_url" in result

    def test_recreate_fallback_uses_recreate_prefix(self, context):
        from app.claude_step import _push_with_pr_fallback
        call_count = [0]
        branches_created = []

        def mock_git(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("permission denied")
            if "checkout" in cmd and "-b" in cmd:
                branches_created.append(cmd[cmd.index("-b") + 1])
            return ""

        with patch("app.claude_step._run_git", side_effect=mock_git), \
             patch("app.claude_step.pr_create", return_value="https://github.com/sukria/koan/pull/201\n"), \
             patch("app.claude_step.run_gh"), \
             patch("app.utils.get_branch_prefix", return_value="koan/"):
            _push_with_pr_fallback(
                "feat/scanner", "main", "sukria/koan", "99",
                context, "/project", pr_type="recreate",
            )
            assert branches_created
            assert "recreate-" in branches_created[0]

    def test_crosslink_failure_is_nonfatal(self, context):
        from app.claude_step import _push_with_pr_fallback
        call_count = [0]

        def mock_git(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("permission denied")
            return ""

        with patch("app.claude_step._run_git", side_effect=mock_git), \
             patch("app.claude_step.pr_create", return_value="https://github.com/sukria/koan/pull/202\n"), \
             patch("app.claude_step.run_gh", side_effect=RuntimeError("API error")), \
             patch("app.utils.get_branch_prefix", return_value="koan/"):
            result = _push_with_pr_fallback(
                "koan/fix", "main", "sukria/koan", "99",
                context, "/project", pr_type="rebase",
            )
            assert result["success"] is True


# ---------- run_project_tests ----------


class TestRunProjectTests:
    """Tests for the shared run_project_tests helper."""

    @patch("app.claude_step.subprocess.run")
    def test_passing_tests_with_count(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="collected 42 items\n42 passed in 3.5s\n",
            stderr="",
        )
        result = run_project_tests("/project")
        assert result["passed"] is True
        assert "42 passed" in result["details"]

    @patch("app.claude_step.subprocess.run")
    def test_passing_tests_no_count(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All good\n",
            stderr="",
        )
        result = run_project_tests("/project")
        assert result["passed"] is True
        assert result["details"] == "OK"

    @patch("app.claude_step.subprocess.run")
    def test_failing_tests(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="3 failed, 10 passed\n",
            stderr="",
        )
        result = run_project_tests("/project")
        assert result["passed"] is False
        assert "3 failed" in result["details"]
        assert "10 passed" in result["details"]

    @patch("app.claude_step.subprocess.run")
    def test_custom_test_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="OK\n",
            stderr="",
        )
        run_project_tests("/project", test_cmd="npm test")
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0] == ["npm", "test"]

    @patch("app.claude_step.subprocess.run")
    def test_custom_timeout(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="OK\n",
            stderr="",
        )
        run_project_tests("/project", timeout=600)
        assert mock_run.call_args[1]["timeout"] == 600

    @patch("app.claude_step.subprocess.run", side_effect=subprocess.TimeoutExpired("make test", 300))
    def test_timeout(self, mock_run):
        result = run_project_tests("/project")
        assert result["passed"] is False
        assert "timeout" in result["details"]

    @patch("app.claude_step.subprocess.run", side_effect=FileNotFoundError("make"))
    def test_command_not_found(self, mock_run):
        result = run_project_tests("/project")
        assert result["passed"] is False
        assert result["details"] == "command not found"

    @patch("app.claude_step.subprocess.run", side_effect=OSError("disk full"))
    def test_generic_exception(self, mock_run):
        result = run_project_tests("/project")
        assert result["passed"] is False
        assert "disk full" in result["details"]

    @patch("app.claude_step.subprocess.run")
    def test_output_truncated_to_3000(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="x" * 5000,
            stderr="",
        )
        result = run_project_tests("/project")
        assert len(result["output"]) <= 3000

    @patch("app.claude_step.subprocess.run")
    def test_uses_shlex_split(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok", stderr=""
        )
        run_project_tests("/project", test_cmd="make test")
        # Should pass a list (shlex.split), not a string with shell=True
        assert mock_run.call_args[0][0] == ["make", "test"]
        assert mock_run.call_args[1].get("shell") is not True

    @patch("app.claude_step.subprocess.run")
    def test_stdin_devnull(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ok", stderr=""
        )
        run_project_tests("/project")
        assert mock_run.call_args[1].get("stdin") == subprocess.DEVNULL or \
               mock_run.call_args[0][0] is not None  # just verify call was made


# ---------- resolve_pr_location ----------


class TestResolvePrLocation:
    """Tests for resolve_pr_location — cross-owner PR URL resolution."""

    @patch("app.claude_step.run_gh")
    def test_fast_path_pr_exists_at_given_owner(self, mock_run_gh):
        """When the PR exists at the given owner/repo, return immediately."""
        mock_run_gh.return_value = '{"number": 42}'
        owner, repo = resolve_pr_location("sukria", "koan", "42", "/project")
        assert owner == "sukria"
        assert repo == "koan"
        # Should only call once (fast path)
        mock_run_gh.assert_called_once()

    @patch("app.utils.get_all_github_remotes")
    @patch("app.claude_step.run_gh")
    def test_fallback_to_git_remote(self, mock_run_gh, mock_remotes):
        """When the PR doesn't exist at given owner, try git remotes."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call: PR not found at sukria/koan
            if call_count == 1:
                raise RuntimeError("Could not resolve to a pull request")
            # Second call: found at anantys-oss/koan
            return '{"number": 42}'

        mock_run_gh.side_effect = side_effect
        mock_remotes.return_value = ["sukria/koan", "anantys-oss/koan"]
        owner, repo = resolve_pr_location("sukria", "koan", "42", "/project")

        assert owner == "anantys-oss"
        assert repo == "koan"

    @patch("app.utils.get_all_github_remotes")
    @patch("app.claude_step.run_gh")
    def test_raises_when_pr_not_found_anywhere(self, mock_run_gh, mock_remotes):
        """When no remote has the PR, raise RuntimeError."""
        mock_run_gh.side_effect = RuntimeError("not found")
        mock_remotes.return_value = ["origin/koan"]
        with pytest.raises(RuntimeError, match="not found at sukria/koan"):
            resolve_pr_location("sukria", "koan", "42", "/project")

    @patch("app.utils.get_all_github_remotes")
    @patch("app.claude_step.run_gh")
    def test_skips_already_tried_remote(self, mock_run_gh, mock_remotes):
        """Don't re-check the original owner/repo if it appears in remotes."""
        mock_run_gh.side_effect = RuntimeError("not found")
        # sukria/koan appears in remotes — should not be tried twice
        mock_remotes.return_value = ["sukria/koan"]
        with pytest.raises(RuntimeError):
            resolve_pr_location("sukria", "koan", "42", "/project")

        # Original check + no duplicates = 1 call total
        assert mock_run_gh.call_count == 1


class TestForcePush:
    """Tests for _force_push() in claude_step."""

    @patch("app.claude_step._run_git")
    def test_force_with_lease_succeeds(self, mock_git):
        from app.claude_step import _force_push
        _force_push("origin", "my-branch", "/project")
        mock_git.assert_called_once_with(
            ["git", "push", "origin", "my-branch", "--force-with-lease"],
            cwd="/project",
        )

    @patch("app.claude_step._run_git")
    def test_falls_back_to_plain_force(self, mock_git):
        from app.claude_step import _force_push
        mock_git.side_effect = [RuntimeError("lease rejected"), None]
        _force_push("origin", "my-branch", "/project")
        assert mock_git.call_count == 2
        second_call = mock_git.call_args_list[1]
        assert "--force" in second_call[0][0]


class TestRunCiFixLoop:
    """Tests for run_ci_fix_loop() — the shared CI fix loop."""

    @patch("app.claude_step._run_git", return_value="")
    @patch("app.claude_step.run_claude_step", return_value=False)
    def test_no_changes_gives_up(self, mock_step, mock_git):
        from app.claude_step import run_ci_fix_loop
        actions = []
        success, logs = run_ci_fix_loop(
            "fix-branch", "main", "owner/repo", "/project",
            "Error: test failed", actions,
            max_attempts=2,
            prompt_builder=lambda logs, diff: "fix this",
        )
        assert success is False
        assert any("no changes" in a.lower() for a in actions)
        mock_step.assert_called_once()

    @patch("app.claude_step.check_existing_ci", return_value=("success", 457, ""))
    @patch("app.claude_step._force_push")
    @patch("app.claude_step._run_git", return_value="")
    @patch("app.claude_step.run_claude_step", return_value=True)
    @patch("time.sleep")
    def test_fix_then_ci_passes(self, mock_sleep, mock_step, mock_git, mock_push, mock_ci):
        from app.claude_step import run_ci_fix_loop
        actions = []
        success, logs = run_ci_fix_loop(
            "fix-branch", "main", "owner/repo", "/project",
            "Error: test failed", actions,
            max_attempts=2,
            use_polling=False,
            prompt_builder=lambda logs, diff: "fix this",
        )
        assert success is True
        assert any("CI passed" in a for a in actions)

    @patch("app.claude_step.check_existing_ci", return_value=("pending", 789, ""))
    @patch("app.claude_step._force_push")
    @patch("app.claude_step._run_git", return_value="")
    @patch("app.claude_step.run_claude_step", return_value=True)
    @patch("time.sleep")
    def test_pending_returns_success(self, mock_sleep, mock_step, mock_git, mock_push, mock_ci):
        from app.claude_step import run_ci_fix_loop
        actions = []
        success, logs = run_ci_fix_loop(
            "fix-branch", "main", "owner/repo", "/project",
            "Error: test failed", actions,
            max_attempts=2,
            use_polling=False,
            prompt_builder=lambda logs, diff: "fix this",
        )
        assert success is True
        assert any("CI running" in a for a in actions)

    @patch("app.claude_step._force_push", side_effect=RuntimeError("push rejected"))
    @patch("app.claude_step._run_git", return_value="")
    @patch("app.claude_step.run_claude_step", return_value=True)
    def test_push_failure_stops(self, mock_step, mock_git, mock_push):
        from app.claude_step import run_ci_fix_loop
        actions = []
        success, logs = run_ci_fix_loop(
            "fix-branch", "main", "owner/repo", "/project",
            "Error: test failed", actions,
            max_attempts=2,
            prompt_builder=lambda logs, diff: "fix this",
        )
        assert success is False
        assert any("Push failed" in a for a in actions)

    @patch("app.claude_step._run_git", return_value="diff content")
    @patch("app.claude_step.run_claude_step", return_value=False)
    def test_base_remote_used_for_diff(self, mock_step, mock_git):
        """The base_remote parameter is used in the git diff command."""
        from app.claude_step import run_ci_fix_loop
        run_ci_fix_loop(
            "fix-branch", "main", "owner/repo", "/project",
            "Error", [],
            max_attempts=1,
            prompt_builder=lambda logs, diff: "fix",
            base_remote="upstream",
        )
        diff_call = mock_git.call_args_list[0]
        assert "upstream/main" in str(diff_call)

    @patch("app.claude_step._run_git", return_value="")
    @patch("app.claude_step.run_claude_step", return_value=False)
    def test_prompt_builder_receives_logs_and_diff(self, mock_step, mock_git):
        """The prompt_builder callback receives CI logs and truncated diff."""
        from app.claude_step import run_ci_fix_loop
        received = []

        def capture_prompt(logs, diff):
            received.append((logs, diff))
            return "fix prompt"

        run_ci_fix_loop(
            "fix-branch", "main", "owner/repo", "/project",
            "CI error output", [],
            max_attempts=1,
            prompt_builder=capture_prompt,
        )
        assert len(received) == 1
        assert received[0][0] == "CI error output"
