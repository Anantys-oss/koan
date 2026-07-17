"""Tests for app.preflight — pre-flight quota check module."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    """Isolate the in-process probe success cache between tests."""
    from app.preflight import _reset_probe_cache
    _reset_probe_cache()
    yield
    _reset_probe_cache()


class TestPreflightQuotaCheck:
    """Tests for preflight_quota_check()."""

    # Note: preflight.py uses lazy imports inside the function body:
    #   from app.usage_tracker import _get_budget_mode
    #   from app.provider import get_provider
    # We must patch at the SOURCE module (app.usage_tracker, app.provider),
    # not at app.preflight.

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_quota_available(self, mock_budget, mock_get_prov):
        """When provider says quota is available, returns (True, None)."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (True, "")
        mock_get_prov.return_value = provider

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is True
        assert error is None
        provider.check_quota_available.assert_called_once_with("/tmp/proj")

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_quota_exhausted(self, mock_budget, mock_get_prov):
        """When provider says quota is exhausted, returns (False, detail)."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (False, "Rate limit exceeded")
        mock_get_prov.return_value = provider

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is False
        assert error == "Rate limit exceeded"

    @patch("app.usage_tracker._get_budget_mode", return_value="disabled")
    def test_budget_disabled_skips_check(self, mock_budget):
        """When budget_mode is 'disabled', skip the check entirely."""
        from app.preflight import preflight_quota_check

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is True
        assert error is None

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="session_only")
    def test_budget_session_only_still_checks(self, mock_budget, mock_get_prov):
        """When budget_mode is 'session_only', still run the preflight check."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (True, "")
        mock_get_prov.return_value = provider

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is True
        provider.check_quota_available.assert_called_once()

    @patch("app.provider.get_provider", side_effect=Exception("provider broken"))
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_provider_error_proceeds_optimistically(self, mock_budget, mock_prov):
        """If get_provider() raises, proceed optimistically (True, None)."""
        from app.preflight import preflight_quota_check

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is True
        assert error is None

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", side_effect=ImportError("no module"))
    def test_budget_mode_import_error_proceeds(self, mock_budget, mock_prov):
        """If _get_budget_mode import fails, skip check and continue to provider."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (True, "")
        mock_prov.return_value = provider

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is True

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_passes_project_path_to_provider(self, mock_budget, mock_prov):
        """Verify the project_path argument is forwarded to the provider."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (True, "")
        mock_prov.return_value = provider

        preflight_quota_check("/my/special/path", "/inst")
        provider.check_quota_available.assert_called_once_with("/my/special/path")

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_quota_exhausted_with_empty_detail(self, mock_budget, mock_prov):
        """Quota exhausted with empty error detail still returns False."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (False, "")
        mock_prov.return_value = provider

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert ok is False
        assert error == ""

    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_project_name_accepted(self, mock_budget, mock_prov):
        """project_name parameter is accepted (for future per-project providers)."""
        from app.preflight import preflight_quota_check

        provider = MagicMock()
        provider.check_quota_available.return_value = (True, "")
        mock_prov.return_value = provider

        ok, error = preflight_quota_check("/tmp/proj", "/tmp/inst", project_name="myproject")
        assert ok is True


class TestPreflightModuleStructure:
    """Verify module imports and structure."""

    def test_module_imports_cleanly(self):
        """preflight.py should import without side effects."""
        import importlib
        mod = importlib.import_module("app.preflight")
        assert hasattr(mod, "preflight_quota_check")

    def test_function_signature(self):
        """Check the function has expected parameters."""
        import inspect
        from app.preflight import preflight_quota_check
        sig = inspect.signature(preflight_quota_check)
        params = list(sig.parameters.keys())
        assert "project_path" in params
        assert "instance_dir" in params
        assert "project_name" in params

    def test_return_type_annotation(self):
        """Function has proper return type annotation."""
        import inspect
        from app.preflight import preflight_quota_check
        sig = inspect.signature(preflight_quota_check)
        # Return annotation should be Tuple[bool, Optional[str]]
        assert sig.return_annotation is not inspect.Parameter.empty


class TestPreflightProbeCache:
    """Successful probes are cached for preflight_cache_minutes."""

    def _provider(self, available=True, detail=""):
        provider = MagicMock()
        provider.name = "haze"
        provider.check_quota_available.return_value = (available, detail)
        return provider

    @patch("app.config.get_preflight_cache_minutes", return_value=10)
    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_success_cached_within_ttl(self, mock_budget, mock_prov, mock_ttl):
        from app.preflight import preflight_quota_check

        provider = self._provider()
        mock_prov.return_value = provider

        assert preflight_quota_check("/tmp/proj", "/tmp/instance") == (True, None)
        assert preflight_quota_check("/tmp/proj", "/tmp/instance") == (True, None)
        # Second call served from cache — probe ran exactly once.
        provider.check_quota_available.assert_called_once()

    @patch("app.config.get_preflight_cache_minutes", return_value=10)
    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_failure_never_cached(self, mock_budget, mock_prov, mock_ttl):
        from app.preflight import preflight_quota_check

        provider = self._provider(available=False, detail="429 rate limit")
        mock_prov.return_value = provider

        ok1, _ = preflight_quota_check("/tmp/proj", "/tmp/instance")
        ok2, _ = preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert (ok1, ok2) == (False, False)
        assert provider.check_quota_available.call_count == 2

    @patch("app.config.get_preflight_cache_minutes", return_value=10)
    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_cache_expires_after_ttl(self, mock_budget, mock_prov, mock_ttl):
        from app.preflight import preflight_quota_check

        provider = self._provider()
        mock_prov.return_value = provider

        # First probe at t=0, second call at t=11 minutes: cache expired.
        with patch("app.preflight.time.monotonic", side_effect=[0.0, 11 * 60.0, 11 * 60.0]):
            preflight_quota_check("/tmp/proj", "/tmp/instance")
            preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert provider.check_quota_available.call_count == 2

    @patch("app.config.get_preflight_cache_minutes", return_value=0)
    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_ttl_zero_disables_caching(self, mock_budget, mock_prov, mock_ttl):
        from app.preflight import preflight_quota_check

        provider = self._provider()
        mock_prov.return_value = provider

        preflight_quota_check("/tmp/proj", "/tmp/instance")
        preflight_quota_check("/tmp/proj", "/tmp/instance")
        assert provider.check_quota_available.call_count == 2

    @patch("app.config.get_preflight_cache_minutes", return_value=10)
    @patch("app.provider.get_provider")
    @patch("app.usage_tracker._get_budget_mode", return_value="full")
    def test_cache_is_per_provider_flavor(self, mock_budget, mock_prov, mock_ttl):
        from app.preflight import preflight_quota_check

        haze = self._provider()
        mock_prov.return_value = haze
        preflight_quota_check("/tmp/proj", "/tmp/instance")

        claude = MagicMock()
        claude.name = "claude"
        claude.check_quota_available.return_value = (True, "")
        mock_prov.return_value = claude
        preflight_quota_check("/tmp/proj", "/tmp/instance")

        # A cached success for haze must not skip claude's probe.
        claude.check_quota_available.assert_called_once()
