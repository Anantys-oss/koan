"""Tests for provider check_quota_available implementations."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.provider.base import CLIProvider
from app.provider.claude import ClaudeProvider
from app.provider.copilot import CopilotProvider
from app.provider.ollama_launch import OllamaLaunchProvider


class TestBaseProviderQuota:
    """Tests for CLIProvider.check_quota_available() base implementation."""

    def test_base_always_returns_available(self):
        """Base implementation returns (True, '') — no quota concept."""

        class StubProvider(CLIProvider):
            name = "stub"
            def binary(self): return "stub"
            def build_prompt_args(self, p): return []
            def build_tool_args(self, a=None, d=None): return []
            def build_model_args(self, m="", f=""): return []
            def build_output_args(self, f=""): return []
            def build_max_turns_args(self, m=0): return []
            def build_mcp_args(self, c=None): return []

        ok, detail = StubProvider().check_quota_available("/tmp")
        assert ok is True
        assert detail == ""


class TestClaudeProviderQuota:
    """Tests for ClaudeProvider.check_quota_available().

    The method is a no-op that always returns (True, '') because
    'claude usage' is not a real CLI subcommand. Quota exhaustion
    is detected post-run by quota_handler.py instead.
    """

    def setup_method(self):
        self.provider = ClaudeProvider()

    def test_quota_available(self):
        """Always returns (True, '') — no subprocess call."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    def test_quota_exhausted(self):
        """Cannot detect exhaustion — always optimistic."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    def test_passes_cwd(self):
        """Method accepts project_path but does not use it."""
        ok, detail = self.provider.check_quota_available("/my/custom/path")
        assert ok is True

    def test_captures_output(self):
        """No subprocess call — nothing to capture."""
        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is True
        assert detail == ""

    def test_custom_timeout(self):
        """Timeout accepted but has no effect."""
        ok, detail = self.provider.check_quota_available("/tmp", timeout=30)
        assert ok is True
        assert detail == ""

    def test_combines_stderr_and_stdout(self):
        """No subprocess — nothing to combine."""
        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is True
        assert detail == ""

    def test_detects_session_limit_stdout(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="You've hit your session limit · resets 3am (UTC)",
            stderr_text="",
            exit_code=1,
        ) is True

    def test_detects_structured_rate_limit_stdout(self):
        payload = (
            '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
            '"resetsAt":1779937200,"rateLimitType":"five_hour"}}'
        )
        assert self.provider.detect_quota_exhaustion(
            stdout_text=payload,
            stderr_text="",
            exit_code=1,
        ) is True

    def test_ignores_generic_rate_limit_stdout(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="Plan: add API rate limit handling to this endpoint.",
            stderr_text="",
            exit_code=1,
        ) is False


class TestLocalProviderQuota:
    """Ollama-launch provider has no quota concept."""

    def test_ollama_launch_always_available(self):
        """OllamaLaunchProvider inherits base (True, '')."""
        provider = OllamaLaunchProvider()
        ok, detail = provider.check_quota_available("/tmp")
        assert ok is True
        assert detail == ""


class TestCopilotProviderQuota:
    """Tests for CopilotProvider.check_quota_available() — minimal probe."""

    def setup_method(self):
        # Create a CopilotProvider with mocked binary availability
        with patch("app.provider.copilot.shutil.which",
                    side_effect=lambda x: "/usr/local/bin/copilot" if x == "copilot" else None):
            self.provider = CopilotProvider()

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_quota_available(self, mock_detect, mock_run):
        """Returns (True, '') when probe succeeds without quota signals."""
        mock_run.return_value = MagicMock(
            stdout="ok",
            stderr="",
            returncode=0,
        )

        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""
        mock_run.assert_called_once()
        # Verify probe command
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "copilot"
        assert "-p" in cmd
        assert "ok" in cmd

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=True)
    def test_quota_exhausted(self, mock_detect, mock_run):
        """Returns (False, combined_output) when probe hits rate limit."""
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="HTTP 429: too many requests\nRetry-After: 300",
            returncode=1,
        )

        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is False
        assert "429" in detail
        assert "too many requests" in detail

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 15))
    def test_timeout_returns_optimistic(self, mock_run):
        """On timeout, proceed optimistically (True, '')."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    @patch("subprocess.run", side_effect=FileNotFoundError("copilot not found"))
    def test_binary_not_found_returns_optimistic(self, mock_run):
        """When CLI binary is missing, proceed optimistically."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    @patch("subprocess.run", side_effect=OSError("disk error"))
    def test_os_error_returns_optimistic(self, mock_run):
        """On generic OS error, proceed optimistically."""
        ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_passes_cwd(self, mock_detect, mock_run):
        """Verify project_path is forwarded as cwd."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)

        self.provider.check_quota_available("/my/custom/path")
        kwargs = mock_run.call_args[1]
        assert kwargs["cwd"] == "/my/custom/path"

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_custom_timeout(self, mock_detect, mock_run):
        """Custom timeout parameter is respected."""
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)

        self.provider.check_quota_available("/tmp", timeout=30)
        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] == 30

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=True)
    def test_combines_stderr_and_stdout(self, mock_detect, mock_run):
        """Combined output includes both stderr and stdout."""
        mock_run.return_value = MagicMock(
            stdout="stdout data",
            stderr="HTTP 429: too many requests",
            returncode=1,
        )

        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is False
        assert "HTTP 429" in detail
        assert "stdout data" in detail

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_handles_none_stdout(self, mock_detect, mock_run):
        """When stdout or stderr is None, doesn't crash."""
        mock_run.return_value = MagicMock(
            stdout=None,
            stderr=None,
            returncode=0,
        )

        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is True

    @patch("subprocess.run")
    @patch("app.quota_handler.detect_quota_exhaustion", return_value=False)
    def test_nonzero_exit_no_pattern_proceeds_optimistically(self, mock_detect, mock_run):
        """Non-zero exit without quota pattern → proceed optimistically."""
        mock_run.return_value = MagicMock(
            stdout="Some error occurred",
            stderr="connection refused",
            returncode=1,
        )

        ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is True
        assert detail == ""

    def test_gh_mode_probe_command(self):
        """In gh mode, probe command uses 'gh copilot -p ok'."""
        with patch("app.provider.copilot.shutil.which",
                    side_effect=lambda x: "/usr/bin/gh" if x == "gh" else None):
            provider = CopilotProvider()

        with patch("subprocess.run") as mock_run, \
             patch("app.quota_handler.detect_quota_exhaustion", return_value=False):
            mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
            provider.check_quota_available("/tmp")

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "gh"
            assert "copilot" in cmd
            assert "-p" in cmd
            assert "ok" in cmd


class TestCopilotDetectQuotaExhaustion:
    """Stdout scanning must not pause Koan on incidental 'rate limit' text."""

    def setup_method(self):
        self.provider = CopilotProvider()

    def test_stderr_pattern_always_triggers(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="",
            stderr_text="HTTP 429: too many requests",
            exit_code=1,
        ) is True

    def test_stdout_rate_limit_in_assistant_text_ignored_on_success(self):
        """A successful research mission discussing rate limits must not pause."""
        stdout = (
            "Here's a plan for handling API rate limits in the new endpoint:\n"
            "1. Detect rate limit headers from the upstream.\n"
            "2. Back off exponentially.\n"
        )
        assert self.provider.detect_quota_exhaustion(
            stdout_text=stdout,
            stderr_text="",
            exit_code=0,
        ) is False

    def test_stdout_rate_limit_in_assistant_text_ignored_on_failure(self):
        """Non-zero exit + assistant prose mentioning 'rate limit' must not trigger.

        Without the content-marker gate, the generic 'rate limit' phrase in
        normal output would mis-classify a max-turns or hook abort as quota.
        """
        stdout = (
            "Here's a plan for handling API rate limits in the new endpoint.\n"
            "It explains how to back off when servers return throttling info.\n"
        )
        assert self.provider.detect_quota_exhaustion(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is False

    def test_stdout_error_line_with_rate_limit_triggers_on_failure(self):
        """When a stdout line looks like a Copilot/GitHub error, scan it."""
        stdout = "Error: GitHub Copilot rate limit exceeded. Try again later."
        assert self.provider.detect_quota_exhaustion(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is True

    def test_stdout_http_429_line_triggers_on_failure(self):
        stdout = "HTTP 429: rate limit"
        assert self.provider.detect_quota_exhaustion(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is True
