"""Tests for the chat-suggested command confirmation flow (command_confirm.py).

Behavior-focused: validates extraction/validation, affirmative matching, and the
single-slot, channel-bound, TTL'd, single-use pending store.
"""

import pytest

from app import command_confirm as cc


class _FakeSkill:
    def __init__(self, chat_confirmable):
        self.chat_confirmable = chat_confirmable


class _FakeRegistry:
    """Minimal registry: maps command name -> skill (or None)."""

    def __init__(self, mapping):
        self._mapping = mapping

    def find_by_command(self, name):
        return self._mapping.get(name)


@pytest.fixture
def registry():
    return _FakeRegistry({
        "recurring": _FakeSkill(True),
        "status": _FakeSkill(True),
        "delete_project": _FakeSkill(False),  # exists but not opted in
        # "stop" intentionally absent — core commands aren't in the registry
    })


@pytest.fixture(autouse=True)
def _clear_pending():
    cc.clear_pending()
    yield
    cc.clear_pending()


# --- is_affirmative -------------------------------------------------------

@pytest.mark.parametrize("text", ["yes", "Yes", "  OK ", "oui", "do it", "vas-y", "y"])
def test_affirmative_accepts_tight_matches(text):
    assert cc.is_affirmative(text) is True


@pytest.mark.parametrize("text", ["yes please run it", "no", "maybe", "yesterday", "run it"])
def test_affirmative_rejects_non_tight(text):
    assert cc.is_affirmative(text) is False


# --- is_confirmable / extraction -----------------------------------------

def test_confirmable_requires_opt_in(registry):
    assert cc.is_confirmable("/recurring run 3", registry) is True
    assert cc.is_confirmable("/delete_project x", registry) is False  # not opted in
    assert cc.is_confirmable("/stop", registry) is False              # not in registry


def test_extract_valid_marker_returns_literal_command(registry):
    reply = "Want me to force-run it?\nSUGGEST_COMMAND: /recurring run 3"
    cleaned, command = cc.extract_suggestion(reply, registry)
    assert command == "/recurring run 3"
    assert "SUGGEST_COMMAND" not in cleaned
    assert cleaned == "Want me to force-run it?"


def test_extract_rejected_marker_is_stripped_not_executed(registry):
    reply = "Sure.\nSUGGEST_COMMAND: /delete_project victim"
    cleaned, command = cc.extract_suggestion(reply, registry)
    assert command is None
    assert "SUGGEST_COMMAND" not in cleaned  # never shown to the human


def test_extract_no_marker(registry):
    cleaned, command = cc.extract_suggestion("just chatting", registry)
    assert command is None
    assert cleaned == "just chatting"


def test_extract_rejects_injection_chars(registry):
    # A marker carrying shell-ish/control chars must not validate.
    reply = "ok\nSUGGEST_COMMAND: /recurring; rm -rf /"
    cleaned, command = cc.extract_suggestion(reply, registry)
    assert command is None
    assert "SUGGEST_COMMAND" not in cleaned


# --- pending store --------------------------------------------------------

def test_pending_roundtrip_single_use():
    cc.register_pending("/recurring run 3", "chat-1")
    assert cc.peek_pending("chat-1") == "/recurring run 3"
    assert cc.take_pending("chat-1") == "/recurring run 3"
    # consumed
    assert cc.peek_pending("chat-1") is None
    assert cc.take_pending("chat-1") is None


def test_pending_is_channel_bound():
    cc.register_pending("/status", "chat-1")
    assert cc.peek_pending("other-chat") is None
    assert cc.take_pending("other-chat") is None
    # original channel still has it
    assert cc.take_pending("chat-1") == "/status"


def test_pending_last_write_wins():
    cc.register_pending("/recurring", "chat-1")
    cc.register_pending("/recurring run 3", "chat-1")
    assert cc.take_pending("chat-1") == "/recurring run 3"


def test_pending_expires():
    cc.register_pending("/status", "chat-1", ttl=100, now=1000.0)
    # before expiry
    assert cc.peek_pending("chat-1", now=1099.0) == "/status"
    # at/after expiry -> gone, and cleared
    assert cc.peek_pending("chat-1", now=1100.0) is None
    assert cc.take_pending("chat-1", now=1101.0) is None


def test_clear_pending():
    cc.register_pending("/status", "chat-1")
    cc.clear_pending()
    assert cc.peek_pending("chat-1") is None
