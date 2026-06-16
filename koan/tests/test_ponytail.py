"""Tests for ponytail code minimalism helpers.

Covers:
- ``app.config.is_ponytail_mode`` and ``_get_ponytail_dict`` reading
  the nested ``optimizations.ponytail.{enabled}`` mapping.
- ``app.ponytail.get_ponytail_section`` end-to-end.
"""

from __future__ import annotations

from unittest.mock import patch


# ---------------------------------------------------------------------------
# Config layer
# ---------------------------------------------------------------------------


class TestIsPonytailModeNested:
    """``is_ponytail_mode`` reads ``optimizations.ponytail.enabled``."""

    def test_default_when_no_config(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config", return_value={}):
            assert is_ponytail_mode() is True

    def test_nested_enabled_true(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"ponytail": {"enabled": True}}
        }):
            assert is_ponytail_mode() is True

    def test_nested_enabled_false(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"ponytail": {"enabled": False}}
        }):
            assert is_ponytail_mode() is False

    def test_nested_missing_enabled_defaults_true(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"ponytail": {}}
        }):
            assert is_ponytail_mode() is True

    def test_nested_garbage_enabled_defaults_true(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"ponytail": {"enabled": "yes"}}
        }):
            assert is_ponytail_mode() is True

    def test_optimizations_not_dict(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config",
                   return_value={"optimizations": "garbage"}):
            assert is_ponytail_mode() is True

    def test_scalar_bool_false_disables(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config",
                   return_value={"optimizations": {"ponytail": False}}):
            assert is_ponytail_mode() is False

    def test_scalar_bool_true_enables(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config",
                   return_value={"optimizations": {"ponytail": True}}):
            assert is_ponytail_mode() is True


# ---------------------------------------------------------------------------
# ponytail.get_ponytail_section
# ---------------------------------------------------------------------------


class TestGetPonytailSection:
    """Returns directive when applicable, empty string otherwise."""

    def test_agent_loop_returns_directive_by_default(self):
        from app.ponytail import get_ponytail_section
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="PONYTAIL-MARKER"):
                assert get_ponytail_section() == "PONYTAIL-MARKER"

    def test_agent_loop_empty_when_globally_disabled(self):
        from app.ponytail import get_ponytail_section
        with patch("app.config._load_config", return_value={
            "optimizations": {"ponytail": {"enabled": False}}
        }):
            assert get_ponytail_section() == ""

    def test_swallows_load_prompt_failure(self):
        from app.ponytail import get_ponytail_section
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt",
                       side_effect=FileNotFoundError("missing")):
                assert get_ponytail_section() == ""


