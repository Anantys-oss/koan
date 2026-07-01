"""Tests for the `cli:` config section: parsing, resolution, model coupling.

Covers app.config._parse_cli_value / get_cli_config / get_cli_fallback and
the role-aware get_model_config(role_providers=...).
"""

from contextlib import contextmanager
from unittest.mock import patch

import app.config as cfg
import app.provider as provider


@contextmanager
def _config(full_config, project_overrides=None):
    """Patch every config-loading seam to return the given dicts."""
    project_overrides = project_overrides or {}
    with patch("app.config._load_config", return_value=full_config), \
         patch("app.config._load_project_overrides", return_value=project_overrides), \
         patch("app.utils.load_config", return_value=full_config):
        provider.reset_provider()
        yield


# ---------------------------------------------------------------------------
# _parse_cli_value
# ---------------------------------------------------------------------------

class TestParseCliValue:
    def test_flavor_only(self):
        assert cfg._parse_cli_value("claude") == ("claude", "")

    def test_flavor_with_absolute_path(self):
        assert cfg._parse_cli_value("claude:/abs/deep-claude") == ("claude", "/abs/deep-claude")

    def test_flavor_with_relative_path(self):
        assert cfg._parse_cli_value("codex:bin/wrap") == ("codex", "bin/wrap")

    def test_splits_on_first_colon_only(self):
        # Extra colons in the path survive.
        assert cfg._parse_cli_value("claude:/a:b/x") == ("claude", "/a:b/x")

    def test_whitespace_trimmed(self):
        assert cfg._parse_cli_value("  claude : /p/x ".replace(" : ", ":").strip()) == ("claude", "/p/x")

    def test_unknown_flavor_returns_empty(self, capsys):
        assert cfg._parse_cli_value("bogus:/x") == ("", "")
        assert "unknown provider flavor" in capsys.readouterr().err

    def test_empty_returns_empty(self):
        assert cfg._parse_cli_value("") == ("", "")
        assert cfg._parse_cli_value(None) == ("", "")


# ---------------------------------------------------------------------------
# get_cli_config
# ---------------------------------------------------------------------------

class TestGetCliConfig:
    def test_absence_parity_all_roles_global(self):
        """No cli: section → every role resolves to (global_provider, '')."""
        with _config({"cli_provider": "codex"}):
            with patch("app.provider.get_provider_name", return_value="codex"):
                resolved = cfg.get_cli_config()
        assert resolved == {
            "mission": ("codex", ""),
            "chat": ("codex", ""),
            "lightweight": ("codex", ""),
            "review_mode": ("codex", ""),
            "reflect": ("codex", ""),
        }

    def test_default_section_per_role(self):
        full = {
            "cli_provider": "codex",
            "cli": {"default": {"review_mode": "claude:/p/deep-claude", "mission": "codex"}},
        }
        with _config(full):
            with patch("app.provider.get_provider_name", return_value="codex"):
                resolved = cfg.get_cli_config()
        assert resolved["review_mode"] == ("claude", "/p/deep-claude")
        assert resolved["mission"] == ("codex", "")
        # Unset roles fall back to the global provider.
        assert resolved["chat"] == ("codex", "")

    def test_project_override_beats_default(self):
        full = {"cli_provider": "codex", "cli": {"default": {"mission": "codex"}}}
        proj = {"cli": {"mission": "claude:/p/x"}}
        with _config(full, project_overrides=proj):
            with patch("app.provider.get_provider_name", return_value="codex"):
                resolved = cfg.get_cli_config("proj")
        assert resolved["mission"] == ("claude", "/p/x")

    def test_unknown_flavor_falls_through_to_global(self):
        full = {"cli_provider": "codex", "cli": {"default": {"mission": "bogus"}}}
        with _config(full):
            with patch("app.provider.get_provider_name", return_value="codex"):
                resolved = cfg.get_cli_config()
        assert resolved["mission"] == ("codex", "")


# ---------------------------------------------------------------------------
# get_cli_fallback
# ---------------------------------------------------------------------------

class TestGetCliFallback:
    def test_unset_returns_empty(self):
        with _config({"cli_provider": "codex"}):
            assert cfg.get_cli_fallback() == ("", "")

    def test_section_level_fallback(self):
        full = {"cli": {"fallback": "claude:/p/deep-claude"}}
        with _config(full):
            assert cfg.get_cli_fallback() == ("claude", "/p/deep-claude")

    def test_default_level_fallback_accepted(self):
        full = {"cli": {"default": {"fallback": "claude"}}}
        with _config(full):
            assert cfg.get_cli_fallback() == ("claude", "")

    def test_project_override_beats_global(self):
        full = {"cli": {"fallback": "codex"}}
        proj = {"cli": {"fallback": "claude:/p/x"}}
        with _config(full, project_overrides=proj):
            assert cfg.get_cli_fallback("proj") == ("claude", "/p/x")


# ---------------------------------------------------------------------------
# Model coupling: get_model_config(role_providers=...)
# ---------------------------------------------------------------------------

class TestModelCoupling:
    _MODELS = {
        "default": {"mission": "", "review_mode": "", "lightweight": "haiku", "fallback": "sonnet"},
        "codex": {"mission": "gpt-5-codex"},
        "claude": {"review_mode": "opus", "mission": "sonnet"},
    }

    def test_role_providers_none_equals_all_global(self):
        """Parity: role_providers=None == an all-global role_providers map."""
        full = {"cli_provider": "codex", "models": self._MODELS}
        with _config(full):
            with patch("app.provider.get_provider_name", return_value="codex"):
                none_cfg = cfg.get_model_config()
                all_global = cfg.get_model_config(role_providers={
                    k: "codex" for k in ("mission", "chat", "lightweight", "fallback", "review_mode", "reflect")
                })
        assert none_cfg == all_global

    def test_each_role_resolves_against_its_provider(self):
        full = {"cli_provider": "codex", "models": self._MODELS}
        with _config(full):
            with patch("app.provider.get_provider_name", return_value="codex"):
                resolved = cfg.get_model_config(role_providers={"mission": "codex", "review_mode": "claude"})
        assert resolved["mission"] == "gpt-5-codex"   # models.codex.mission
        assert resolved["review_mode"] == "opus"        # models.claude.review_mode
