"""Tests for app.cli_health — CLI binary availability + in-memory degraded state."""

from unittest.mock import MagicMock, patch

import pytest

from app import cli_health


@pytest.fixture(autouse=True)
def _clean_state():
    cli_health.clear()
    yield
    cli_health.clear()


def _fake_provider(available, binary="claude", name="claude"):
    p = MagicMock()
    p.is_available.return_value = available
    p.binary.return_value = binary
    p.name = name
    return p


class TestCheckPrimaryCli:
    def test_available(self):
        with patch("app.provider.get_provider", return_value=_fake_provider(True)):
            check = cli_health.check_primary_cli()
        assert check == cli_health.CliCheck(True, "claude", "claude")

    def test_missing(self):
        with patch("app.provider.get_provider",
                   return_value=_fake_provider(False, "codex", "codex")):
            check = cli_health.check_primary_cli()
        assert check.available is False
        assert check.binary == "codex"
        assert check.provider_name == "codex"

    def test_probe_error_reports_available(self):
        # A provider-resolution error must never false-positive a degraded state.
        with patch("app.provider.get_provider", side_effect=RuntimeError("boom")):
            check = cli_health.check_primary_cli()
        assert check.available is True


class TestDegradedState:
    def test_set_is_get_clear(self):
        assert cli_health.is_unavailable() is False
        assert cli_health.get_unavailable_info() is None
        cli_health.set_unavailable("claude", "claude")
        assert cli_health.is_unavailable() is True
        assert cli_health.get_unavailable_info() == {"binary": "claude", "provider": "claude"}
        cli_health.clear()
        assert cli_health.is_unavailable() is False
        assert cli_health.get_unavailable_info() is None

    def test_get_returns_copy(self):
        cli_health.set_unavailable("claude", "claude")
        info = cli_health.get_unavailable_info()
        info["binary"] = "mutated"
        assert cli_health.get_unavailable_info()["binary"] == "claude"


class TestThrottle:
    def test_should_warn_initially_true(self):
        assert cli_health.should_warn() is True

    def test_mark_warned_suppresses_within_cooldown(self):
        cli_health.mark_warned()
        assert cli_health.should_warn() is False

    def test_should_warn_true_after_cooldown(self):
        cli_health.mark_warned()
        assert cli_health.should_warn(cooldown_s=0) is True


class TestWarningMessage:
    def test_contains_binary_provider_and_restart_hint(self):
        msg = cli_health.warning_message("claude", "claude")
        assert "claude" in msg
        assert "PATH" in msg
        assert "restart" in msg.lower()
        assert msg.startswith("⚠️")

    def test_handles_empty_values(self):
        msg = cli_health.warning_message("", "")
        assert "(unknown)" in msg
        assert "(default)" in msg
