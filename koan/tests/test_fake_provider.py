"""Tests for the fail-closed ``fake`` CLI provider (issue #2390).

Covers the three acceptance requirements:
  - allow path works (KOAN_ALLOW_FAKE_PROVIDER=1 → constructs, selectable);
  - deny path fails closed (no flag → error, never a real LLM, never a swap);
  - default provider resolution is unchanged when nothing is configured.
"""

import pytest

from app.provider import (
    _PROVIDERS,
    ClaudeProvider,
    FakeProvider,
    get_fallback_provider,
    get_provider,
    get_provider_by_name,
    get_provider_for_role,
    get_provider_name,
    is_known_provider,
    known_providers,
    reset_provider,
)
from app.provider.fake import (
    ALLOW_ENV,
    FakeProviderNotAllowed,
    fake_provider_allowed,
)


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """Keep the cached singleton from leaking a fake instance across tests."""
    reset_provider()
    yield
    reset_provider()


@pytest.fixture
def _allow_fake(monkeypatch):
    monkeypatch.setenv(ALLOW_ENV, "1")


@pytest.fixture
def _deny_fake(monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)


# ---------------------------------------------------------------------------
# Registration: `fake` is a known provider name
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_fake_registered(self):
        assert "fake" in _PROVIDERS
        assert _PROVIDERS["fake"] is FakeProvider

    def test_is_known_provider(self):
        assert is_known_provider("fake") is True

    def test_in_known_providers_list(self):
        assert "fake" in known_providers()

    def test_name_attribute(self):
        assert FakeProvider.name == "fake"


# ---------------------------------------------------------------------------
# Allow path: with the flag, the provider constructs and is selectable
# ---------------------------------------------------------------------------


class TestAllowPath:
    def test_allowed_helper_true(self, _allow_fake):
        assert fake_provider_allowed() is True

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_spellings(self, monkeypatch, value):
        monkeypatch.setenv(ALLOW_ENV, value)
        assert fake_provider_allowed() is True

    def test_constructs_when_allowed(self, _allow_fake):
        p = FakeProvider()
        assert isinstance(p, FakeProvider)
        assert p.name == "fake"

    def test_get_provider_returns_fake(self, monkeypatch, _allow_fake):
        monkeypatch.setenv("KOAN_CLI_PROVIDER", "fake")
        assert get_provider_name() == "fake"
        assert isinstance(get_provider(), FakeProvider)

    def test_get_provider_by_name(self, _allow_fake):
        assert isinstance(get_provider_by_name("fake"), FakeProvider)

    def test_role_resolution_returns_fake(self, monkeypatch, _allow_fake):
        """`cli.<role>: fake` resolves to a fresh FakeProvider instance."""
        monkeypatch.setattr(
            "app.config.get_cli_config",
            lambda project_name="": {"mission": ("fake", "")},
        )
        provider = get_provider_for_role("mission")
        assert isinstance(provider, FakeProvider)

    def test_fallback_returns_instance_when_allowed(self, monkeypatch, _allow_fake):
        monkeypatch.setattr(
            "app.config.get_cli_fallback",
            lambda project_name="": ("fake", ""),
        )
        assert isinstance(get_fallback_provider(), FakeProvider)

    def test_binary_is_noop_not_claude(self, _allow_fake):
        # Never pretends to be a real Claude CLI.
        assert FakeProvider().binary() == "true"

    def test_is_available_true(self, _allow_fake):
        assert FakeProvider().is_available() is True

    def test_build_command_runs_without_crashing(self, _allow_fake):
        cmd = FakeProvider().build_command(
            prompt="hello",
            allowed_tools=["Bash", "Read"],
            model="opus",
            fallback="sonnet",
            output_format="stream-json",
            max_turns=10,
            mcp_configs=["c.json"],
            skip_permissions=True,
            system_prompt="be nice",
        )
        # A harmless no-op invocation: just the binary, no real LLM flags.
        assert cmd == ["true"]

    def test_no_api_quota(self, _allow_fake):
        assert FakeProvider().has_api_quota() is False

    def test_binary_override_honored(self, monkeypatch, _allow_fake):
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        assert FakeProvider(binary_path="/opt/stub/echo").binary() == "/opt/stub/echo"


# ---------------------------------------------------------------------------
# Deny path: without the flag, selecting fake fails closed
# ---------------------------------------------------------------------------


class TestDenyPath:
    def test_allowed_helper_false(self, _deny_fake):
        assert fake_provider_allowed() is False

    def test_construction_raises(self, _deny_fake):
        with pytest.raises(FakeProviderNotAllowed):
            FakeProvider()

    def test_error_is_actionable(self, _deny_fake):
        with pytest.raises(FakeProviderNotAllowed) as exc:
            FakeProvider()
        msg = str(exc.value)
        assert ALLOW_ENV in msg  # tells the operator exactly what to set
        assert "fall back" in msg.lower()  # explains it will not silently swap

    def test_get_provider_by_name_raises(self, _deny_fake):
        with pytest.raises(FakeProviderNotAllowed):
            get_provider_by_name("fake")

    def test_get_provider_raises_never_swaps(self, monkeypatch, _deny_fake):
        """Selecting fake without the flag errors — it must NOT return Claude."""
        monkeypatch.setenv("KOAN_CLI_PROVIDER", "fake")
        assert get_provider_name() == "fake"
        with pytest.raises(FakeProviderNotAllowed):
            get_provider()

    def test_role_resolution_raises(self, monkeypatch, _deny_fake):
        monkeypatch.setattr(
            "app.config.get_cli_config",
            lambda project_name="": {"mission": ("fake", "")},
        )
        with pytest.raises(FakeProviderNotAllowed):
            get_provider_for_role("mission")

    def test_fallback_declines_instead_of_crashing(self, monkeypatch, _deny_fake):
        """A `cli.fallback: fake` without the flag returns None, not a crash.

        get_fallback_provider() is contractually Optional and is called on any
        non-zero mission exit — a real-provider failure must not raise
        FakeProviderNotAllowed during finalization just because fake is the
        configured fallback.
        """
        monkeypatch.setattr(
            "app.config.get_cli_fallback",
            lambda project_name="": ("fake", ""),
        )
        assert get_fallback_provider() is None

    def test_empty_string_flag_denies(self, monkeypatch):
        monkeypatch.setenv(ALLOW_ENV, "")
        assert fake_provider_allowed() is False
        with pytest.raises(FakeProviderNotAllowed):
            FakeProvider()

    def test_falsey_flag_denies(self, monkeypatch):
        monkeypatch.setenv(ALLOW_ENV, "0")
        assert fake_provider_allowed() is False
        with pytest.raises(FakeProviderNotAllowed):
            FakeProvider()


# ---------------------------------------------------------------------------
# Default resolution is unchanged when nothing is configured
# ---------------------------------------------------------------------------


class TestDefaultUnchanged:
    def test_default_is_claude_when_unset(self, monkeypatch, _deny_fake):
        monkeypatch.delenv("KOAN_CLI_PROVIDER", raising=False)
        monkeypatch.delenv("CLI_PROVIDER", raising=False)
        from unittest.mock import patch

        with patch("app.utils.load_config", return_value={}):
            assert get_provider_name() == "claude"

    def test_get_provider_default_is_claude(self, monkeypatch, _deny_fake):
        """With no fake selection, get_provider() is unaffected by the guard."""
        from unittest.mock import patch

        with patch("app.provider.get_provider_name", return_value="claude"):
            assert isinstance(get_provider(), ClaudeProvider)
