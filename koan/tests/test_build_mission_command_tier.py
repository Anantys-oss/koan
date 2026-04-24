"""Tests for tier override logic in mission_runner.build_mission_command."""

import pytest
from unittest.mock import patch, MagicMock


def _routing_cfg(enabled=True, trivial_model="haiku", trivial_turns=50):
    return {
        "enabled": enabled,
        "tiers": {
            "trivial": {"model": trivial_model, "max_turns": trivial_turns, "timeout_multiplier": 0.5},
            "simple":  {"model": "sonnet", "max_turns": 100, "timeout_multiplier": 0.75},
            "medium":  {"model": "",       "max_turns": 100, "timeout_multiplier": 1.0},
            "complex": {"model": "",       "max_turns": 500, "timeout_multiplier": 1.5},
        },
    }


def _base_models(mission_model=""):
    return {
        "mission": mission_model,
        "chat": "",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
    }


class TestBuildMissionCommandTierOverride:
    def _call(self, tier=None, autonomous_mode="implement", mission_model="",
              routing_cfg=None, review_mode_model=""):
        """Helper to call build_mission_command with mocked dependencies."""
        models = _base_models(mission_model)
        models["review_mode"] = review_mode_model

        captured = {}

        def fake_build(prompt, allowed_tools, model, fallback, output_format,
                       max_turns=0, mcp_configs=None, plugin_dirs=None,
                       system_prompt=""):
            captured["model"] = model
            captured["max_turns"] = max_turns
            return ["fake", "cmd"]

        from app.mission_runner import build_mission_command
        # Functions are imported locally inside build_mission_command, so patch
        # at the source modules rather than at mission_runner.
        with patch("app.config.get_model_config", return_value=models), \
             patch("app.config.get_mission_tools", return_value="Read,Glob"), \
             patch("app.config.get_mcp_configs", return_value=[]), \
             patch("app.cli_provider.build_full_command", side_effect=fake_build), \
             patch("app.config.get_complexity_routing_config",
                   return_value=routing_cfg if routing_cfg is not None else _routing_cfg()):
            build_mission_command(
                prompt="test prompt",
                autonomous_mode=autonomous_mode,
                tier=tier,
            )
        return captured

    def test_no_tier_uses_mission_model(self):
        result = self._call(tier=None, mission_model="default-model")
        assert result["model"] == "default-model"
        assert result["max_turns"] == 0  # no override

    def test_trivial_tier_uses_haiku_model(self):
        result = self._call(tier="trivial")
        assert result["model"] == "haiku"
        assert result["max_turns"] == 50

    def test_simple_tier_uses_sonnet(self):
        result = self._call(tier="simple")
        assert result["model"] == "sonnet"
        assert result["max_turns"] == 100

    def test_medium_tier_empty_model_keeps_mission_model(self):
        """Empty model string in tier config means no override."""
        result = self._call(tier="medium", mission_model="my-model")
        # medium has model="" so mission model should be used
        assert result["model"] == "my-model"

    def test_complex_tier_uses_max_turns_500(self):
        result = self._call(tier="complex")
        assert result["max_turns"] == 500

    def test_review_mode_takes_precedence_over_tier(self):
        """REVIEW mode model must not be overridden by tier."""
        result = self._call(
            tier="trivial",
            autonomous_mode="review",
            review_mode_model="review-model",
        )
        assert result["model"] == "review-model"

    def test_review_mode_without_review_model_uses_mission_model(self):
        """REVIEW mode without review_mode configured: mission model used, no tier."""
        result = self._call(
            tier="trivial",
            autonomous_mode="review",
            mission_model="base-model",
            review_mode_model="",
        )
        assert result["model"] == "base-model"

    def test_routing_disabled_tier_ignored(self):
        """When routing is disabled (None returned), tier override is skipped."""
        # Use a sentinel to distinguish explicit None from default
        _SENTINEL = object()

        def _call_disabled(tier, mission_model):
            models = _base_models(mission_model)
            captured = {}

            def fake_build(prompt, allowed_tools, model, fallback, output_format,
                           max_turns=0, mcp_configs=None, plugin_dirs=None,
                           system_prompt=""):
                captured["model"] = model
                captured["max_turns"] = max_turns
                return ["fake", "cmd"]

            from app.mission_runner import build_mission_command
            with patch("app.config.get_model_config", return_value=models), \
                 patch("app.config.get_mission_tools", return_value="Read,Glob"), \
                 patch("app.config.get_mcp_configs", return_value=[]), \
                 patch("app.cli_provider.build_full_command", side_effect=fake_build), \
                 patch("app.config.get_complexity_routing_config", return_value=None):
                build_mission_command(
                    prompt="test prompt",
                    autonomous_mode="implement",
                    tier=tier,
                )
            return captured

        result = _call_disabled(tier="trivial", mission_model="base-model")
        assert result["model"] == "base-model"
        assert result["max_turns"] == 0
