#!/usr/bin/env python3
"""Tests for GitHub authentication helper (github_auth.py)."""

import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from tests._helpers import run_module
from app.github_auth import (
    get_github_user,
    get_gh_token,
    get_gh_env,
    setup_github_auth,
)
from tests._helpers import run_module


# ── get_github_user ──────────────────────────────────────────────

class TestGetGithubUser:
    def test_returns_env_var(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        assert get_github_user() == "my-bot"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "  my-bot  ")
        assert get_github_user() == "my-bot"

    def test_returns_empty_when_not_set(self, monkeypatch):
        monkeypatch.delenv("GITHUB_USER", raising=False)
        assert get_github_user() == ""

    def test_returns_empty_for_empty_string(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "")
        assert get_github_user() == ""


# ── get_gh_token ─────────────────────────────────────────────────

class TestGetGhToken:
    @patch("app.github_auth.subprocess.run")
    def test_returns_token_on_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="ghp_abc123token\n"
        )
        assert get_gh_token("my-bot") == "ghp_abc123token"
        mock_run.assert_called_once_with(
            ["gh", "auth", "token", "--user", "my-bot"],
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10,
        )

    @patch("app.github_auth.subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert get_gh_token("my-bot") is None

    @patch("app.github_auth.subprocess.run")
    def test_returns_none_on_empty_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        assert get_gh_token("my-bot") is None

    def test_returns_none_for_empty_username(self):
        assert get_gh_token("") is None

    @patch("app.github_auth.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_none_when_gh_not_installed(self, mock_run):
        assert get_gh_token("my-bot") is None

    @patch("app.github_auth.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=10))
    def test_returns_none_on_timeout(self, mock_run):
        assert get_gh_token("my-bot") is None


# ── get_gh_env ───────────────────────────────────────────────────

class TestGetGhEnv:
    def test_empty_when_no_user(self, monkeypatch):
        monkeypatch.delenv("GITHUB_USER", raising=False)
        assert get_gh_env() == {}

    @patch("app.github_auth.get_gh_token", return_value="ghp_token123")
    def test_returns_token_env(self, mock_token, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        result = get_gh_env()
        assert result == {"GH_TOKEN": "ghp_token123"}

    @patch("app.github_auth.get_gh_token", return_value=None)
    def test_returns_empty_when_token_fails(self, mock_token, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert get_gh_env() == {}

    def test_uses_existing_gh_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.setenv("GH_TOKEN", "existing-token-123")
        result = get_gh_env()
        assert result == {"GH_TOKEN": "existing-token-123"}


# ── setup_github_auth ────────────────────────────────────────────

class TestSetupGithubAuth:
    def test_noop_when_no_user(self, monkeypatch):
        monkeypatch.delenv("GITHUB_USER", raising=False)
        assert setup_github_auth() is True

    @patch("app.github_auth.get_gh_token", return_value="ghp_token123")
    def test_sets_gh_token_on_success(self, mock_token, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert setup_github_auth() is True
        assert os.environ.get("GH_TOKEN") == "ghp_token123"

    @patch("app.notify.send_telegram")
    @patch("app.github_auth.get_gh_token", return_value=None)
    def test_trusts_existing_gh_token_without_user_lookup(
        self, mock_token, mock_send, monkeypatch
    ):
        # GH_TOKEN exported in env should be trusted directly: no
        # `gh auth token --user` lookup, no false-failure alert.
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.setenv("GH_TOKEN", "existing-token-123")
        assert setup_github_auth() is True
        mock_token.assert_not_called()
        mock_send.assert_not_called()
        assert os.environ.get("GH_TOKEN") == "existing-token-123"

    @patch("app.notify.send_telegram")
    @patch("app.github_auth.get_gh_token", return_value=None)
    def test_sends_alert_on_failure(self, mock_token, mock_send, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert setup_github_auth() is False
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "my-bot" in msg
        assert "failed" in msg.lower() or "⚠️" in msg

    @patch("app.github_auth.get_gh_token", return_value=None)
    def test_returns_false_even_if_alert_fails(self, mock_token, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        with patch("app.notify.send_telegram", side_effect=Exception("network error")):
            assert setup_github_auth() is False


# ── CLI entry point ──────────────────────────────────────────────

class TestCLIEntryPoint:
    def test_prints_token_on_success(self, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        mock_run = MagicMock(return_value=MagicMock(returncode=0, stdout="ghp_token123\n"))
        monkeypatch.setattr(subprocess, "run", mock_run)
        with pytest.raises(SystemExit) as exc_info:
            run_module("app.github_auth", run_name="__main__")
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "GH_TOKEN=ghp_token123" in captured.out

    def test_exits_0_when_no_user(self, monkeypatch):
        monkeypatch.delenv("GITHUB_USER", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            run_module("app.github_auth", run_name="__main__")
        assert exc_info.value.code == 0

    def test_exits_1_on_failure(self, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "my-bot")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        mock_run = MagicMock(return_value=MagicMock(returncode=1, stdout=""))
        monkeypatch.setattr(subprocess, "run", mock_run)
        with patch("app.notify.send_telegram"):
            with pytest.raises(SystemExit) as exc_info:
                run_module("app.github_auth", run_name="__main__")
            assert exc_info.value.code == 1
