"""Tests for language_preference.py — language preference management."""

import json
from unittest.mock import patch

import pytest

from app.language_preference import (
    get_language,
    set_language,
    reset_language,
    get_language_instruction,
)


class TestGetLanguage:
    def test_no_file_defaults_to_english(self, tmp_path):
        """Fresh install (no language.json) defaults to English."""
        with patch("app.language_preference._get_language_file", return_value=tmp_path / "language.json"):
            assert get_language() == "english"

    def test_reads_language_from_file(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "french"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == "french"

    def test_empty_sentinel_returns_empty(self, tmp_path):
        """Explicit reset stores ``{"language": ""}`` → input-language mode."""
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": ""}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == ""

    def test_invalid_json_defaults_to_english(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text("not json")
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == "english"

    def test_missing_key_defaults_to_english(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"other": "value"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language() == "english"


class TestSetLanguage:
    def test_creates_file(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("French")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "french"

    def test_normalizes_to_lowercase(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("SPANISH")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "spanish"

    def test_strips_whitespace(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("  english  ")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "english"

    def test_overwrites_existing(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            set_language("german")
        data = json.loads(lang_file.read_text())
        assert data["language"] == "german"


class TestResetLanguage:
    def test_writes_empty_sentinel(self, tmp_path):
        """Reset persists an explicit empty sentinel (input-language mode)."""
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            reset_language()
        assert json.loads(lang_file.read_text())["language"] == ""

    def test_reset_distinguishable_from_fresh_install(self, tmp_path):
        """After reset, get_language() is empty (input mode), not the English default."""
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            reset_language()
            assert get_language() == ""

    def test_creates_file_if_missing(self, tmp_path):
        lang_file = tmp_path / "language.json"
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            reset_language()  # Should not raise
        assert lang_file.exists()


class TestGetLanguageInstruction:
    def test_no_file_enforces_english(self, tmp_path):
        """Fresh install enforces English without the user running /english."""
        with patch("app.language_preference._get_language_file", return_value=tmp_path / "language.json"):
            assert "english" in get_language_instruction()

    def test_reset_sentinel_returns_empty(self, tmp_path):
        """Explicit reset → no enforcement (reply in input language)."""
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": ""}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            assert get_language_instruction() == ""

    def test_with_language_returns_instruction(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "english"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            instruction = get_language_instruction()
        assert "english" in instruction
        assert "MUST" in instruction

    def test_instruction_contains_language_name(self, tmp_path):
        lang_file = tmp_path / "language.json"
        lang_file.write_text(json.dumps({"language": "japanese"}))
        with patch("app.language_preference._get_language_file", return_value=lang_file):
            instruction = get_language_instruction()
        assert "japanese" in instruction
