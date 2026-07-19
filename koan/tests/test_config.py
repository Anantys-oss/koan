"""Tests for app.config — configuration loading and access."""

import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest


@contextmanager
def _mock_config(data: dict):
    """Mock load_config to return a specific config dict."""
    with patch("app.config._load_config", return_value=data):
        yield


# --- get_chat_tools ---


class TestGetChatTools:
    def test_default(self):
        from app.config import get_chat_tools

        with _mock_config({}):
            assert get_chat_tools() == "Read,Glob,Grep"

    def test_custom(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"chat": ["Read", "Write"]}}):
            assert get_chat_tools() == "Read,Write"

    def test_string_value_passed_through(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"chat": "Read,Custom"}}):
            assert get_chat_tools() == "Read,Custom"

    def test_non_list_non_string_uses_default(self):
        from app.config import get_chat_tools

        with _mock_config({"tools": {"chat": 42}}):
            assert get_chat_tools() == "Read,Glob,Grep"


# --- get_mission_tools ---


class TestGetMissionTools:
    def test_default(self):
        from app.config import get_mission_tools

        with _mock_config({}):
            assert get_mission_tools() == "Read,Glob,Grep,Edit,Write,Bash,Skill"

    def test_custom(self):
        from app.config import get_mission_tools

        with _mock_config({"tools": {"mission": ["Read", "Bash"]}}):
            assert get_mission_tools() == "Read,Bash"


# --- get_contemplative_tools ---


class TestGetContemplativeTools:
    def test_default(self):
        from app.config import get_contemplative_tools

        with _mock_config({}):
            assert get_contemplative_tools() == "Read,Write,Glob,Grep"

    def test_custom(self):
        from app.config import get_contemplative_tools

        with _mock_config({"tools": {"contemplative": ["Read", "Glob", "Grep", "Bash"]}}):
            assert get_contemplative_tools() == "Read,Glob,Grep,Bash"

    def test_string_value_passed_through(self):
        from app.config import get_contemplative_tools

        with _mock_config({"tools": {"contemplative": "Read,Write"}}):
            assert get_contemplative_tools() == "Read,Write"


# --- get_allowed_tools (backward compat) ---


class TestGetAllowedTools:
    def test_delegates_to_mission_tools(self):
        from app.config import get_allowed_tools

        with _mock_config({}):
            assert get_allowed_tools() == "Read,Glob,Grep,Edit,Write,Bash,Skill"


# --- get_tools_description ---


class TestGetToolsDescription:
    def test_default_empty(self):
        from app.config import get_tools_description

        with _mock_config({}):
            assert get_tools_description() == ""

    def test_custom(self):
        from app.config import get_tools_description

        with _mock_config({"tools": {"description": "Tools info"}}):
            assert get_tools_description() == "Tools info"


# --- get_model_config ---


class TestGetModelConfig:
    def test_defaults(self):
        from app.config import get_model_config

        with _mock_config({}):
            result = get_model_config()
        assert result["mission"] == ""
        assert result["chat"] == ""
        assert result["lightweight"] == "haiku"
        assert result["fallback"] == "sonnet"
        assert result["review_mode"] == ""

    def test_custom_models(self):
        from app.config import get_model_config

        with _mock_config({"models": {"mission": "opus", "chat": "sonnet"}}):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert result["chat"] == "sonnet"
        assert result["lightweight"] == "haiku"  # not overridden


class TestGetModelConfigProviderSection:
    """Tests for provider-specific model sections (models_for_{provider})."""

    def test_provider_section_overrides_global_models(self):
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"mission": "claude-opus"},
            "models_for_codex": {"mission": "gpt-5.5"},
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="codex"):
            result = get_model_config()
        assert result["mission"] == "gpt-5.5"

    def test_provider_section_per_key_fallback(self):
        """Key absent from provider section falls back to global models."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"mission": "claude-opus", "chat": "claude-haiku"},
            "models_for_codex": {"mission": "gpt-5.5"},  # only mission overridden
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="codex"):
            result = get_model_config()
        assert result["mission"] == "gpt-5.5"
        assert result["chat"] == "claude-haiku"  # falls back to global models

    def test_no_provider_section_falls_back_to_global_models(self):
        """No provider section → global models unchanged."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {"models": {"mission": "claude-sonnet"}}
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="codex"):
            result = get_model_config()
        assert result["mission"] == "claude-sonnet"

    def test_per_project_beats_provider_section(self):
        """Per-project models override wins over global provider section."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"chat": "gpt-5.5"},
            "models_for_codex": {"chat": "gpt-5.5"},
        }
        project_overrides = {"models": {"chat": "gpt-4o-mini"}}
        with (
            _mock_config(config),
            patch("app.provider.get_provider_name", return_value="codex"),
            patch("app.config._load_project_overrides", return_value=project_overrides),
        ):
            result = get_model_config("my-project")
        assert result["chat"] == "gpt-4o-mini"

    def test_hyphen_to_underscore_normalization(self):
        """Provider name with hyphens is normalized to underscores for the key."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"mission": "default-model"},
            "models_for_ollama_launch": {"mission": "llama3"},
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="ollama-launch"):
            result = get_model_config()
        assert result["mission"] == "llama3"

    def test_provider_resolution_error_falls_back_gracefully(self):
        """If provider resolution raises, global models are returned unchanged."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {"models": {"mission": "claude-sonnet"}}
        with _mock_config(config), patch("app.provider.get_provider_name", side_effect=RuntimeError("oops")):
            result = get_model_config()
        assert result["mission"] == "claude-sonnet"


class TestGetModelConfigNestedStructure:
    """Tests for new nested models.default / models.{provider} structure."""

    def test_nested_default_section(self):
        """New nested models.default section works correctly."""
        from app.config import get_model_config

        config = {
            "models": {
                "default": {
                    "mission": "opus",
                    "chat": "haiku",
                    "lightweight": "haiku",
                    "fallback": "sonnet",
                    "review_mode": "",
                    "reflect": "",
                }
            }
        }
        with _mock_config(config):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert result["chat"] == "haiku"
        assert result["lightweight"] == "haiku"

    def test_nested_provider_section(self):
        """New nested models.{provider} section overrides models.default."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {
                "default": {"mission": "opus", "chat": "haiku"},
                "codex": {"mission": "gpt-5.5"},
            }
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="codex"):
            result = get_model_config()
        assert result["mission"] == "gpt-5.5"
        assert result["chat"] == "haiku"  # falls back to default

    def test_nested_per_key_fallback(self):
        """Key missing from provider section falls back to default."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {
                "default": {"mission": "opus", "chat": "sonnet", "lightweight": "haiku"},
                "claude": {"mission": "opus-turbo"},
            }
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="claude"):
            result = get_model_config()
        assert result["mission"] == "opus-turbo"
        assert result["chat"] == "sonnet"
        assert result["lightweight"] == "haiku"

    def test_nested_provider_with_hyphens_in_name(self):
        """Nested provider names can use hyphens as literal keys."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {
                "default": {"mission": "default"},
                "ollama-launch": {"mission": "llama3"},
            }
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="ollama-launch"):
            result = get_model_config()
        assert result["mission"] == "llama3"


class TestBackwardCompatNormalization:
    """Tests for legacy structure detection and normalization."""

    def test_legacy_flat_models_normalized_to_default(self):
        """Legacy flat models.{role} is normalized to models.default."""
        from unittest.mock import patch
        from io import StringIO

        from app.config import get_model_config

        config = {
            "models": {
                "mission": "opus",
                "chat": "haiku",
                "lightweight": "haiku",
                "fallback": "sonnet",
            }
        }
        stderr = StringIO()
        with _mock_config(config), patch("sys.stderr", stderr):
            # Reset the module-level guard to trigger deprecation warning
            import app.config

            app.config._MODEL_CONFIG_NORMALIZED = False
            os.environ.pop("_KOAN_MODELS_DEPRECATION_SHOWN", None)
            result = get_model_config()

        assert result["mission"] == "opus"
        assert result["chat"] == "haiku"
        assert "DEPRECATED" in stderr.getvalue()
        os.environ.pop("_KOAN_MODELS_DEPRECATION_SHOWN", None)

    def test_legacy_models_for_provider_normalized(self):
        """Legacy top-level models_for_* keys are folded into models.{provider}."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"mission": "default-mission"},
            "models_for_codex": {"mission": "gpt-5.5", "chat": "gpt-4"},
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="codex"):
            result = get_model_config()
        assert result["mission"] == "gpt-5.5"
        assert result["chat"] == "gpt-4"

    def test_legacy_flat_plus_models_for_provider(self):
        """Both legacy flat models and models_for_* are normalized together."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"mission": "default-opus", "chat": "default-haiku"},
            "models_for_claude": {"mission": "claude-opus"},
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="claude"):
            result = get_model_config()
        assert result["mission"] == "claude-opus"
        assert result["chat"] == "default-haiku"

    def test_new_structure_beats_legacy_on_collision(self):
        """When both legacy and new forms exist, new form (models.provider) wins."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {
                "mission": "legacy-flat",
                "default": {"mission": "new-default"},
                "codex": {"mission": "new-provider"},
            },
            "models_for_codex": {"mission": "legacy-provider"},
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="codex"):
            result = get_model_config()
        # New provider form should win over legacy
        assert result["mission"] == "new-provider"

    def test_legacy_underscore_normalization_in_top_level_keys(self):
        """Legacy models_for_ollama_launch key (with underscores) is handled correctly."""
        from unittest.mock import patch

        from app.config import get_model_config

        config = {
            "models": {"mission": "default"},
            "models_for_ollama_launch": {"mission": "llama3"},
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="ollama-launch"):
            result = get_model_config()
        assert result["mission"] == "llama3"


class TestModelConfigScenarios:
    """End-to-end scenarios requested in review: no models / flat / models_for_* / new nested."""

    def _reset_guard(self):
        import app.config

        app.config._MODEL_CONFIG_NORMALIZED = False
        os.environ.pop("_KOAN_MODELS_DEPRECATION_SHOWN", None)

    def test_scenario_no_models_entry_falls_back_to_defaults(self):
        """1) No 'models' entry → sane built-in defaults, no warning."""
        from io import StringIO
        from unittest.mock import patch

        from app.config import get_model_config

        self._reset_guard()
        stderr = StringIO()
        with _mock_config({}), patch("sys.stderr", stderr):
            result = get_model_config()
        assert result["mission"] == ""
        assert result["lightweight"] == "haiku"
        assert result["fallback"] == "sonnet"
        assert "DEPRECATED" not in stderr.getvalue()

    def test_scenario_flat_models_only_emits_deprecated(self):
        """2) Flat models entry only → works and emits DEPRECATED."""
        from io import StringIO
        from unittest.mock import patch

        from app.config import get_model_config

        self._reset_guard()
        stderr = StringIO()
        config = {"models": {"mission": "opus", "chat": "haiku"}}
        with _mock_config(config), patch("sys.stderr", stderr):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert result["chat"] == "haiku"
        assert "DEPRECATED" in stderr.getvalue()

    def test_scenario_models_for_provider_emits_deprecated(self):
        """3) models_for_FOO entry → works and emits DEPRECATED."""
        from io import StringIO
        from unittest.mock import patch

        from app.config import get_model_config

        self._reset_guard()
        stderr = StringIO()
        config = {"models_for_claude": {"mission": "opus"}}
        with (
            _mock_config(config),
            patch("sys.stderr", stderr),
            patch("app.provider.get_provider_name", return_value="claude"),
        ):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert "DEPRECATED" in stderr.getvalue()

    def test_scenario_new_nested_structure_no_warning(self):
        """4) Final valid structure models.{harness}.{role} → works, no warning."""
        from io import StringIO
        from unittest.mock import patch

        from app.config import get_model_config

        self._reset_guard()
        stderr = StringIO()
        config = {
            "models": {
                "default": {"mission": "sonnet"},
                "claude": {"mission": "opus", "chat": "haiku"},
            }
        }
        with (
            _mock_config(config),
            patch("sys.stderr", stderr),
            patch("app.provider.get_provider_name", return_value="claude"),
        ):
            result = get_model_config()
        assert result["mission"] == "opus"
        assert result["chat"] == "haiku"
        assert "DEPRECATED" not in stderr.getvalue()

    def test_env_var_suppresses_deprecation_in_subprocess(self):
        """Env var set by parent process suppresses warning in child processes."""
        import app.config
        from io import StringIO
        from unittest.mock import patch

        from app.config import get_model_config

        self._reset_guard()
        os.environ["_KOAN_MODELS_DEPRECATION_SHOWN"] = "1"
        stderr = StringIO()
        config = {"models": {"mission": "opus"}}
        try:
            with _mock_config(config), patch("sys.stderr", stderr):
                app.config._MODEL_CONFIG_NORMALIZED = False
                result = get_model_config()
            assert result["mission"] == "opus"
            assert "DEPRECATED" not in stderr.getvalue()
        finally:
            os.environ.pop("_KOAN_MODELS_DEPRECATION_SHOWN", None)

    def test_legacy_flat_does_not_clobber_new_default_on_collision(self):
        """Legacy flat keys must NOT overwrite an explicit models.default (new wins)."""
        from unittest.mock import patch

        from app.config import get_model_config

        self._reset_guard()
        config = {
            "models": {
                "mission": "legacy-flat",
                "default": {"mission": "new-default"},
            }
        }
        with _mock_config(config), patch("app.provider.get_provider_name", return_value="claude"):
            result = get_model_config()
        assert result["mission"] == "new-default"


# --- get_start_on_pause ---


class TestGetStartOnPause:
    def test_default_false(self):
        from app.config import get_start_on_pause

        with _mock_config({}):
            assert get_start_on_pause() is False

    def test_enabled(self):
        from app.config import get_start_on_pause

        with _mock_config({"start_on_pause": True}):
            assert get_start_on_pause() is True


# --- get_auto_pause ---


class TestGetAutoPause:
    def test_default_true(self):
        from app.config import get_auto_pause

        with _mock_config({}):
            assert get_auto_pause() is True

    def test_disabled(self):
        from app.config import get_auto_pause

        with _mock_config({"auto_pause": False}):
            assert get_auto_pause() is False

    def test_explicit_true(self):
        from app.config import get_auto_pause

        with _mock_config({"auto_pause": True}):
            assert get_auto_pause() is True


# --- get_skip_permissions ---


class TestGetSkipPermissions:
    def test_default_false(self):
        from app.config import get_skip_permissions

        with _mock_config({}):
            assert get_skip_permissions() is False

    def test_enabled(self):
        from app.config import get_skip_permissions

        with _mock_config({"skip_permissions": True}):
            # Pure config read even under root: root handling is
            # provider-specific (ClaudeProvider.build_permission_args).
            assert get_skip_permissions() is True

    def test_explicit_false(self):
        from app.config import get_skip_permissions

        with _mock_config({"skip_permissions": False}):
            assert get_skip_permissions() is False


# --- is_rebase_foreign_prs_allowed ---


class TestIsRebaseForeignPrsAllowed:
    def test_default_false(self):
        from app.config import is_rebase_foreign_prs_allowed

        with _mock_config({}):
            assert is_rebase_foreign_prs_allowed() is False

    def test_enabled(self):
        from app.config import is_rebase_foreign_prs_allowed

        with _mock_config({"allow_rebase_foreign_prs": True}):
            assert is_rebase_foreign_prs_allowed() is True


# --- get_debug_enabled ---


class TestGetDebugEnabled:
    def test_default_false(self):
        from app.config import get_debug_enabled

        with _mock_config({}):
            assert get_debug_enabled() is False

    def test_explicit_true(self):
        from app.config import get_debug_enabled

        with _mock_config({"debug": True}):
            assert get_debug_enabled() is True

    def test_explicit_false(self):
        from app.config import get_debug_enabled

        with _mock_config({"debug": False}):
            assert get_debug_enabled() is False


# --- get_max_runs ---


class TestIsUnlimitedQuota:
    def test_default_false(self):
        from app.config import is_unlimited_quota

        with _mock_config({}):
            assert is_unlimited_quota() is False

    def test_true_when_set(self):
        from app.config import is_unlimited_quota

        with _mock_config({"usage": {"unlimited_quota": True}}):
            assert is_unlimited_quota() is True

    def test_false_when_explicit(self):
        from app.config import is_unlimited_quota

        with _mock_config({"usage": {"unlimited_quota": False}}):
            assert is_unlimited_quota() is False

    def test_true_from_legacy_top_level(self):
        from app.config import is_unlimited_quota

        with _mock_config({"unlimited_quota": True, "usage": {}}):
            assert is_unlimited_quota() is True

    def test_nested_false_overrides_legacy_top_level_true(self):
        from app.config import is_unlimited_quota

        with _mock_config({"unlimited_quota": True, "usage": {"unlimited_quota": False}}):
            assert is_unlimited_quota() is False

    def test_truthy_string_coerced(self):
        from app.config import is_unlimited_quota

        with _mock_config({"usage": {"unlimited_quota": "yes"}}):
            assert is_unlimited_quota() is True

    def test_non_dict_usage_returns_false(self):
        from app.config import is_unlimited_quota

        with _mock_config({"usage": "malformed"}):
            assert is_unlimited_quota() is False

    def test_load_config_error_returns_false(self):
        from app.config import is_unlimited_quota

        with patch("app.config._load_config", side_effect=OSError("broken")):
            assert is_unlimited_quota() is False


class TestGetMaxRuns:
    def test_default(self):
        from app.config import get_max_runs

        with _mock_config({}):
            assert get_max_runs() == 60

    def test_custom(self):
        from app.config import get_max_runs

        with _mock_config({"max_runs_per_day": 50}):
            assert get_max_runs() == 50

    def test_string_value_coerced(self):
        from app.config import get_max_runs

        with _mock_config({"max_runs_per_day": "30"}):
            assert get_max_runs() == 30


# --- get_interval_seconds ---


class TestGetIntervalSeconds:
    def test_default(self):
        from app.config import get_interval_seconds

        with _mock_config({}):
            assert get_interval_seconds() == 300

    def test_custom(self):
        from app.config import get_interval_seconds

        with _mock_config({"interval_seconds": 120}):
            assert get_interval_seconds() == 120


# --- get_same_project_stickiness_percent ---


class TestGetSameProjectStickinessPercent:
    def test_default_disabled(self):
        from app.config import get_same_project_stickiness_percent

        with _mock_config({}):
            assert get_same_project_stickiness_percent() == 0

    def test_reads_nested_prompt_caching_value(self):
        from app.config import get_same_project_stickiness_percent

        with _mock_config({"prompt_caching": {"same_project_stickiness_percent": 35}}):
            assert get_same_project_stickiness_percent() == 35

    def test_clamps_out_of_range_values(self):
        from app.config import get_same_project_stickiness_percent

        with _mock_config({"prompt_caching": {"same_project_stickiness_percent": 999}}):
            assert get_same_project_stickiness_percent() == 100

        with _mock_config({"prompt_caching": {"same_project_stickiness_percent": -5}}):
            assert get_same_project_stickiness_percent() == 0


# --- get_fast_reply_model ---


class TestGetFastReplyModel:
    def test_disabled_by_default(self):
        from app.config import get_fast_reply_model

        with _mock_config({}):
            assert get_fast_reply_model() == ""

    def test_enabled_returns_lightweight(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": True, "models": {"lightweight": "flash"}}):
            assert get_fast_reply_model() == "flash"

    def test_enabled_uses_default_lightweight(self):
        from app.config import get_fast_reply_model

        with _mock_config({"fast_reply": True}):
            assert get_fast_reply_model() == "haiku"


# --- get_branch_prefix ---


class TestGetBranchPrefix:
    def test_default(self):
        from app.config import get_branch_prefix

        with _mock_config({}):
            assert get_branch_prefix() == "koan/"

    def test_custom(self):
        from app.config import get_branch_prefix

        with _mock_config({"branch_prefix": "mybot"}):
            assert get_branch_prefix() == "mybot/"

    def test_strips_trailing_slash(self):
        from app.config import get_branch_prefix

        with _mock_config({"branch_prefix": "agent/"}):
            assert get_branch_prefix() == "agent/"

    def test_empty_string_defaults_to_koan(self):
        from app.config import get_branch_prefix

        with _mock_config({"branch_prefix": ""}):
            assert get_branch_prefix() == "koan/"


# --- get_contemplative_chance ---


class TestGetContemplativeChance:
    def test_default(self):
        from app.config import get_contemplative_chance

        with _mock_config({}):
            assert get_contemplative_chance() == 10

    def test_custom(self):
        from app.config import get_contemplative_chance

        with _mock_config({"contemplative_chance": 25}):
            assert get_contemplative_chance() == 25

    def test_zero(self):
        from app.config import get_contemplative_chance

        with _mock_config({"contemplative_chance": 0}):
            assert get_contemplative_chance() == 0


# --- get_skill_timeout ---


class TestGetSkillTimeout:
    def test_default(self):
        from app.config import get_skill_timeout

        with _mock_config({}):
            assert get_skill_timeout() == 7200

    def test_custom(self):
        from app.config import get_skill_timeout

        with _mock_config({"skill_timeout": 1800}):
            assert get_skill_timeout() == 1800

    def test_string_value_coerced(self):
        from app.config import get_skill_timeout

        with _mock_config({"skill_timeout": "7200"}):
            assert get_skill_timeout() == 7200

    def test_invalid_string_returns_default(self):
        from app.config import get_skill_timeout

        with _mock_config({"skill_timeout": "forever"}):
            assert get_skill_timeout() == 7200

    def test_none_returns_default(self):
        from app.config import get_skill_timeout

        with _mock_config({"skill_timeout": None}):
            assert get_skill_timeout() == 7200


# --- get_first_output_timeout ---


class TestGetFirstOutputTimeout:
    def test_default(self):
        from app.config import get_first_output_timeout

        with _mock_config({}):
            assert get_first_output_timeout() == 600

    def test_custom(self):
        from app.config import get_first_output_timeout

        with _mock_config({"first_output_timeout": 300}):
            assert get_first_output_timeout() == 300

    def test_zero_disables(self):
        from app.config import get_first_output_timeout

        with _mock_config({"first_output_timeout": 0}):
            assert get_first_output_timeout() == 0


# --- get_rebase_first_output_timeout ---


class TestGetRebaseFirstOutputTimeout:
    def test_defaults_to_first_output_timeout(self):
        from app.config import get_rebase_first_output_timeout

        with _mock_config({"first_output_timeout": 600}):
            assert get_rebase_first_output_timeout() == 600

    def test_uses_override(self):
        from app.config import get_rebase_first_output_timeout

        with _mock_config({
            "first_output_timeout": 600,
            "rebase_first_output_timeout": 1800,
        }):
            assert get_rebase_first_output_timeout() == 1800


class TestGetRebaseReviewIdleTimeout:
    def test_defaults_to_rebase_first_output_timeout(self):
        from app.config import get_rebase_review_idle_timeout

        with _mock_config({"first_output_timeout": 600, "rebase_first_output_timeout": 1800}):
            assert get_rebase_review_idle_timeout() == 1800

    def test_uses_override(self):
        from app.config import get_rebase_review_idle_timeout

        with _mock_config({
            "first_output_timeout": 600,
            "rebase_first_output_timeout": 1800,
            "rebase_review_idle_timeout": 2400,
        }):
            assert get_rebase_review_idle_timeout() == 2400


class TestGetRebaseReviewMaxDuration:
    def test_defaults_to_skill_timeout(self):
        from app.config import get_rebase_review_max_duration

        with _mock_config({"skill_timeout": 7200}):
            assert get_rebase_review_max_duration() == 7200

    def test_uses_override(self):
        from app.config import get_rebase_review_max_duration

        with _mock_config({"skill_timeout": 7200, "rebase_review_max_duration": 10800}):
            assert get_rebase_review_max_duration() == 10800


class TestGetRebaseCiIdleTimeout:
    def test_defaults_to_rebase_first_output_timeout(self):
        from app.config import get_rebase_ci_idle_timeout

        with _mock_config({"first_output_timeout": 600, "rebase_first_output_timeout": 1800}):
            assert get_rebase_ci_idle_timeout() == 1800

    def test_uses_override(self):
        from app.config import get_rebase_ci_idle_timeout

        with _mock_config({
            "first_output_timeout": 600,
            "rebase_first_output_timeout": 1800,
            "rebase_ci_idle_timeout": 2400,
        }):
            assert get_rebase_ci_idle_timeout() == 2400


class TestGetRebaseCiMaxDuration:
    def test_defaults_to_skill_timeout(self):
        from app.config import get_rebase_ci_max_duration

        with _mock_config({"skill_timeout": 7200}):
            assert get_rebase_ci_max_duration() == 7200

    def test_uses_override(self):
        from app.config import get_rebase_ci_max_duration

        with _mock_config({"skill_timeout": 7200, "rebase_ci_max_duration": 9000}):
            assert get_rebase_ci_max_duration() == 9000


class TestGetRebaseIncludeBotFeedback:
    def test_default_true(self):
        from app.config import get_rebase_include_bot_feedback

        with _mock_config({}):
            assert get_rebase_include_bot_feedback() is True

    def test_uses_override(self):
        from app.config import get_rebase_include_bot_feedback

        with _mock_config({"rebase_include_bot_feedback": False}):
            assert get_rebase_include_bot_feedback() is False


# --- get_skill_max_turns ---


class TestGetSkillMaxTurns:
    def test_default(self):
        from app.config import get_skill_max_turns

        with _mock_config({}):
            assert get_skill_max_turns() == 200

    def test_custom(self):
        from app.config import get_skill_max_turns

        with _mock_config({"skill_max_turns": 100}):
            assert get_skill_max_turns() == 100

    def test_string_value_coerced(self):
        from app.config import get_skill_max_turns

        with _mock_config({"skill_max_turns": "300"}):
            assert get_skill_max_turns() == 300

    def test_invalid_string_returns_default(self):
        from app.config import get_skill_max_turns

        with _mock_config({"skill_max_turns": "infinite"}):
            assert get_skill_max_turns() == 200


# --- get_analysis_max_turns ---


class TestGetAnalysisMaxTurns:
    def test_default(self):
        from app.config import get_analysis_max_turns

        with _mock_config({}):
            assert get_analysis_max_turns() == 75

    def test_custom(self):
        from app.config import get_analysis_max_turns

        with _mock_config({"analysis_max_turns": 100}):
            assert get_analysis_max_turns() == 100

    def test_string_value_coerced(self):
        from app.config import get_analysis_max_turns

        with _mock_config({"analysis_max_turns": "100"}):
            assert get_analysis_max_turns() == 100

    def test_invalid_string_returns_default(self):
        from app.config import get_analysis_max_turns

        with _mock_config({"analysis_max_turns": "lots"}):
            assert get_analysis_max_turns() == 75


# --- get_reply_max_turns ---


class TestGetReplyMaxTurns:
    def test_default(self):
        from app.config import get_reply_max_turns

        with _mock_config({}):
            assert get_reply_max_turns() == 20

    def test_custom(self):
        from app.config import get_reply_max_turns

        with _mock_config({"reply_max_turns": 30}):
            assert get_reply_max_turns() == 30

    def test_string_value_coerced(self):
        from app.config import get_reply_max_turns

        with _mock_config({"reply_max_turns": "12"}):
            assert get_reply_max_turns() == 12

    def test_invalid_string_returns_default(self):
        from app.config import get_reply_max_turns

        with _mock_config({"reply_max_turns": "many"}):
            assert get_reply_max_turns() == 20


# --- get_mission_timeout ---


class TestGetMissionTimeout:
    def test_default(self):
        from app.config import get_mission_timeout

        with _mock_config({}):
            assert get_mission_timeout() == 3600

    def test_custom(self):
        from app.config import get_mission_timeout

        with _mock_config({"mission_timeout": 1800}):
            assert get_mission_timeout() == 1800

    def test_zero_disables(self):
        from app.config import get_mission_timeout

        with _mock_config({"mission_timeout": 0}):
            assert get_mission_timeout() == 0


# --- get_bash_foreground_timeout_ms ---


class TestGetBashForegroundTimeoutMs:
    def test_default(self):
        from app.config import get_bash_foreground_timeout_ms

        with _mock_config({}):
            # Default 900s (15 min) with default mission_timeout 3600s.
            assert get_bash_foreground_timeout_ms() == 900_000

    def test_custom_honored(self):
        from app.config import get_bash_foreground_timeout_ms

        with _mock_config({"mission_timeout": 3600,
                           "bash_foreground_timeout": 600}):
            assert get_bash_foreground_timeout_ms() == 600_000

    def test_clamped_below_mission_timeout(self):
        from app.config import get_bash_foreground_timeout_ms

        # Requested 3600s must clamp strictly under mission_timeout 600s.
        with _mock_config({"mission_timeout": 600,
                           "bash_foreground_timeout": 3600}):
            ms = get_bash_foreground_timeout_ms()
            assert ms > 0
            assert ms < 600 * 1000

    def test_zero_disables(self):
        from app.config import get_bash_foreground_timeout_ms

        with _mock_config({"bash_foreground_timeout": 0}):
            assert get_bash_foreground_timeout_ms() == 0

    def test_mission_timeout_zero_is_unbounded(self):
        """mission_timeout: 0 disables the watchdog — the Bash foreground
        timeout must honor the requested value as-is, not clamp to 60s."""
        from app.config import get_bash_foreground_timeout_ms

        with _mock_config({"mission_timeout": 0,
                           "bash_foreground_timeout": 900}):
            assert get_bash_foreground_timeout_ms() == 900_000


# --- get_post_mission_timeout ---


class TestGetPostMissionTimeout:
    def test_default(self):
        from app.config import get_post_mission_timeout

        with _mock_config({}):
            assert get_post_mission_timeout() == 300

    def test_custom(self):
        from app.config import get_post_mission_timeout

        with _mock_config({"post_mission_timeout": 600}):
            assert get_post_mission_timeout() == 600

    def test_string_parsed(self):
        from app.config import get_post_mission_timeout

        with _mock_config({"post_mission_timeout": "120"}):
            assert get_post_mission_timeout() == 120

    def test_invalid_returns_default(self):
        from app.config import get_post_mission_timeout

        with _mock_config({"post_mission_timeout": "nope"}):
            assert get_post_mission_timeout() == 300


# --- build_claude_flags ---


class TestBuildClaudeFlags:
    def test_empty_returns_empty(self):
        from app.config import build_claude_flags

        with patch("app.cli_provider.build_cli_flags", return_value=[]):
            result = build_claude_flags()
        assert result == []

    def test_with_model(self):
        from app.config import build_claude_flags

        with patch("app.cli_provider.build_cli_flags", return_value=["--model", "opus"]) as mock:
            result = build_claude_flags(model="opus")
        mock.assert_called_once_with(model="opus", fallback="", disallowed_tools=None)
        assert result == ["--model", "opus"]


# --- get_auto_merge_config ---


class TestGetAutoMergeConfig:
    def test_defaults(self):
        from app.config import get_auto_merge_config

        config = {}
        result = get_auto_merge_config(config, "myproject")
        assert result["enabled"] is True
        assert result["base_branch"] == "main"
        assert result["strategy"] == "squash"
        assert result["rules"] == []

    def test_global_config(self):
        from app.config import get_auto_merge_config

        config = {"git_auto_merge": {"enabled": False, "strategy": "rebase"}}
        result = get_auto_merge_config(config, "myproject")
        assert result["enabled"] is False
        assert result["strategy"] == "rebase"

    def test_config_yaml_projects_section_ignored(self):
        """config.yaml projects: section is no longer used for per-project overrides.

        Per-project auto-merge config is now exclusively in projects.yaml.
        """
        from app.config import get_auto_merge_config

        config = {
            "git_auto_merge": {"enabled": True, "strategy": "squash"},
            "projects": {"myproject": {"git_auto_merge": {"strategy": "merge"}}},
        }
        result = get_auto_merge_config(config, "myproject")
        assert result["enabled"] is True
        # Should use global config, not the projects section override
        assert result["strategy"] == "squash"


# --- _safe_int ---


class TestSafeInt:
    def test_int_value(self):
        from app.config import _safe_int
        assert _safe_int(42, 0) == 42

    def test_string_int_value(self):
        from app.config import _safe_int
        assert _safe_int("30", 0) == 30

    def test_invalid_string_returns_default(self):
        from app.config import _safe_int
        assert _safe_int("abc", 20) == 20

    def test_none_returns_default(self):
        from app.config import _safe_int
        assert _safe_int(None, 10) == 10

    def test_float_string_returns_default(self):
        from app.config import _safe_int
        assert _safe_int("3.14", 5) == 5

    def test_empty_string_returns_default(self):
        from app.config import _safe_int
        assert _safe_int("", 7) == 7


class TestGetMaxRunsInvalidConfig:
    def test_invalid_string_returns_default(self):
        from app.config import get_max_runs
        with _mock_config({"max_runs_per_day": "not_a_number"}):
            assert get_max_runs() == 60

    def test_none_returns_default(self):
        from app.config import get_max_runs
        with _mock_config({"max_runs_per_day": None}):
            assert get_max_runs() == 60


class TestGetIntervalSecondsInvalidConfig:
    def test_invalid_string_returns_default(self):
        from app.config import get_interval_seconds
        with _mock_config({"interval_seconds": "slow"}):
            assert get_interval_seconds() == 300


class TestGetContemplativeChanceInvalidConfig:
    def test_invalid_string_returns_default(self):
        from app.config import get_contemplative_chance
        with _mock_config({"contemplative_chance": "high"}):
            assert get_contemplative_chance() == 10


# --- get_claude_flags_for_role ---


class TestGetClaudeFlagsForRole:
    # The role provider is resolved via get_provider_for_role (cli: section);
    # patch that and inspect the resolved provider's build_extra_flags call.
    def test_mission_role(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "haiku", "lightweight": "haiku",
                 "fallback": "opus", "review_mode": "",
             }), \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = ["--model", "sonnet", "--fallback", "opus"]
            result = get_claude_flags_for_role("mission")
            mock_prov.return_value.build_extra_flags.assert_called_once_with(
                model="sonnet", fallback="opus", disallowed_tools=None
            )
            assert result == "--model sonnet --fallback opus"

    def test_mission_review_mode(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "haiku", "lightweight": "haiku",
                 "fallback": "opus", "review_mode": "haiku",
             }), \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = []
            get_claude_flags_for_role("mission", autonomous_mode="review")
            call_kwargs = mock_prov.return_value.build_extra_flags.call_args[1]
            assert call_kwargs["model"] == "haiku"
            assert call_kwargs["disallowed_tools"] == ["Bash", "Edit", "Write"]

    def test_contemplative_role(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "haiku", "lightweight": "haiku",
                 "fallback": "opus", "review_mode": "",
             }), \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = ["--model", "haiku"]
            get_claude_flags_for_role("contemplative")
            call_kwargs = mock_prov.return_value.build_extra_flags.call_args[1]
            assert call_kwargs["model"] == "haiku"
            assert call_kwargs["fallback"] == ""

    def test_chat_role(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "opus", "lightweight": "haiku",
                 "fallback": "sonnet", "review_mode": "",
             }), \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = []
            get_claude_flags_for_role("chat")
            call_kwargs = mock_prov.return_value.build_extra_flags.call_args[1]
            assert call_kwargs["model"] == "opus"
            assert call_kwargs["fallback"] == "sonnet"
            assert call_kwargs["disallowed_tools"] is None

    def test_unknown_role_passes_empty(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "haiku", "lightweight": "haiku",
                 "fallback": "opus", "review_mode": "",
             }), \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = []
            result = get_claude_flags_for_role("lightweight")
            call_kwargs = mock_prov.return_value.build_extra_flags.call_args[1]
            # "lightweight" has no explicit branch — model stays ""
            assert call_kwargs["model"] == ""
            assert result == ""

    def test_project_name_passed_to_model_config(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "haiku", "lightweight": "haiku",
                 "fallback": "", "review_mode": "",
             }) as mock_models, \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = []
            get_claude_flags_for_role("mission", project_name="myapp")
            # project_name is forwarded as the first positional arg (the
            # role_providers kwarg is an implementation detail).
            assert mock_models.call_args.args[0] == "myapp"

    def test_mission_review_mode_empty_uses_mission_model(self):
        from app.config import get_claude_flags_for_role
        with _mock_config({}), \
             patch("app.config.get_model_config", return_value={
                 "mission": "sonnet", "chat": "haiku", "lightweight": "haiku",
                 "fallback": "opus", "review_mode": "",
             }), \
             patch("app.cli_provider.get_provider_for_role") as mock_prov:
            mock_prov.return_value.build_extra_flags.return_value = []
            get_claude_flags_for_role("mission", autonomous_mode="review")
            call_kwargs = mock_prov.return_value.build_extra_flags.call_args[1]
            # review_mode="" means keep mission model
            assert call_kwargs["model"] == "sonnet"


# --- private_review_gate ---


class TestPrivateReviewGateConfig:
    def test_defaults_disabled(self):
        # Opt-in during the testing phase: the gate is off unless enabled.
        from app.config import get_private_review_gate_config

        with _mock_config({}), \
             patch("app.config._load_project_overrides", return_value={}):
            result = get_private_review_gate_config("app", skill_origin="rebase")

        assert result == {
            "enabled": False,
            "max_rounds": 3,
            "min_severity": "warning",
            "enabled_skills": ["fix", "implement", "rebase"],
            "budget_aware": True,
            "dedup": True,
            "tracker_max_age_days": 30,
        }

    def test_global_config_overrides_defaults(self):
        from app.config import get_private_review_gate_config

        with _mock_config({
            "private_review_gate": {
                "enabled": False,
                "max_rounds": "5",
                "min_severity": "critical",
                "enabled_skills": ["fix"],
            }
        }), patch("app.config._load_project_overrides", return_value={}):
            result = get_private_review_gate_config("app", skill_origin="fix")

        assert result["enabled"] is False
        assert result["max_rounds"] == 5
        assert result["min_severity"] == "critical"
        assert result["enabled_skills"] == ["fix"]

    def test_project_override_wins(self):
        from app.config import get_private_review_gate_config

        project_overrides = {
            "private_review_gate": {
                "enabled": "false",
                "max_rounds": 1,
                "min_severity": "important",
                "enabled_skills": "implement,rebase",
            }
        }
        with _mock_config({
            "private_review_gate": {
                "enabled": True,
                "max_rounds": 3,
                "min_severity": "critical",
            }
        }), patch("app.config._load_project_overrides",
                  return_value=project_overrides):
            result = get_private_review_gate_config("app", skill_origin="rebase")

        assert result == {
            "enabled": False,
            "max_rounds": 1,
            "min_severity": "warning",
            "enabled_skills": ["implement", "rebase"],
            "budget_aware": True,
            "dedup": True,
            "tracker_max_age_days": 30,
        }

    def test_malformed_values_fall_back(self):
        from app.config import get_private_review_gate_config

        with _mock_config({
            "private_review_gate": {
                "enabled": "maybe",
                "max_rounds": "bad",
                "min_severity": "unknown",
                "enabled_skills": 123,
            }
        }), patch("app.config._load_project_overrides", return_value={}):
            result = get_private_review_gate_config("app", skill_origin="fix")

        assert result == {
            "enabled": False,
            "max_rounds": 3,
            "min_severity": "warning",
            "enabled_skills": ["fix", "implement", "rebase"],
            "budget_aware": True,
            "dedup": True,
            "tracker_max_age_days": 30,
        }

    def test_legacy_key_is_ignored(self):
        # The pre-release implementation_review_gate key is no longer read;
        # only private_review_gate is honored.
        from app.config import get_private_review_gate_config

        with _mock_config({
            "implementation_review_gate": {
                "enabled": False,
                "max_rounds": 1,
                "enabled_skills": ["fix"],
            }
        }), patch("app.config._load_project_overrides", return_value={}):
            result = get_private_review_gate_config("app", skill_origin="rebase")

        assert result == {
            "enabled": False,
            "max_rounds": 3,
            "min_severity": "warning",
            "enabled_skills": ["fix", "implement", "rebase"],
            "budget_aware": True,
            "dedup": True,
            "tracker_max_age_days": 30,
        }

    def test_new_subsystem_flags_parsed(self):
        from app.config import get_private_review_gate_config

        with _mock_config({
            "private_review_gate": {
                "enabled": True,
                "budget_aware": "off",
                "dedup": False,
                "tracker_max_age_days": "7",
            }
        }), patch("app.config._load_project_overrides", return_value={}):
            result = get_private_review_gate_config("app", skill_origin="fix")

        assert result["budget_aware"] is False
        assert result["dedup"] is False
        assert result["tracker_max_age_days"] == 7


# --- review_memory ---


class TestReviewMemoryConfig:
    def test_disabled_by_default(self):
        from app.config import get_review_memory_config

        with _mock_config({}):
            result = get_review_memory_config()

        assert result == {"enabled": False, "max_entries": 8}

    def test_enabled_with_custom_max_entries(self):
        from app.config import get_review_memory_config

        with _mock_config({
            "review_memory": {"enabled": True, "max_entries": "5"}
        }):
            result = get_review_memory_config()

        assert result == {"enabled": True, "max_entries": 5}

    def test_malformed_values_fall_back(self):
        from app.config import get_review_memory_config

        with _mock_config({
            "review_memory": {"enabled": "maybe", "max_entries": "lots"}
        }):
            result = get_review_memory_config()

        assert result == {"enabled": False, "max_entries": 8}

    def test_negative_max_entries_clamped(self):
        from app.config import get_review_memory_config

        with _mock_config({"review_memory": {"max_entries": -3}}):
            result = get_review_memory_config()

        assert result["max_entries"] == 0


class TestReviewContextConfig:
    def test_defaults_fall_back_to_rebase_flag(self):
        from app.config import get_review_context_config

        # No review_context key and no rebase flag -> rebase default (True).
        with _mock_config({}):
            result = get_review_context_config()

        assert result == {"include_bot_feedback": True, "prior_review_max_chars": 10000}

    def test_include_bot_feedback_inherits_rebase_flag(self):
        from app.config import get_review_context_config

        with _mock_config({"rebase_include_bot_feedback": False}):
            result = get_review_context_config()

        assert result["include_bot_feedback"] is False

    def test_explicit_review_context_overrides_rebase_flag(self):
        from app.config import get_review_context_config

        with _mock_config({
            "rebase_include_bot_feedback": False,
            "review_context": {"include_bot_feedback": True},
        }):
            result = get_review_context_config()

        assert result["include_bot_feedback"] is True

    def test_custom_and_clamped_max_chars(self):
        from app.config import get_review_context_config

        with _mock_config({"review_context": {"prior_review_max_chars": "500"}}):
            assert get_review_context_config()["prior_review_max_chars"] == 500

        with _mock_config({"review_context": {"prior_review_max_chars": -10}}):
            assert get_review_context_config()["prior_review_max_chars"] == 0

    def test_non_dict_review_context_safe_defaults(self):
        from app.config import get_review_context_config

        with _mock_config({"review_context": "garbage"}):
            result = get_review_context_config()

        assert result == {"include_bot_feedback": True, "prior_review_max_chars": 10000}


# --- backward compatibility ---


class TestDashboardConfig:
    """Tests for dashboard config getters."""

    def test_dashboard_disabled_by_default(self):
        from app.config import is_dashboard_enabled
        with _mock_config({}):
            assert not is_dashboard_enabled()

    def test_dashboard_enabled(self):
        from app.config import is_dashboard_enabled
        with _mock_config({"dashboard": {"enabled": True}}):
            assert is_dashboard_enabled()

    def test_dashboard_disabled_explicitly(self):
        from app.config import is_dashboard_enabled
        with _mock_config({"dashboard": {"enabled": False}}):
            assert not is_dashboard_enabled()

    def test_dashboard_non_dict_value(self):
        from app.config import is_dashboard_enabled
        with _mock_config({"dashboard": "yes"}):
            assert not is_dashboard_enabled()

    def test_dashboard_port_default(self):
        from app.config import get_dashboard_port
        with _mock_config({}):
            assert get_dashboard_port() == 5001

    def test_dashboard_port_custom(self):
        from app.config import get_dashboard_port
        with _mock_config({"dashboard": {"port": 8080}}):
            assert get_dashboard_port() == 8080


# --- get_mcp_configs ---


class TestGetMcpConfigs:
    def test_default_empty(self):
        from app.config import get_mcp_configs

        with _mock_config({}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_configs() == []

    def test_global_list(self):
        from app.config import get_mcp_configs

        with _mock_config({"mcp": ["/path/to/mcp.json"]}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_configs() == ["/path/to/mcp.json"]

    def test_global_multiple(self):
        from app.config import get_mcp_configs

        configs = ["/path/a.json", "/path/b.json"]
        with _mock_config({"mcp": configs}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_configs() == configs

    def test_non_list_returns_empty(self):
        from app.config import get_mcp_configs

        with _mock_config({"mcp": "not-a-list"}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_configs() == []

    def test_filters_non_string_entries(self):
        from app.config import get_mcp_configs

        with _mock_config({"mcp": ["/valid.json", 42, "", None]}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_configs() == ["/valid.json"]

    def test_project_override_replaces_global(self):
        from app.config import get_mcp_configs

        with _mock_config({"mcp": ["/global.json"]}):
            with patch(
                "app.config._load_project_overrides",
                return_value={"mcp": ["/project.json"]},
            ):
                assert get_mcp_configs("myproject") == ["/project.json"]

    def test_project_override_absent_uses_global(self):
        from app.config import get_mcp_configs

        with _mock_config({"mcp": ["/global.json"]}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_configs("myproject") == ["/global.json"]

    def test_project_override_empty_list_clears_global(self):
        from app.config import get_mcp_configs

        with _mock_config({"mcp": ["/global.json"]}):
            with patch(
                "app.config._load_project_overrides",
                return_value={"mcp": []},
            ):
                assert get_mcp_configs("myproject") == []


class TestBackwardCompat:
    """Verify that importing from app.utils still works."""

    def test_config_functions_accessible_from_utils(self):
        from app.utils import get_chat_tools, get_model_config, get_branch_prefix
        # Just verify they're importable (not None)
        assert callable(get_chat_tools)
        assert callable(get_model_config)
        assert callable(get_branch_prefix)


# --- get_effort_for_mode ---


class TestGetEffortForMode:
    def test_defaults_no_config(self):
        from app.config import get_effort_for_mode
        with _mock_config({}):
            assert get_effort_for_mode("review") == "low"
            assert get_effort_for_mode("implement") == ""
            assert get_effort_for_mode("deep") == "high"
            assert get_effort_for_mode("wait") == ""

    def test_string_config_applies_to_all_modes(self):
        from app.config import get_effort_for_mode
        with _mock_config({"effort": "max"}):
            assert get_effort_for_mode("review") == "max"
            assert get_effort_for_mode("implement") == "max"
            assert get_effort_for_mode("deep") == "max"

    def test_dict_config_per_mode(self):
        from app.config import get_effort_for_mode
        with _mock_config({"effort": {"review": "low", "deep": "max"}}):
            assert get_effort_for_mode("review") == "low"
            assert get_effort_for_mode("deep") == "max"
            # Missing mode falls back to default
            assert get_effort_for_mode("implement") == ""

    def test_empty_string_disables(self):
        from app.config import get_effort_for_mode
        with _mock_config({"effort": ""}):
            assert get_effort_for_mode("deep") == ""

    def test_invalid_string_returns_empty(self):
        from app.config import get_effort_for_mode
        with _mock_config({"effort": "turbo"}):
            assert get_effort_for_mode("deep") == ""

    def test_invalid_dict_value_falls_back(self):
        from app.config import get_effort_for_mode
        with _mock_config({"effort": {"deep": "turbo"}}):
            # Invalid value in dict falls back to default
            assert get_effort_for_mode("deep") == "high"


# --- get_effort (per-mission-type) ---


class TestGetEffortMissionType:
    def test_mission_type_overrides_dynamic_default(self):
        """A pinned mission type wins over the budget-mode dynamic default.

        A /review mission running in deep mode would normally get "high";
        with effort.review pinned it stays "low" regardless of mode.
        """
        from app.config import get_effort
        with _mock_config({"effort": {"review": "low"}}):
            assert get_effort("deep", mission_type="review") == "low"
            assert get_effort("implement", mission_type="review") == "low"

    def test_mission_type_absent_falls_back_to_dynamic_default(self):
        from app.config import get_effort
        with _mock_config({"effort": {"review": "low"}}):
            # "plan" not pinned → dynamic default by mode
            assert get_effort("deep", mission_type="plan") == "high"
            assert get_effort("review", mission_type="plan") == "low"

    def test_no_config_preserves_dynamic_default(self):
        from app.config import get_effort
        with _mock_config({}):
            assert get_effort("deep", mission_type="review") == "high"
            assert get_effort("review", mission_type="plan") == "low"
            assert get_effort("implement", mission_type="implement") == ""

    def test_no_mission_type_resolves_per_mode(self):
        """Without a mission type, resolution is the per-mode config value.

        Asserts hardcoded expected values (not equality with the wrapper, which
        would be trivially true since get_effort_for_mode just calls get_effort
        with mission_type="").
        """
        from app.config import get_effort
        with _mock_config({"effort": {"review": "low", "deep": "max"}}):
            assert get_effort("review") == "low"   # pinned
            assert get_effort("deep") == "max"     # pinned
            # Unlisted modes whose dynamic default is "" stay "".
            assert get_effort("implement") == ""
            assert get_effort("wait") == ""

    def test_partial_dict_unlisted_mode_uses_dynamic_default(self):
        """A partial dict leaves unlisted modes on the DYNAMIC default.

        Pins behavior flagged in review: with only `implement` listed, an
        unlisted mode whose dynamic default is non-empty (deep→high, review→low)
        resolves to that default rather than being disabled. This is the
        intended semantics ("dynamic default preserved unless config pins a
        value"); the test locks it so the fall-through can't regress silently.
        """
        from app.config import get_effort
        with _mock_config({"effort": {"implement": "high"}}):
            assert get_effort("implement") == "high"   # pinned
            assert get_effort("deep") == "high"        # dynamic default
            assert get_effort("review") == "low"       # dynamic default
            assert get_effort("wait") == ""            # dynamic default (none)

    def test_mission_type_disables_with_empty_string(self):
        from app.config import get_effort
        with _mock_config({"effort": {"review": ""}}):
            # Explicit "" disables the flag for this mission type
            assert get_effort("deep", mission_type="review") == ""

    def test_mission_type_takes_precedence_over_mode_key(self):
        """When both a mission-type and a mode key could match, the type wins."""
        from app.config import get_effort
        with _mock_config({"effort": {"review": "low", "deep": "max"}}):
            # review mission in deep mode: type "review" wins over mode "deep"
            assert get_effort("deep", mission_type="review") == "low"

    def test_invalid_mission_type_value_falls_back(self):
        from app.config import get_effort
        with _mock_config({"effort": {"plan": "turbo"}}):
            # Invalid value → ignore pin, use dynamic default
            assert get_effort("deep", mission_type="plan") == "high"

    def test_string_config_ignores_mission_type(self):
        from app.config import get_effort
        with _mock_config({"effort": "high"}):
            assert get_effort("deep", mission_type="review") == "high"
            assert get_effort("implement", mission_type="plan") == "high"


# --- get_thinking_config / should_enable_thinking ---


class TestThinkingConfig:
    def test_defaults_no_config(self):
        from app.config import get_thinking_config
        with _mock_config({}):
            cfg = get_thinking_config()
            assert cfg["enabled"] is False
            assert cfg["budget_tokens"] == 0
            assert cfg["min_mode"] == "deep"

    def test_enabled_with_defaults(self):
        from app.config import get_thinking_config
        with _mock_config({"thinking": {"enabled": True}}):
            cfg = get_thinking_config()
            assert cfg["enabled"] is True
            assert cfg["budget_tokens"] == 0
            assert cfg["min_mode"] == "deep"

    def test_full_config(self):
        from app.config import get_thinking_config
        with _mock_config({"thinking": {"enabled": True, "budget_tokens": 10000, "min_mode": "implement"}}):
            cfg = get_thinking_config()
            assert cfg["enabled"] is True
            assert cfg["budget_tokens"] == 10000
            assert cfg["min_mode"] == "implement"

    def test_non_dict_thinking_returns_defaults(self):
        from app.config import get_thinking_config
        with _mock_config({"thinking": "yes"}):
            cfg = get_thinking_config()
            assert cfg["enabled"] is False

    def test_should_enable_thinking_disabled(self):
        from app.config import should_enable_thinking
        with _mock_config({"thinking": {"enabled": False}}):
            assert should_enable_thinking("deep", tier="critical") is False

    def test_should_enable_thinking_requires_critical_tier(self):
        """Thinking only activates for 'critical' tier missions."""
        from app.config import should_enable_thinking
        with _mock_config({"thinking": {"enabled": True, "min_mode": "deep"}}):
            assert should_enable_thinking("deep", tier="critical") is True
            assert should_enable_thinking("deep", tier="complex") is False
            assert should_enable_thinking("deep", tier="medium") is False
            assert should_enable_thinking("deep", tier="") is False

    def test_should_enable_thinking_deep_mode(self):
        from app.config import should_enable_thinking
        with _mock_config({"thinking": {"enabled": True, "min_mode": "deep"}}):
            assert should_enable_thinking("deep", tier="critical") is True
            assert should_enable_thinking("implement", tier="critical") is False
            assert should_enable_thinking("review", tier="critical") is False

    def test_should_enable_thinking_implement_mode(self):
        from app.config import should_enable_thinking
        with _mock_config({"thinking": {"enabled": True, "min_mode": "implement"}}):
            assert should_enable_thinking("deep", tier="critical") is True
            assert should_enable_thinking("implement", tier="critical") is True
            assert should_enable_thinking("review", tier="critical") is False

    def test_should_enable_thinking_no_config(self):
        from app.config import should_enable_thinking
        with _mock_config({}):
            assert should_enable_thinking("deep", tier="critical") is False

    def test_should_enable_thinking_unknown_mode(self):
        from app.config import should_enable_thinking
        with _mock_config({"thinking": {"enabled": True, "min_mode": "deep"}}):
            assert should_enable_thinking("unknown", tier="critical") is False


class TestCiCheckConfig:
    """Tests for ci_check config getter."""

    def test_enabled_by_default(self):
        from app.config import is_ci_check_enabled
        with _mock_config({}):
            assert is_ci_check_enabled() is True

    def test_enabled_explicitly(self):
        from app.config import is_ci_check_enabled
        with _mock_config({"ci_check": {"enabled": True}}):
            assert is_ci_check_enabled() is True

    def test_disabled(self):
        from app.config import is_ci_check_enabled
        with _mock_config({"ci_check": {"enabled": False}}):
            assert is_ci_check_enabled() is False

    def test_bare_false(self):
        from app.config import is_ci_check_enabled
        with _mock_config({"ci_check": False}):
            assert is_ci_check_enabled() is False

    def test_bare_true(self):
        from app.config import is_ci_check_enabled
        with _mock_config({"ci_check": True}):
            assert is_ci_check_enabled() is True

    def test_non_dict_string(self):
        from app.config import is_ci_check_enabled
        with _mock_config({"ci_check": "yes"}):
            assert is_ci_check_enabled() is True

    def test_non_dict_string_warns(self, capsys):
        from app.config import is_ci_check_enabled
        with _mock_config({"ci_check": "yes"}):
            is_ci_check_enabled()
        assert "unexpected type" in capsys.readouterr().err


class TestRunningIndicatorConfig:
    """Tests for get_running_indicator_config()."""

    def test_defaults_on(self):
        from app.config import get_running_indicator_config
        with _mock_config({}):
            cfg = get_running_indicator_config()
        assert cfg == {
            "enabled": True,
            "commit_status": True,
            "issue_label": True,
            "label_name": "koan:working",
        }

    def test_explicit_block_overrides(self):
        from app.config import get_running_indicator_config
        with _mock_config(
            {"running_indicator": {"enabled": False, "commit_status": False}}
        ):
            cfg = get_running_indicator_config()
        assert cfg["enabled"] is False
        assert cfg["commit_status"] is False
        assert cfg["issue_label"] is True
        assert cfg["label_name"] == "koan:working"

    def test_custom_label_name(self):
        from app.config import get_running_indicator_config
        with _mock_config({"running_indicator": {"label_name": "wip"}}):
            cfg = get_running_indicator_config()
        assert cfg["label_name"] == "wip"

    def test_bare_bool(self):
        from app.config import get_running_indicator_config
        with _mock_config({"running_indicator": False}):
            assert get_running_indicator_config()["enabled"] is False

    def test_non_dict_non_bool_uses_defaults(self):
        from app.config import get_running_indicator_config
        with _mock_config({"running_indicator": "yes"}):
            cfg = get_running_indicator_config()
        assert cfg["enabled"] is True


# --- get_review_verdict_config ---


class TestGetReviewVerdictConfig:
    def test_defaults_when_missing(self):
        from app.config import get_review_verdict_config
        with _mock_config({}):
            cfg = get_review_verdict_config()
        assert cfg == {"approved": True, "body_enabled": True, "include_blockers": True}

    def test_approved_disabled(self):
        from app.config import get_review_verdict_config
        with _mock_config({"review_verdict": {"approved": False}}):
            cfg = get_review_verdict_config()
        assert cfg["approved"] is False
        assert cfg["body_enabled"] is True

    def test_body_disabled(self):
        from app.config import get_review_verdict_config
        with _mock_config({"review_verdict": {"body_enabled": False}}):
            cfg = get_review_verdict_config()
        assert cfg["body_enabled"] is False
        assert cfg["include_blockers"] is True

    def test_blockers_disabled(self):
        from app.config import get_review_verdict_config
        with _mock_config({"review_verdict": {"include_blockers": False}}):
            cfg = get_review_verdict_config()
        assert cfg["body_enabled"] is True
        assert cfg["include_blockers"] is False

    def test_non_dict_fails_closed(self):
        from app.config import get_review_verdict_config
        with _mock_config({"review_verdict": "garbage"}):
            cfg = get_review_verdict_config()
        assert cfg["approved"] is False
        assert cfg["body_enabled"] is True

    def test_non_bool_value_fails_closed(self):
        from app.config import get_review_verdict_config
        with _mock_config({"review_verdict": {"approved": "yes"}}):
            cfg = get_review_verdict_config()
        assert cfg["approved"] is False


# --- get_review_history_config ---


class TestGetReviewHistoryConfig:
    def test_defaults_to_collapse(self):
        """Default behavior preserves history: collapse the prior review."""
        from app.config import get_review_history_config
        with _mock_config({}):
            cfg = get_review_history_config()
        assert cfg == {"preserve_previous": False}

    def test_preserve_previous_opt_in(self):
        from app.config import get_review_history_config
        with _mock_config({"review_history": {"preserve_previous": True}}):
            cfg = get_review_history_config()
        assert cfg["preserve_previous"] is True

    def test_explicit_false(self):
        from app.config import get_review_history_config
        with _mock_config({"review_history": {"preserve_previous": False}}):
            cfg = get_review_history_config()
        assert cfg["preserve_previous"] is False

    def test_non_dict_fails_closed(self):
        from app.config import get_review_history_config
        with _mock_config({"review_history": "garbage"}):
            cfg = get_review_history_config()
        assert cfg["preserve_previous"] is False

    def test_non_bool_value_fails_closed(self):
        from app.config import get_review_history_config
        with _mock_config({"review_history": {"preserve_previous": "yes"}}):
            cfg = get_review_history_config()
        assert cfg["preserve_previous"] is False


class TestReviewInlineCommentsConfig:
    def test_disabled_by_default(self):
        from app.config import get_review_inline_comments_config
        with _mock_config({}):
            cfg = get_review_inline_comments_config()
        assert cfg["enabled"] is False
        assert cfg["max_comments"] == 25

    def test_opt_in(self):
        from app.config import get_review_inline_comments_config
        raw = {"review_inline_comments": {"enabled": True, "max_comments": 5}}
        with _mock_config(raw):
            cfg = get_review_inline_comments_config()
        assert cfg["enabled"] is True
        assert cfg["max_comments"] == 5

    def test_malformed_section_disabled(self):
        from app.config import get_review_inline_comments_config
        with _mock_config({"review_inline_comments": "nonsense"}):
            cfg = get_review_inline_comments_config()
        assert cfg["enabled"] is False
        assert cfg["max_comments"] == 25

    def test_negative_max_comments_falls_back(self):
        from app.config import get_review_inline_comments_config
        with _mock_config({"review_inline_comments": {"enabled": True, "max_comments": -3}}):
            cfg = get_review_inline_comments_config()
        assert cfg["max_comments"] == 25


class TestReviewDraftSkipConfig:
    def test_disabled_by_default(self):
        from app.config import get_review_draft_skip_config
        with _mock_config({}):
            cfg = get_review_draft_skip_config()
        assert cfg["enabled"] is False

    def test_opt_in(self):
        from app.config import get_review_draft_skip_config
        with _mock_config({"review_draft_skip": {"enabled": True}}):
            cfg = get_review_draft_skip_config()
        assert cfg["enabled"] is True

    def test_malformed_section_disabled(self):
        from app.config import get_review_draft_skip_config
        with _mock_config({"review_draft_skip": "nonsense"}):
            cfg = get_review_draft_skip_config()
        assert cfg["enabled"] is False

    def test_non_bool_enabled_disabled(self):
        from app.config import get_review_draft_skip_config
        with _mock_config({"review_draft_skip": {"enabled": "yes"}}):
            cfg = get_review_draft_skip_config()
        assert cfg["enabled"] is False


class TestReviewPauseLabelConfig:
    def test_default_is_pause_review(self):
        from app.config import get_review_pause_label
        with _mock_config({}):
            assert get_review_pause_label() == "PauseReview"

    def test_custom_label(self):
        from app.config import get_review_pause_label
        with _mock_config({"review_pause_label": "AI:Paused"}):
            assert get_review_pause_label() == "AI:Paused"

    def test_empty_string_disables(self):
        from app.config import get_review_pause_label
        with _mock_config({"review_pause_label": ""}):
            assert get_review_pause_label() == ""

    def test_whitespace_only_disables(self):
        from app.config import get_review_pause_label
        with _mock_config({"review_pause_label": "   "}):
            assert get_review_pause_label() == ""

    def test_non_string_disables(self):
        from app.config import get_review_pause_label
        with _mock_config({"review_pause_label": 123}):
            assert get_review_pause_label() == ""

    def test_strips_surrounding_whitespace(self):
        from app.config import get_review_pause_label
        with _mock_config({"review_pause_label": "  Skip AI  "}):
            assert get_review_pause_label() == "Skip AI"


class TestMemoryMonitorConfig:
    def test_defaults(self):
        from app.config import get_memory_monitor_config
        with _mock_config({}):
            conf = get_memory_monitor_config()
        assert conf["enabled"] is False
        assert conf["threshold_mb"] == 1200
        assert conf["sustained_samples"] == 3
        assert conf["tracemalloc"] is False
        assert conf["min_runs_before_restart"] == 1

    def test_overrides(self):
        from app.config import get_memory_monitor_config
        raw = {
            "memory_monitor": {
                "enabled": True,
                "threshold_mb": "900",
                "sustained_samples": "5",
                "tracemalloc": True,
                "min_runs_before_restart": "2",
            }
        }
        with _mock_config(raw):
            conf = get_memory_monitor_config()
        assert conf["enabled"] is True
        assert conf["threshold_mb"] == 900
        assert conf["sustained_samples"] == 5
        assert conf["tracemalloc"] is True
        assert conf["min_runs_before_restart"] == 2

    def test_malformed_section_disabled(self):
        from app.config import get_memory_monitor_config
        with _mock_config({"memory_monitor": "nonsense"}):
            conf = get_memory_monitor_config()
        assert conf["enabled"] is False
        assert conf["threshold_mb"] == 1200


class TestReviewCompressorBudget:
    def test_token_budget_default(self):
        from app.config import get_review_compressor_token_budget
        with _mock_config({}):
            assert get_review_compressor_token_budget() == 80_000

    def test_token_budget_override(self):
        from app.config import get_review_compressor_token_budget
        with _mock_config(
            {"optimizations": {"review_compressor": {"token_budget": 120_000}}}
        ):
            assert get_review_compressor_token_budget() == 120_000

    def test_token_budget_malformed_falls_back(self):
        from app.config import get_review_compressor_token_budget
        with _mock_config(
            {"optimizations": {"review_compressor": {"token_budget": "huge"}}}
        ):
            assert get_review_compressor_token_budget() == 80_000

    def test_token_budget_bool_falls_back(self):
        from app.config import get_review_compressor_token_budget
        with _mock_config(
            {"optimizations": {"review_compressor": {"token_budget": True}}}
        ):
            assert get_review_compressor_token_budget() == 80_000

    def test_max_diff_chars_derived_from_budget(self):
        from app.config import get_review_max_diff_chars
        with _mock_config(
            {"optimizations": {"review_compressor": {"token_budget": 80_000}}}
        ):
            # 80_000 tokens * 3.5 chars/token * 4 headroom
            assert get_review_max_diff_chars() == 1_120_000

    def test_max_diff_chars_scales_with_budget(self):
        from app.config import get_review_max_diff_chars
        with _mock_config(
            {"optimizations": {"review_compressor": {"token_budget": 40_000}}}
        ):
            assert get_review_max_diff_chars() == 560_000


class TestInstanceSyncInterval:
    """KOAN_INSTANCE_SYNC_INTERVAL parsing."""

    def test_instance_sync_interval_default_disabled(self, monkeypatch):
        monkeypatch.delenv("KOAN_INSTANCE_SYNC_INTERVAL", raising=False)
        from app.config import get_instance_sync_interval
        assert get_instance_sync_interval() == 0

    def test_instance_sync_interval_from_env(self, monkeypatch):
        monkeypatch.setenv("KOAN_INSTANCE_SYNC_INTERVAL", "900")
        from app.config import get_instance_sync_interval
        assert get_instance_sync_interval() == 900

    def test_instance_sync_interval_malformed_disabled(self, monkeypatch):
        monkeypatch.setenv("KOAN_INSTANCE_SYNC_INTERVAL", "nope")
        from app.config import get_instance_sync_interval
        assert get_instance_sync_interval() == 0


# --- get_bridge_memory_monitor_config / get_conversation_compact_interval (#2354) ---

class TestBridgeMemoryConfig:
    def test_bridge_monitor_defaults(self):
        from app.config import get_bridge_memory_monitor_config
        with _mock_config({}):
            conf = get_bridge_memory_monitor_config()
        assert conf["enabled"] is True   # on by default (#2354)
        assert conf["threshold_mb"] == 600
        assert conf["sustained_samples"] == 3

    def test_bridge_monitor_opt_out(self):
        from app.config import get_bridge_memory_monitor_config
        with _mock_config({"memory_monitor": {"bridge": {"enabled": False}}}):
            conf = get_bridge_memory_monitor_config()
        assert conf["enabled"] is False

    def test_bridge_monitor_subblock_override(self):
        from app.config import get_bridge_memory_monitor_config
        with _mock_config({
            "memory_monitor": {
                "enabled": False,
                "bridge": {"enabled": True, "threshold_mb": 500, "sustained_samples": 5},
            }
        }):
            conf = get_bridge_memory_monitor_config()
        assert conf["enabled"] is True          # sub-block opts in independently
        assert conf["threshold_mb"] == 500
        assert conf["sustained_samples"] == 5

    def test_compact_interval_default(self):
        from app.config import get_conversation_compact_interval
        with _mock_config({}):
            assert get_conversation_compact_interval() == 3600

    def test_compact_interval_floored(self):
        from app.config import get_conversation_compact_interval
        with _mock_config({"conversation": {"compact_interval_seconds": 30}}):
            assert get_conversation_compact_interval() == 300

    def test_compact_interval_disabled(self):
        from app.config import get_conversation_compact_interval
        with _mock_config({"conversation": {"compact_interval_seconds": 0}}):
            assert get_conversation_compact_interval() == 0


class TestCleanupExtraTmpGlobs:
    def test_defaults(self):
        from app.config import get_cleanup_extra_tmp_globs
        with _mock_config({}):
            globs = get_cleanup_extra_tmp_globs()
        assert "/tmp/pytest-of-*" in globs
        assert "/tmp/test-koan*" in globs
        assert "/tmp/jest_rs" in globs

    def test_override(self):
        from app.config import get_cleanup_extra_tmp_globs
        with _mock_config({"cleanup": {"extra_tmp_globs": ["/tmp/foo-*"]}}):
            assert get_cleanup_extra_tmp_globs() == ["/tmp/foo-*"]

    def test_empty_list_disables(self):
        from app.config import get_cleanup_extra_tmp_globs
        with _mock_config({"cleanup": {"extra_tmp_globs": []}}):
            assert get_cleanup_extra_tmp_globs() == []

    def test_malformed_falls_back_to_defaults(self):
        from app.config import get_cleanup_extra_tmp_globs
        with _mock_config({"cleanup": {"extra_tmp_globs": "nope"}}):
            globs = get_cleanup_extra_tmp_globs()
        assert "/tmp/pytest-of-*" in globs


class TestCleanupMinTmpAgeSeconds:
    def test_default(self):
        from app.config import get_cleanup_min_tmp_age_seconds
        with _mock_config({}):
            assert get_cleanup_min_tmp_age_seconds() == 600.0

    def test_override(self):
        from app.config import get_cleanup_min_tmp_age_seconds
        with _mock_config({"cleanup": {"min_tmp_age_seconds": 120}}):
            assert get_cleanup_min_tmp_age_seconds() == 120.0

    def test_zero_disables_gate(self):
        from app.config import get_cleanup_min_tmp_age_seconds
        with _mock_config({"cleanup": {"min_tmp_age_seconds": 0}}):
            assert get_cleanup_min_tmp_age_seconds() == 0.0

    def test_malformed_falls_back_to_default(self):
        from app.config import get_cleanup_min_tmp_age_seconds
        with _mock_config({"cleanup": {"min_tmp_age_seconds": "nope"}}):
            assert get_cleanup_min_tmp_age_seconds() == 600.0

    def test_negative_clamped_to_zero(self):
        from app.config import get_cleanup_min_tmp_age_seconds
        with _mock_config({"cleanup": {"min_tmp_age_seconds": -5}}):
            assert get_cleanup_min_tmp_age_seconds() == 0.0


# --- get_mcp_roles / mcp_configs_for_role ---


class TestGetMcpRoles:
    def test_default_when_absent(self):
        from app.config import get_mcp_roles

        with patch("app.config._load_config", return_value={}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_roles() == ["mission", "contemplative", "plan"]

    def test_global_override(self):
        from app.config import get_mcp_roles

        with patch("app.config._load_config", return_value={"mcp_roles": ["mission"]}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_roles() == ["mission"]

    def test_empty_list_is_kill_switch(self):
        from app.config import get_mcp_roles

        with patch("app.config._load_config", return_value={"mcp_roles": []}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_roles() == []

    def test_malformed_falls_back_to_default(self):
        from app.config import get_mcp_roles

        with patch("app.config._load_config", return_value={"mcp_roles": "mission"}):
            with patch("app.config._load_project_overrides", return_value={}):
                assert get_mcp_roles() == ["mission", "contemplative", "plan"]

    def test_project_override_replaces_global(self):
        from app.config import get_mcp_roles

        with patch("app.config._load_config", return_value={"mcp_roles": ["mission"]}):
            with patch(
                "app.config._load_project_overrides",
                return_value={"mcp_roles": ["plan"]},
            ):
                assert get_mcp_roles("proj") == ["plan"]


class TestMcpConfigsForRole:
    def test_role_in_allowlist_returns_configs(self):
        from app.config import MCP_ROLE_PLAN, mcp_configs_for_role

        with patch("app.config.get_mcp_roles", return_value=[MCP_ROLE_PLAN]):
            with patch("app.config.get_mcp_configs", return_value=["/a.json"]):
                assert mcp_configs_for_role(MCP_ROLE_PLAN, "proj") == ["/a.json"]

    def test_role_not_in_allowlist_returns_none(self):
        from app.config import (
            MCP_ROLE_GITHUB_REPLY,
            MCP_ROLE_MISSION,
            mcp_configs_for_role,
        )

        with patch("app.config.get_mcp_roles", return_value=[MCP_ROLE_MISSION]):
            with patch("app.config.get_mcp_configs", return_value=["/a.json"]):
                assert mcp_configs_for_role(MCP_ROLE_GITHUB_REPLY, "proj") is None

    def test_allowlisted_but_no_configs_returns_none(self):
        from app.config import MCP_ROLE_PLAN, mcp_configs_for_role

        with patch("app.config.get_mcp_roles", return_value=[MCP_ROLE_PLAN]):
            with patch("app.config.get_mcp_configs", return_value=[]):
                assert mcp_configs_for_role(MCP_ROLE_PLAN, "proj") is None

    def test_mission_role_disabled_by_empty_mcp_roles(self):
        from app.config import MCP_ROLE_MISSION, mcp_configs_for_role

        with patch(
            "app.config._load_config",
            return_value={"mcp_roles": [], "mcp": ["/a.json"]},
        ):
            with patch("app.config._load_project_overrides", return_value={}):
                assert mcp_configs_for_role(MCP_ROLE_MISSION, "proj") is None


class TestGetPageCacheReclaimConfig:
    def test_page_cache_reclaim_defaults(self):
        from app.config import get_page_cache_reclaim_config
        with _mock_config({}):
            assert get_page_cache_reclaim_config() == {
                "enabled": True,
                "idle_interval_s": 180,
                "time_budget_s": 10,
                "extra_roots": [],
            }

    def test_page_cache_reclaim_overrides_and_bad_types(self):
        from app.config import get_page_cache_reclaim_config
        with _mock_config({
            "page_cache_reclaim": {
                "enabled": False,
                "idle_interval_s": "0",
                "time_budget_s": "not-an-int",
                "extra_roots": ["/data", 42],
            }
        }):
            cfg = get_page_cache_reclaim_config()
        assert cfg["enabled"] is False
        assert cfg["idle_interval_s"] == 0
        assert cfg["time_budget_s"] == 10          # bad → default
        assert cfg["extra_roots"] == ["/data", "42"]  # coerced to str

    def test_page_cache_reclaim_malformed_section(self):
        from app.config import get_page_cache_reclaim_config
        with _mock_config({"page_cache_reclaim": "nope"}):
            cfg = get_page_cache_reclaim_config()
        assert cfg["enabled"] is True
        assert cfg["extra_roots"] == []


# --- _get_config_with_overrides (shared helper, issue #2340) ---


class TestGetConfigWithOverrides:
    """The shared load->type-check->merge helper behind the get_*_config
    functions. Individual functions keep their own field-level coercion;
    this only covers the merge/resolution contract itself."""

    def test_defaults_when_section_absent(self):
        from app.config import _get_config_with_overrides
        with _mock_config({}):
            result = _get_config_with_overrides("widget", {"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_global_section_overrides_defaults(self):
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": {"a": 99}}):
            result = _get_config_with_overrides("widget", {"a": 1, "b": 2})
        assert result == {"a": 99, "b": 2}

    def test_non_dict_section_falls_back_to_defaults(self):
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": "nope"}):
            result = _get_config_with_overrides("widget", {"a": 1})
        assert result == {"a": 1}

    def test_bare_false_ignored_by_default(self):
        """Without bool_shortcut=True, a bare ``False`` section is just
        malformed (-> ``{}``) — matching the pre-refactor behavior of the
        many sections that never supported a ``key: false`` shorthand."""
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": False}):
            result = _get_config_with_overrides("widget", {"enabled": True, "a": 1})
        assert result == {"enabled": True, "a": 1}

    def test_bare_false_shortcut_becomes_enabled_false_when_opted_in(self):
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": False}):
            result = _get_config_with_overrides(
                "widget", {"enabled": True, "a": 1}, bool_shortcut=True,
            )
        assert result == {"enabled": False, "a": 1}

    def test_project_override_wins_over_global(self):
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": {"a": 2}}), \
             patch("app.config._load_project_overrides", return_value={"widget": {"a": 3}}):
            result = _get_config_with_overrides("widget", {"a": 1}, "myproj")
        assert result == {"a": 3}

    def test_project_bare_false_shortcut_disables_when_opted_in(self):
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": {"enabled": True}}), \
             patch("app.config._load_project_overrides", return_value={"widget": False}):
            result = _get_config_with_overrides(
                "widget", {"enabled": True}, "myproj", bool_shortcut=True,
            )
        assert result == {"enabled": False}

    def test_no_project_name_skips_override_lookup(self):
        from app.config import _get_config_with_overrides
        with _mock_config({"widget": {"a": 1}}), \
             patch("app.config._load_project_overrides") as mock_overrides:
            result = _get_config_with_overrides("widget", {"a": 1})
        mock_overrides.assert_not_called()
        assert result == {"a": 1}


class TestMigratedConfigFunctionsIgnoreBareFalse:
    """Regression coverage (issue #2340): migrating these functions onto the
    shared _get_config_with_overrides() helper must not grant a new
    ``<key>: false`` disable-shorthand to sections that never documented one
    — only stagnation/autonomous_health opt into that shortcut. Everything
    else must keep treating a stray ``False`` as malformed config (falls
    back to defaults, same as pre-refactor isinstance-dict checks)."""

    def test_branch_cleanup_false_does_not_disable(self):
        from app.config import get_branch_cleanup_config
        with _mock_config({"branch_cleanup": False}):
            assert get_branch_cleanup_config()["enabled"] is True

    def test_plan_review_false_does_not_disable(self):
        from app.config import get_plan_review_config
        with _mock_config({"plan_review": False}):
            assert get_plan_review_config()["enabled"] is True

    def test_prompt_guard_false_does_not_disable(self):
        from app.config import get_prompt_guard_config
        with _mock_config({"prompt_guard": False}):
            assert get_prompt_guard_config()["enabled"] is True

    def test_review_concurrency_false_does_not_disable(self):
        from app.config import get_review_concurrency_config
        with _mock_config({"review_concurrency": False}):
            assert get_review_concurrency_config()["enabled"] is True

    def test_review_issue_context_false_does_not_disable(self):
        from app.config import get_review_issue_context_config
        with _mock_config({"review_issue_context": False}):
            assert get_review_issue_context_config()["enabled"] is True

    def test_page_cache_reclaim_false_does_not_disable(self):
        from app.config import get_page_cache_reclaim_config
        with _mock_config({"page_cache_reclaim": False}):
            assert get_page_cache_reclaim_config()["enabled"] is True

    def test_private_review_gate_false_stays_at_default(self):
        # This section's default is already enabled=False, so a bare
        # `False` value must resolve the same way as an absent section.
        from app.config import get_private_review_gate_config
        with _mock_config({"private_review_gate": False}), \
             patch("app.config._load_project_overrides", return_value={}):
            assert get_private_review_gate_config()["enabled"] is False


# --- get_verify_requeue_max ---


class TestGetVerifyRequeueMax:
    def test_default(self):
        from app.config import get_verify_requeue_max

        with _mock_config({}):
            assert get_verify_requeue_max() == 2

    def test_override(self):
        from app.config import get_verify_requeue_max

        with _mock_config({"verification": {"max_requeue": 1}}):
            assert get_verify_requeue_max() == 1

    def test_zero_disables(self):
        from app.config import get_verify_requeue_max

        with _mock_config({"verification": {"max_requeue": 0}}):
            assert get_verify_requeue_max() == 0

    def test_negative_is_config_error_not_disable(self):
        from app.config import get_verify_requeue_max

        # A negative value is a mistake, not "disable" — returning the default
        # keeps the re-queue on instead of silently clamping to 0 (disabled).
        with _mock_config({"verification": {"max_requeue": -5}}):
            assert get_verify_requeue_max() == 2

    def test_non_numeric_falls_back_to_default(self):
        from app.config import get_verify_requeue_max

        with _mock_config({"verification": {"max_requeue": "lots"}}):
            assert get_verify_requeue_max() == 2
