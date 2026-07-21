#!/usr/bin/env python3
"""
Language preference management for Kōan.

Stores and retrieves the user's preferred reply language.
When unset (fresh install), Kōan defaults to English so a new user never has
to run /english to get consistent replies.
When explicitly reset, Kōan replies in the same language as the input.

Storage: instance/language.json
"""

import json
import os
from pathlib import Path

from app.utils import atomic_write

# Default reply language when the user has never configured a preference.
# A fresh install has no language.json, so replies would otherwise fall back to
# soul.md's language — forcing the user to run /english. English is the default.
DEFAULT_LANGUAGE = "english"


def _get_language_file() -> Path:
    """Return path to the language preference file."""
    koan_root = Path(os.environ.get("KOAN_ROOT", "."))
    return koan_root / "instance" / "language.json"


def get_language() -> str:
    """Get the current language preference.

    Returns:
        Language name (e.g. "english", "french"). Defaults to English when no
        preference file exists. Returns an empty string only when the user has
        explicitly reset (input-language mode), stored as ``{"language": ""}``.
    """
    lang_file = _get_language_file()
    if not lang_file.exists():
        return DEFAULT_LANGUAGE
    try:
        data = json.loads(lang_file.read_text())
    except (json.JSONDecodeError, OSError):
        return DEFAULT_LANGUAGE
    if not isinstance(data, dict):
        return DEFAULT_LANGUAGE
    return data.get("language", DEFAULT_LANGUAGE)


def set_language(language: str) -> None:
    """Set the language preference.

    Args:
        language: Language name (e.g. "english", "french", "spanish").
    """
    lang_file = _get_language_file()
    lang_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(lang_file, json.dumps({"language": language.strip().lower()}))


def reset_language() -> None:
    """Reset to input-language mode (reply in same language as input).

    Persists an explicit empty sentinel rather than deleting the file, so a
    deliberate reset is distinguishable from a fresh install (which defaults to
    English via :func:`get_language`).
    """
    lang_file = _get_language_file()
    lang_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(lang_file, json.dumps({"language": ""}))


def get_language_instruction() -> str:
    """Get a prompt instruction for language enforcement.

    Returns:
        Instruction string to inject into prompts, or empty string if no override.
    """
    lang = get_language()
    if not lang:
        return ""
    return f"IMPORTANT: You MUST reply in {lang}. This is a user-configured language preference. All your responses must be written in {lang}, regardless of the input language."
