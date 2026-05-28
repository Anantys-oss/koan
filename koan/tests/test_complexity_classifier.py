"""Tests for app.complexity_classifier — mission tier pre-classification."""

import pytest
from unittest.mock import MagicMock, patch

from app.complexity_classifier import MissionTier, _parse_tier_response, _DEFAULT_TIER


# ---------------------------------------------------------------------------
# MissionTier enum
# ---------------------------------------------------------------------------

class TestMissionTier:
    def test_values(self):
        assert MissionTier.TRIVIAL.value == "trivial"
        assert MissionTier.SIMPLE.value == "simple"
        assert MissionTier.MEDIUM.value == "medium"
        assert MissionTier.COMPLEX.value == "complex"

    def test_is_string_enum(self):
        assert isinstance(MissionTier.TRIVIAL, str)


# ---------------------------------------------------------------------------
# _parse_tier_response
# ---------------------------------------------------------------------------

class TestParseTierResponse:
    def test_trivial(self):
        assert _parse_tier_response('{"tier": "trivial", "rationale": "Just a typo fix."}') == MissionTier.TRIVIAL

    def test_simple(self):
        assert _parse_tier_response('{"tier": "simple", "rationale": "One file."}') == MissionTier.SIMPLE

    def test_medium(self):
        assert _parse_tier_response('{"tier": "medium", "rationale": "Multi-file."}') == MissionTier.MEDIUM

    def test_complex(self):
        assert _parse_tier_response('{"tier": "complex", "rationale": "Architecture."}') == MissionTier.COMPLEX

    def test_case_insensitive(self):
        assert _parse_tier_response('{"tier": "TRIVIAL", "rationale": "x"}') == MissionTier.TRIVIAL

    def test_empty_response_defaults_to_medium(self):
        assert _parse_tier_response("") == _DEFAULT_TIER

    def test_malformed_json_defaults_to_medium(self):
        assert _parse_tier_response("not json at all") == _DEFAULT_TIER

    def test_unknown_tier_defaults_to_medium(self):
        assert _parse_tier_response('{"tier": "supercomplex", "rationale": "x"}') == _DEFAULT_TIER

    def test_missing_tier_key_defaults_to_medium(self):
        assert _parse_tier_response('{"rationale": "no tier here"}') == _DEFAULT_TIER

    def test_strips_markdown_fences(self):
        response = "```json\n{\"tier\": \"trivial\", \"rationale\": \"small\"}\n```"
        assert _parse_tier_response(response) == MissionTier.TRIVIAL

    def test_json_embedded_in_text(self):
        response = 'Here is my answer:\n{"tier": "simple", "rationale": "one file"}\nDone.'
        assert _parse_tier_response(response) == MissionTier.SIMPLE


# ---------------------------------------------------------------------------
# classify_mission_complexity — integration (mocked CLI)
# ---------------------------------------------------------------------------

class TestClassifyMissionComplexity:
    def _make_fake_result(self, tier: str):
        result = MagicMock()
        result.returncode = 0
        result.stdout = f'{{"tier": "{tier}", "rationale": "test"}}'
        result.stderr = ""
        return result

    def test_classify_trivial(self):
        from app.complexity_classifier import classify_mission_complexity
        fake = self._make_fake_result("trivial")
        with patch("app.cli_exec.run_cli_with_retry", return_value=fake), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "x"]), \
             patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"}), \
             patch("app.prompts.load_prompt", return_value="classify this"):
            tier = classify_mission_complexity("fix typo in README")
        assert tier == MissionTier.TRIVIAL

    def test_classify_complex(self):
        from app.complexity_classifier import classify_mission_complexity
        fake = self._make_fake_result("complex")
        with patch("app.cli_exec.run_cli_with_retry", return_value=fake), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "x"]), \
             patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"}), \
             patch("app.prompts.load_prompt", return_value="classify this"):
            tier = classify_mission_complexity("Redesign entire auth pipeline")
        assert tier == MissionTier.COMPLEX

    def test_cli_failure_defaults_to_medium(self):
        from app.complexity_classifier import classify_mission_complexity
        fake = MagicMock()
        fake.returncode = 1
        fake.stdout = ""
        fake.stderr = "error"
        with patch("app.cli_exec.run_cli_with_retry", return_value=fake), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "x"]), \
             patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"}), \
             patch("app.prompts.load_prompt", return_value="classify this"):
            tier = classify_mission_complexity("some mission")
        assert tier == MissionTier.MEDIUM

    def test_exception_defaults_to_medium(self):
        from app.complexity_classifier import classify_mission_complexity
        with patch("app.cli_exec.run_cli_with_retry", side_effect=RuntimeError("network error")), \
             patch("app.cli_provider.build_full_command", return_value=["claude", "-p", "x"]), \
             patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"}), \
             patch("app.prompts.load_prompt", return_value="classify this"):
            tier = classify_mission_complexity("some mission")
        assert tier == MissionTier.MEDIUM

    def test_empty_mission_defaults_to_medium(self):
        from app.complexity_classifier import classify_mission_complexity
        tier = classify_mission_complexity("")
        assert tier == MissionTier.MEDIUM

    def test_uses_lightweight_model_from_config(self):
        """Classifier should use the lightweight model key, not a hardcoded string."""
        from app.complexity_classifier import classify_mission_complexity
        captured_model = []
        fake = self._make_fake_result("simple")

        def capture_build(prompt, allowed_tools, model, fallback, max_turns):
            captured_model.append(model)
            return ["claude", "-p", prompt]

        with patch("app.cli_exec.run_cli_with_retry", return_value=fake), \
             patch("app.cli_provider.build_full_command", side_effect=capture_build), \
             patch("app.config.get_model_config", return_value={"lightweight": "my-custom-haiku", "fallback": "sonnet"}), \
             patch("app.prompts.load_prompt", return_value="x"):
            classify_mission_complexity("task", "myproject")
        assert captured_model == ["my-custom-haiku"]
