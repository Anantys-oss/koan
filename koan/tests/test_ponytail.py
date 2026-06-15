"""Tests for ponytail code minimalism helpers.

Covers:
- ``app.config.is_ponytail_mode`` and ``_get_ponytail_dict`` reading
  the nested ``optimizations.ponytail.{enabled}`` mapping.
- ``app.ponytail.get_ponytail_section`` / ``append_ponytail`` end-to-end.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


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

    def test_scalar_bool_form_falls_back_to_default(self):
        from app.config import is_ponytail_mode
        with patch("app.config._load_config",
                   return_value={"optimizations": {"ponytail": False}}):
            assert is_ponytail_mode() is True


# ---------------------------------------------------------------------------
# ponytail.get_ponytail_section / append_ponytail
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


class TestAppendPonytail:
    """``append_ponytail`` is a no-op when the section is empty, otherwise concatenates."""

    def test_no_change_when_disabled(self):
        from app.ponytail import append_ponytail
        with patch("app.config._load_config", return_value={
            "optimizations": {"ponytail": {"enabled": False}}
        }):
            assert append_ponytail("base prompt") == "base prompt"

    def test_concatenates_with_blank_line(self):
        from app.ponytail import append_ponytail
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="X"):
                result = append_ponytail("base prompt")
                assert result == "base prompt\n\nX"

    def test_no_double_newline_when_prompt_already_ends_with_newline(self):
        from app.ponytail import append_ponytail
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="X"):
                result = append_ponytail("base prompt\n")
                assert result == "base prompt\nX"
