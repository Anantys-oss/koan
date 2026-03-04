"""Tests for chat_receiver.py — Pub/Sub parsing, deduplication, event handling."""

import json
import time

import pytest

from app.chat_receiver import ChatEvent, parse_pubsub_message, is_duplicate, _SEEN_MESSAGES


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_message_payload(event_type="MESSAGE", text="status", **overrides):
    """Build a minimal Google Chat Pub/Sub payload."""
    payload = {
        "type": event_type,
        "eventTime": "2026-03-04T10:00:00Z",
        "space": {
            "name": "spaces/AAA",
            "displayName": "AI Governor Tests",
            "type": overrides.pop("space_type_raw", "ROOM"),
        },
        "message": {
            "name": "spaces/AAA/messages/BBB",
            "text": text,
            "argumentText": overrides.pop("argument_text", ""),
            "thread": {"name": "spaces/AAA/threads/CCC"},
            "sender": {
                "name": "users/123",
                "displayName": "Stéphane",
                "email": "stephane.levy@yourart.art",
                "type": "HUMAN",
            },
        },
        "user": {
            "name": "users/123",
            "displayName": "Stéphane",
            "email": "stephane.levy@yourart.art",
            "type": "HUMAN",
        },
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


# ── parse_pubsub_message ─────────────────────────────────────────────

class TestParsePubsubMessage:
    def test_basic_message(self):
        data = _make_message_payload(text="@AiGovernor status")
        event = parse_pubsub_message(data)

        assert event.event_type == "MESSAGE"
        assert event.space_name == "spaces/AAA"
        assert event.space_type == "ROOM"
        assert event.thread_name == "spaces/AAA/threads/CCC"
        assert event.message_name == "spaces/AAA/messages/BBB"
        assert event.sender_email == "stephane.levy@yourart.art"
        assert event.text == "@AiGovernor status"
        assert event.slash_command_id is None

    def test_dm_message(self):
        data = _make_message_payload(text="status", space_type_raw="DM")
        event = parse_pubsub_message(data)

        assert event.space_type == "DM"

    def test_slash_command(self):
        payload = json.loads(_make_message_payload())
        payload["message"]["slashCommand"] = {"commandId": "1"}
        payload["message"]["argumentText"] = "  --verbose  "
        data = json.dumps(payload).encode()

        event = parse_pubsub_message(data)
        assert event.slash_command_id == 1
        assert event.argument_text == "--verbose"

    def test_card_clicked(self):
        payload = {
            "type": "CARD_CLICKED",
            "space": {"name": "spaces/AAA", "type": "ROOM"},
            "message": {
                "name": "spaces/AAA/messages/BBB",
                "thread": {"name": "spaces/AAA/threads/CCC"},
            },
            "action": {
                "actionMethodName": "advisor_feedback",
                "parameters": [
                    {"key": "detectionId", "value": "det-001"},
                    {"key": "feedback", "value": "relevant"},
                ],
            },
            "user": {
                "email": "stephane.levy@yourart.art",
                "displayName": "Stéphane",
            },
        }
        data = json.dumps(payload).encode()
        event = parse_pubsub_message(data)

        assert event.event_type == "CARD_CLICKED"
        assert event.action_name == "advisor_feedback"
        assert event.action_params == {"detectionId": "det-001", "feedback": "relevant"}

    def test_added_to_space(self):
        payload = {
            "type": "ADDED_TO_SPACE",
            "eventTime": "2026-03-04T10:00:00Z",
            "space": {"name": "spaces/AAA", "type": "ROOM"},
            "message": {},
            "user": {"email": "stephane.levy@yourart.art", "displayName": "Stéphane"},
        }
        data = json.dumps(payload).encode()
        event = parse_pubsub_message(data)
        assert event.event_type == "ADDED_TO_SPACE"

    def test_removed_from_space(self):
        payload = {
            "type": "REMOVED_FROM_SPACE",
            "space": {"name": "spaces/AAA", "type": "ROOM"},
            "message": {},
            "user": {"email": "", "displayName": ""},
        }
        data = json.dumps(payload).encode()
        event = parse_pubsub_message(data)
        assert event.event_type == "REMOVED_FROM_SPACE"

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_pubsub_message(b"not json")

    def test_unknown_event_type(self):
        data = json.dumps({"type": "UNKNOWN_TYPE"}).encode()
        with pytest.raises(ValueError, match="Unknown event type"):
            parse_pubsub_message(data)


# ── is_duplicate ─────────────────────────────────────────────────────

class TestIsDuplicate:
    def setup_method(self):
        _SEEN_MESSAGES.clear()

    def test_first_time_not_duplicate(self):
        assert is_duplicate("msg-001") is False

    def test_second_time_is_duplicate(self):
        is_duplicate("msg-002")
        assert is_duplicate("msg-002") is True

    def test_empty_name_not_duplicate(self):
        assert is_duplicate("") is False
        assert is_duplicate("") is False

    def test_ttl_expiry(self):
        _SEEN_MESSAGES["msg-old"] = time.time() - 700  # Expired (>600s)
        assert is_duplicate("msg-old", ttl=600) is False

    def test_different_messages(self):
        is_duplicate("msg-a")
        assert is_duplicate("msg-b") is False
