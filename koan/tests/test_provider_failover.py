"""Tests for provider_failover.py — cross-provider failover on quota exhaustion."""

import time
from unittest.mock import patch, MagicMock

import pytest

from app.provider_failover import (
    attempt_failover,
    check_primary_recovery,
    is_on_fallback,
    get_failover_status,
    reset_for_testing,
    _RECOVERY_PROBE_INTERVAL,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset failover state before and after each test."""
    reset_for_testing()
    yield
    reset_for_testing()


# ---------------------------------------------------------------------------
# attempt_failover
# ---------------------------------------------------------------------------


class TestAttemptFailover:

    def test_returns_none_when_no_fallbacks_configured(self):
        with patch("app.provider_failover._get_fallback_providers", return_value=[]):
            assert attempt_failover("claude") is None

    def test_returns_none_when_all_fallbacks_unavailable(self):
        mock_provider = MagicMock()
        mock_provider.return_value.is_available.return_value = False
        providers = {"claude": MagicMock, "copilot": mock_provider}
        with patch("app.provider_failover._get_fallback_providers", return_value=["copilot"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"):
            assert attempt_failover("claude") is None

    def test_skips_exhausted_provider(self):
        """If the exhausted provider is also in the fallback list, skip it."""
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        providers = {"claude": MagicMock, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["claude", "local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override") as mock_override:
            result = attempt_failover("claude")
            assert result == "local"
            mock_override.assert_called_once_with("local")

    def test_switches_to_first_available_fallback(self):
        mock_copilot = MagicMock()
        mock_copilot.return_value.is_available.return_value = False
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        providers = {"claude": MagicMock, "copilot": mock_copilot, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["copilot", "local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override") as mock_override:
            result = attempt_failover("claude")
            assert result == "local"
            mock_override.assert_called_once_with("local")

    def test_sets_primary_state(self):
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        providers = {"claude": MagicMock, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"):
            attempt_failover("claude")
            assert is_on_fallback()

    def test_skips_unknown_provider(self):
        """Unknown providers in the fallback list are skipped gracefully."""
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        providers = {"claude": MagicMock, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["nonexistent", "local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"):
            result = attempt_failover("claude")
            assert result == "local"


# ---------------------------------------------------------------------------
# check_primary_recovery
# ---------------------------------------------------------------------------


class TestCheckPrimaryRecovery:

    def test_returns_false_when_not_on_fallback(self):
        assert check_primary_recovery() is False

    def test_returns_false_when_probe_too_soon(self):
        """Probing before the interval has elapsed returns False."""
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        mock_claude = MagicMock()
        mock_claude.return_value.check_quota_available.return_value = (True, "")
        providers = {"claude": mock_claude, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"):
            attempt_failover("claude")

        # Immediately after failover, probe is too soon (within interval)
        with patch("app.provider_failover._get_available_providers", return_value=providers):
            assert check_primary_recovery() is False

    def test_returns_true_when_primary_recovered(self):
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        mock_claude = MagicMock()
        mock_claude.return_value.check_quota_available.return_value = (True, "")
        providers = {"claude": mock_claude, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"):
            attempt_failover("claude")

        # Fast-forward past the probe interval
        import app.provider_failover as pf
        pf._last_recovery_probe = time.time() - _RECOVERY_PROBE_INTERVAL - 1

        with patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.clear_provider_override"):
            result = check_primary_recovery("/tmp/test")
            assert result is True
            assert not is_on_fallback()

    def test_returns_false_when_primary_still_exhausted(self):
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        mock_claude = MagicMock()
        mock_claude.return_value.check_quota_available.return_value = (False, "quota exhausted")
        providers = {"claude": mock_claude, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"):
            attempt_failover("claude")

        # Fast-forward past the probe interval
        import app.provider_failover as pf
        pf._last_recovery_probe = time.time() - _RECOVERY_PROBE_INTERVAL - 1

        with patch("app.provider_failover._get_available_providers", return_value=providers):
            result = check_primary_recovery("/tmp/test")
            assert result is False
            assert is_on_fallback()


# ---------------------------------------------------------------------------
# is_on_fallback / get_failover_status
# ---------------------------------------------------------------------------


class TestStatusFunctions:

    def test_is_on_fallback_false_by_default(self):
        assert not is_on_fallback()

    def test_get_failover_status_empty_by_default(self):
        assert get_failover_status() == ""

    def test_get_failover_status_shows_info_during_failover(self):
        mock_local = MagicMock()
        mock_local.return_value.is_available.return_value = True
        providers = {"claude": MagicMock, "local": mock_local}
        with patch("app.provider_failover._get_fallback_providers", return_value=["local"]), \
             patch("app.provider_failover._get_available_providers", return_value=providers), \
             patch("app.provider.set_provider_override"), \
             patch("app.provider.get_provider_name", return_value="local"):
            attempt_failover("claude")
            status = get_failover_status()
            assert "local" in status
            assert "claude" in status


# ---------------------------------------------------------------------------
# Provider override integration
# ---------------------------------------------------------------------------


class TestProviderOverride:

    def test_set_and_clear_provider_override(self):
        from app.provider import (
            set_provider_override,
            clear_provider_override,
            _provider_override,
        )
        set_provider_override("copilot")
        from app.provider import _provider_override as after_set
        assert after_set == "copilot"

        clear_provider_override()
        from app.provider import _provider_override as after_clear
        assert after_clear is None

    def test_override_takes_precedence_in_get_provider_name(self):
        from app.provider import set_provider_override, get_provider_name, clear_provider_override
        set_provider_override("copilot")
        try:
            assert get_provider_name() == "copilot"
        finally:
            clear_provider_override()
