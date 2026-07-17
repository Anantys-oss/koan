"""Tests for chat_context.build_chat_prompt (specs/007-chat-process/).

These lock the invariants the dedicated chat process depends on:
- soul/summary are read *fresh* every call (no stale personality — the
  primary defect the earlier PR #1088 shipped);
- the 12k-char cap forces lite mode and, as a last resort, truncates the
  user message.
"""

from unittest.mock import patch

import pytest

from app import chat_context as cc


@pytest.fixture
def instance(tmp_path):
    """A minimal instance/ tree with the dirs build_chat_prompt reads."""
    (tmp_path / "memory" / "global").mkdir(parents=True)
    (tmp_path / "journal").mkdir(parents=True)
    return tmp_path


def _build(text, instance, *, lite=False):
    """Call build_chat_prompt with paths pointed at the test instance."""
    with patch.object(cc, "INSTANCE_DIR", instance), \
         patch.object(cc, "KOAN_ROOT", instance), \
         patch.object(cc, "MISSIONS_FILE", instance / "missions.md"), \
         patch.object(cc, "load_recent_history", return_value=[]), \
         patch.object(cc, "format_conversation_history", return_value=""), \
         patch.object(cc, "get_tools_description", return_value=""):
        return cc.build_chat_prompt(text, lite=lite)


def test_prompt_includes_running_status(instance):
    prompt = _build("hi", instance)
    assert "RUNNING" in prompt or "▶️" in prompt


def test_prompt_includes_paused_status(instance):
    (instance / ".koan-pause").write_text("PAUSE")
    prompt = _build("hi", instance)
    assert "PAUSED" in prompt or "⏸️" in prompt


def test_soul_is_read_fresh_without_restart(instance):
    """Editing soul.md must change the next prompt — no cached snapshot (FR-004)."""
    with patch.object(cc, "get_soul", return_value="SOUL-ONE"), \
         patch.object(cc, "get_summary", return_value=""):
        first = _build("hi", instance)
    assert "SOUL-ONE" in first
    with patch.object(cc, "get_soul", return_value="SOUL-TWO"), \
         patch.object(cc, "get_summary", return_value=""):
        second = _build("hi", instance)
    assert "SOUL-TWO" in second
    assert "SOUL-ONE" not in second


def test_summary_is_read_fresh_without_restart(instance):
    with patch.object(cc, "get_soul", return_value=""), \
         patch.object(cc, "get_summary", return_value="SUMMARY-ALPHA"):
        prompt = _build("hi", instance)
    assert "SUMMARY-ALPHA" in prompt


def test_summary_omitted_in_lite_mode(instance):
    with patch.object(cc, "get_soul", return_value=""), \
         patch.object(cc, "get_summary", return_value="SUMMARY-ALPHA"):
        prompt = _build("hi", instance, lite=True)
    assert "SUMMARY-ALPHA" not in prompt


def test_oversized_prompt_forces_lite_then_truncates(instance):
    long_text = "x" * 15000
    prompt = _build(long_text, instance)
    assert len(prompt) <= 12000
    assert "[truncated]" in prompt


def test_short_message_not_truncated(instance):
    prompt = _build("hello there", instance, lite=True)
    assert "[truncated]" not in prompt
    assert "hello there" in prompt


def test_missions_read_failure_degrades_to_empty(instance):
    """A store read error must not crash prompt building."""
    with patch.object(cc, "read_sections_cached", side_effect=RuntimeError("db locked")):
        prompt = _build("hi", instance)
    assert isinstance(prompt, str) and prompt


def test_load_cached_context_returns_empty_for_missing_file(tmp_path):
    assert cc.load_cached_context(tmp_path / "nope.md") == ""


def test_load_cached_context_reloads_on_mtime_change(tmp_path):
    f = tmp_path / "prefs.md"
    f.write_text("Prefers French")
    assert cc.load_cached_context(f) == "Prefers French"
    # Bust the mtime cache and rewrite → new content is returned.
    cc._chat_context_cache.pop(str(f), None)
    f.write_text("Prefers English")
    assert cc.load_cached_context(f) == "Prefers English"


def test_read_sections_cached_serves_within_ttl():
    cc._sections_cache["ts"] = 0.0
    cc._sections_cache["value"] = None
    with patch("app.mission_store.transition.read_sections",
               return_value={"pending": ["m"]}) as mock_read, \
         patch("app.chat_context.time.time", side_effect=[100.0, 101.0]):
        first = cc.read_sections_cached("/x")
        second = cc.read_sections_cached("/x")
    assert first == second == {"pending": ["m"]}
    mock_read.assert_called_once()  # second call served from cache


def test_read_sections_cached_expires_after_ttl():
    cc._sections_cache["ts"] = 0.0
    cc._sections_cache["value"] = None
    with patch("app.mission_store.transition.read_sections",
               side_effect=[{"pending": ["a"]}, {"pending": ["b"]}]) as mock_read, \
         patch("app.chat_context.time.time", side_effect=[100.0, 200.0]):
        first = cc.read_sections_cached("/x")
        second = cc.read_sections_cached("/x")
    assert first == {"pending": ["a"]}
    assert second == {"pending": ["b"]}  # TTL expired → re-read
    assert mock_read.call_count == 2
