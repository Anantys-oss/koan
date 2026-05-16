"""Tests for app.cli_exec — secure prompt passing via temp files."""

import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from app.cli_exec import (
    STDIN_PLACEHOLDER,
    _uses_stdin_passing,
    prepare_prompt_file,
    run_cli,
    popen_cli,
    stream_with_timeout,
    _cleanup_prompt_file,
)


# ---------------------------------------------------------------------------
# _uses_stdin_passing
# ---------------------------------------------------------------------------

class TestUsesStdinPassing:
    """Tests for _uses_stdin_passing() provider detection."""

    @patch("app.provider.get_provider_name", return_value="claude")
    def test_claude_provider_uses_stdin(self, _mock):
        assert _uses_stdin_passing() is True

    @patch("app.provider.get_provider_name", return_value="copilot")
    def test_copilot_provider_skips_stdin(self, _mock):
        assert _uses_stdin_passing() is False

    @patch("app.provider.get_provider_name", return_value="local")
    def test_local_provider_uses_stdin(self, _mock):
        assert _uses_stdin_passing() is True

    @patch("app.provider.get_provider_name", side_effect=ImportError("no provider"))
    def test_import_error_defaults_to_true(self, _mock):
        assert _uses_stdin_passing() is True

    @patch("app.provider.get_provider_name", side_effect=RuntimeError("broken"))
    def test_runtime_error_defaults_to_true(self, _mock):
        assert _uses_stdin_passing() is True


# ---------------------------------------------------------------------------
# prepare_prompt_file
# ---------------------------------------------------------------------------

class TestPreparePromptFile:
    """Tests for prepare_prompt_file()."""

    def test_extracts_prompt_and_writes_temp_file(self):
        cmd = ["claude", "-p", "my secret prompt", "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert path is not None
            assert os.path.isfile(path)
            assert new_cmd == ["claude", "-p", STDIN_PLACEHOLDER, "--model", "opus"]
            with open(path) as f:
                assert f.read() == "my secret prompt"
            # Check permissions are restrictive
            mode = os.stat(path).st_mode & 0o777
            assert mode == 0o600
        finally:
            _cleanup_prompt_file(path)

    def test_no_p_flag_returns_unchanged(self):
        cmd = ["claude", "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None

    def test_p_at_end_with_no_value_returns_unchanged(self):
        cmd = ["claude", "-p"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None

    def test_already_placeholder_returns_none(self):
        cmd = ["claude", "-p", STDIN_PLACEHOLDER, "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None

    def test_preserves_original_cmd(self):
        cmd = ["claude", "-p", "secret"]
        original = cmd.copy()
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert cmd == original  # original not mutated
            assert new_cmd is not cmd
        finally:
            _cleanup_prompt_file(path)

    def test_handles_unicode_prompt(self):
        cmd = ["claude", "-p", "日本語のプロンプト 🎯"]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            with open(path, encoding="utf-8") as f:
                assert f.read() == "日本語のプロンプト 🎯"
        finally:
            _cleanup_prompt_file(path)

    def test_handles_empty_prompt(self):
        cmd = ["claude", "-p", ""]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert path is not None
            with open(path) as f:
                assert f.read() == ""
            assert new_cmd[2] == STDIN_PLACEHOLDER
        finally:
            _cleanup_prompt_file(path)

    def test_copilot_gh_mode(self):
        cmd = ["gh", "copilot", "-p", "my prompt", "--model", "opus"]
        new_cmd, path = prepare_prompt_file(cmd)
        try:
            assert new_cmd == ["gh", "copilot", "-p", STDIN_PLACEHOLDER, "--model", "opus"]
            with open(path) as f:
                assert f.read() == "my prompt"
        finally:
            _cleanup_prompt_file(path)

    @patch("app.provider.get_provider_name", return_value="copilot")
    def test_copilot_provider_skips_stdin_passing(self, _mock):
        """Copilot provider should skip @stdin mechanism entirely."""
        cmd = ["copilot", "-p", "my prompt", "--allow-all-tools"]
        new_cmd, path = prepare_prompt_file(cmd)
        assert new_cmd is cmd
        assert path is None


# ---------------------------------------------------------------------------
# _cleanup_prompt_file
# ---------------------------------------------------------------------------

class TestCleanupPromptFile:

    def test_removes_existing_file(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("data")
        _cleanup_prompt_file(str(f))
        assert not f.exists()

    def test_ignores_none(self):
        _cleanup_prompt_file(None)  # should not raise

    def test_ignores_missing_file(self):
        _cleanup_prompt_file("/nonexistent/path/file.md")  # should not raise


# ---------------------------------------------------------------------------
# run_cli
# ---------------------------------------------------------------------------

class TestRunCli:

    @patch("app.cli_exec.subprocess.run")
    def test_passes_prompt_via_stdin_fd(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["claude", "-p", "secret prompt", "--model", "opus"]

        result = run_cli(cmd, capture_output=True, text=True, timeout=60)

        call_args = mock_run.call_args
        actual_cmd = call_args[0][0]
        assert actual_cmd[2] == STDIN_PLACEHOLDER
        assert "secret prompt" not in actual_cmd
        # stdin should be a file object, not DEVNULL
        assert call_args[1]["stdin"] != subprocess.DEVNULL

    @patch("app.cli_exec.subprocess.run")
    def test_falls_back_to_devnull_without_p(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["git", "status"]

        run_cli(cmd, capture_output=True, text=True)

        call_args = mock_run.call_args
        assert call_args[1]["stdin"] == subprocess.DEVNULL

    @patch("app.cli_exec.subprocess.run")
    def test_cleans_up_temp_file_on_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["claude", "-p", "test prompt"]

        import glob
        before = set(glob.glob("/tmp/koan-prompt-*"))
        run_cli(cmd, capture_output=True, text=True)
        after = set(glob.glob("/tmp/koan-prompt-*"))
        assert after - before == set()

    @patch("app.cli_exec.subprocess.run", side_effect=Exception("boom"))
    def test_cleans_up_temp_file_on_exception(self, mock_run):
        cmd = ["claude", "-p", "test prompt"]

        import glob
        before = set(glob.glob("/tmp/koan-prompt-*"))
        with pytest.raises(Exception, match="boom"):
            run_cli(cmd, capture_output=True, text=True)
        after = set(glob.glob("/tmp/koan-prompt-*"))
        assert after - before == set()

    @patch("app.cli_exec.subprocess.run")
    def test_removes_existing_stdin_kwarg(self, mock_run):
        """If caller passes stdin=DEVNULL, it gets replaced with the file."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["claude", "-p", "prompt"]

        run_cli(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)

        call_args = mock_run.call_args
        assert call_args[1]["stdin"] != subprocess.DEVNULL

    @patch("app.provider.get_provider_name", return_value="copilot")
    @patch("app.cli_exec.subprocess.run")
    def test_copilot_keeps_prompt_in_args(self, mock_run, _mock_provider):
        """Copilot provider: prompt stays in -p, stdin is DEVNULL."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["copilot", "-p", "my prompt", "--model", "opus"]

        run_cli(cmd, capture_output=True, text=True)

        actual_cmd = mock_run.call_args[0][0]
        assert actual_cmd == ["copilot", "-p", "my prompt", "--model", "opus"]
        assert mock_run.call_args[1]["stdin"] == subprocess.DEVNULL

    @patch("app.cli_exec.subprocess.run")
    def test_default_timeout_applied_when_missing(self, mock_run):
        """run_cli applies DEFAULT_TIMEOUT when caller omits timeout=."""
        from app.cli_exec import DEFAULT_TIMEOUT
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["git", "status"]

        run_cli(cmd, capture_output=True, text=True)

        call_args = mock_run.call_args
        assert call_args[1]["timeout"] == DEFAULT_TIMEOUT

    @patch("app.cli_exec.subprocess.run")
    def test_explicit_timeout_not_overridden(self, mock_run):
        """run_cli respects an explicit timeout from the caller."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        cmd = ["git", "status"]

        run_cli(cmd, capture_output=True, text=True, timeout=42)

        call_args = mock_run.call_args
        assert call_args[1]["timeout"] == 42


# ---------------------------------------------------------------------------
# popen_cli
# ---------------------------------------------------------------------------

class TestPopenCli:

    @patch("app.cli_exec.subprocess.Popen")
    def test_returns_proc_and_cleanup(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        cmd = ["claude", "-p", "secret", "--model", "opus"]

        proc, cleanup = popen_cli(cmd, stdout=subprocess.PIPE)

        assert proc is mock_proc
        actual_cmd = mock_popen.call_args[0][0]
        assert actual_cmd[2] == STDIN_PLACEHOLDER

        # Cleanup should remove the temp file
        import glob
        before = set(glob.glob("/tmp/koan-prompt-*"))
        cleanup()
        after = set(glob.glob("/tmp/koan-prompt-*"))
        assert len(after) <= len(before)

    @patch("app.cli_exec.subprocess.Popen")
    def test_no_p_flag_returns_noop_cleanup(self, mock_popen):
        mock_popen.return_value = MagicMock()
        cmd = ["git", "status"]

        proc, cleanup = popen_cli(cmd)

        call_args = mock_popen.call_args
        assert call_args[1].get("stdin", subprocess.DEVNULL) == subprocess.DEVNULL
        cleanup()  # should not raise

    @patch("app.cli_exec.subprocess.Popen")
    def test_stdin_is_file_object(self, mock_popen):
        mock_popen.return_value = MagicMock()
        cmd = ["claude", "-p", "prompt"]

        proc, cleanup = popen_cli(cmd)

        call_args = mock_popen.call_args
        stdin_arg = call_args[1]["stdin"]
        assert hasattr(stdin_arg, "read")  # it's a file object
        cleanup()

    @patch("app.provider.get_provider_name", return_value="copilot")
    @patch("app.cli_exec.subprocess.Popen")
    def test_copilot_keeps_prompt_in_args(self, mock_popen, _mock_provider):
        """Copilot provider: popen keeps prompt in -p, stdin is DEVNULL."""
        mock_popen.return_value = MagicMock()
        cmd = ["copilot", "-p", "my prompt"]

        proc, cleanup = popen_cli(cmd)

        actual_cmd = mock_popen.call_args[0][0]
        assert actual_cmd == ["copilot", "-p", "my prompt"]
        assert mock_popen.call_args[1]["stdin"] == subprocess.DEVNULL
        cleanup()


# ---------------------------------------------------------------------------
# stream_with_timeout
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self, lines=None, read_text=""):
        self._lines = list(lines or [])
        self._read_text = read_text
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._read_text

    def close(self):
        self.closed = True


def _fake_proc(stdout_lines, stderr_text="", returncode=0, pid=99999):
    proc = MagicMock()
    proc.stdout = _FakeStream(lines=stdout_lines)
    proc.stderr = _FakeStream(read_text=stderr_text)
    proc.returncode = returncode
    proc.pid = pid
    proc.wait.return_value = returncode
    return proc


class TestStreamWithTimeout:
    """Tests for stream_with_timeout — shared streaming + watchdog helper."""

    def test_collects_stdout_lines(self):
        proc = _fake_proc(["a\n", "b\n", "c\n"], returncode=0)
        result = stream_with_timeout(proc, timeout=10)
        assert result.stdout == "a\nb\nc"
        assert result.stderr == ""
        assert result.timed_out is False

    def test_forwards_each_line_to_callback(self):
        proc = _fake_proc(["one\n", "two\n", "three\n"], returncode=0)
        seen = []
        stream_with_timeout(proc, timeout=10, on_line=seen.append)
        assert seen == ["one", "two", "three"]

    def test_drains_stderr(self):
        proc = _fake_proc(["ok\n"], stderr_text="oops", returncode=1)
        result = stream_with_timeout(proc, timeout=10)
        assert result.stderr == "oops"
        assert result.timed_out is False

    def test_closes_streams(self):
        proc = _fake_proc(["ok\n"], returncode=0)
        stream_with_timeout(proc, timeout=10)
        assert proc.stdout.closed is True
        assert proc.stderr.closed is True

    def test_timeout_kills_process_group(self):
        """When the watchdog fires it must SIGKILL the whole process group."""
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

        with patch("app.cli_exec.os.killpg",
                   side_effect=lambda *a, **kw: killed.set()) as killpg, \
                patch("app.cli_exec.os.getpgid", return_value=12345):
            result = stream_with_timeout(proc, timeout=0.5)

        assert result.timed_out is True
        killpg.assert_called_once()

    def test_completed_flag_blocks_watchdog_race(self):
        """If the watchdog Timer fires after stream EOF but before
        ``watchdog.cancel()``, the kill must be skipped and ``timed_out``
        must stay False — otherwise a clean completion gets reported as
        a timeout."""
        from app.cli_exec import stream_with_timeout as swt

        proc = _fake_proc(["done\n"], returncode=0)

        with patch("app.cli_exec.threading.Timer") as TimerMock:
            timer_instance = MagicMock()
            captured = {}

            def factory(timeout, fn):
                captured["fn"] = fn
                return timer_instance

            TimerMock.side_effect = factory

            with patch("app.cli_exec.os.killpg") as killpg:
                # Simulate the race: invoke the watchdog callback after
                # stream consumption but before cancel() returns.
                def fire_after_stream():
                    captured["fn"]()
                    return None
                timer_instance.cancel.side_effect = fire_after_stream

                result = swt(proc, timeout=10)

            killpg.assert_not_called()
            assert result.timed_out is False
