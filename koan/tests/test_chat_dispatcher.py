"""Tests for chat_dispatcher.py — command parsing, permissions, dispatch."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.chat_dispatcher import (
    ChatCommand,
    ChatResponse,
    PERMISSIONS,
    SLASH_COMMAND_MAP,
    LONG_COMMANDS,
    check_permission,
    suggest_command,
    build_help_text,
    log_chat_audit,
    parse_command,
    resolve_sender_type,
)
from app.chat_receiver import ChatEvent


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_event(**kwargs):
    """Build a minimal ChatEvent."""
    defaults = dict(
        event_type="MESSAGE",
        space_name="spaces/AAA",
        space_type="ROOM",
        thread_name="spaces/AAA/threads/CCC",
        message_name="spaces/AAA/messages/BBB",
        sender_email="stephane.levy@yourart.art",
        sender_display_name="Stéphane",
        text="status",
        argument_text="",
        slash_command_id=None,
        action_name="",
        action_params={},
        timestamp="2026-03-04T10:00:00Z",
        raw={},
    )
    defaults.update(kwargs)
    return ChatEvent(**defaults)


def _make_command(**kwargs):
    """Build a minimal ChatCommand."""
    defaults = dict(
        skill_name="status",
        args="",
        sender_email="stephane.levy@yourart.art",
        sender_type="governor",
        sender_name="Stéphane",
        space_name="spaces/AAA",
        thread_name="spaces/AAA/threads/CCC",
        message_name="spaces/AAA/messages/BBB",
        is_dm=False,
        is_slash_command=False,
    )
    defaults.update(kwargs)
    return ChatCommand(**defaults)


# ── check_permission ─────────────────────────────────────────────────

class TestCheckPermission:
    def test_governor_has_all_access(self):
        cmd = _make_command(sender_type="governor", skill_name="vault")
        assert check_permission(cmd) is None

    def test_citizen_can_access_status(self):
        cmd = _make_command(sender_type="citizen", skill_name="status")
        assert check_permission(cmd) is None

    def test_citizen_can_access_help(self):
        cmd = _make_command(sender_type="citizen", skill_name="help")
        assert check_permission(cmd) is None

    def test_citizen_denied_vault(self):
        cmd = _make_command(sender_type="citizen", skill_name="vault")
        result = check_permission(cmd)
        assert result is not None
        assert "réservée aux governors" in result

    def test_tech_can_access_watcher(self):
        cmd = _make_command(sender_type="tech", skill_name="watcher")
        assert check_permission(cmd) is None

    def test_tech_denied_vault(self):
        cmd = _make_command(sender_type="tech", skill_name="vault")
        assert check_permission(cmd) is not None

    def test_unknown_denied_status(self):
        cmd = _make_command(sender_type="unknown", skill_name="status")
        result = check_permission(cmd)
        assert "non reconnu" in result

    def test_unknown_can_access_help(self):
        cmd = _make_command(sender_type="unknown", skill_name="help")
        assert check_permission(cmd) is None


# ── suggest_command ──────────────────────────────────────────────────

class TestSuggestCommand:
    def test_close_match(self):
        msg, suggestions = suggest_command("statu")
        assert "status" in suggestions

    def test_no_match(self):
        msg, suggestions = suggest_command("zzzzz")
        assert len(suggestions) == 0
        assert "inconnue" in msg

    def test_advisor_match(self):
        msg, suggestions = suggest_command("adviso")
        assert "advisor" in suggestions


# ── build_help_text ──────────────────────────────────────────────────

class TestBuildHelpText:
    def test_governor_sees_all(self):
        text = build_help_text("governor")
        assert "vault" in text
        assert "status" in text

    def test_citizen_sees_limited(self):
        text = build_help_text("citizen")
        assert "status" in text
        assert "help" in text
        assert "vault" not in text

    def test_unknown_sees_help_only(self):
        text = build_help_text("unknown")
        assert "help" in text
        assert "vault" not in text
        assert "status" not in text


# ── parse_command ────────────────────────────────────────────────────

class TestParseCommand:
    @patch("app.chat_dispatcher.resolve_sender_type", return_value=("governor", "Stéphane"))
    def test_text_message(self, mock_resolve):
        event = _make_event(text="advisor scan --full", argument_text="advisor scan --full")
        cmd = parse_command(event)

        assert cmd.skill_name == "advisor"
        assert cmd.args == "scan --full"
        assert cmd.is_slash_command is False

    @patch("app.chat_dispatcher.resolve_sender_type", return_value=("governor", "Stéphane"))
    def test_slash_command(self, mock_resolve):
        event = _make_event(slash_command_id=1, argument_text="  --verbose  ")
        cmd = parse_command(event)

        assert cmd.skill_name == "status"
        assert cmd.args == "--verbose"
        assert cmd.is_slash_command is True

    @patch("app.chat_dispatcher.resolve_sender_type", return_value=("governor", "Stéphane"))
    def test_dm_message(self, mock_resolve):
        event = _make_event(space_type="DM", text="vault list", argument_text="")
        cmd = parse_command(event)

        assert cmd.skill_name == "vault"
        assert cmd.args == "list"
        assert cmd.is_dm is True

    @patch("app.chat_dispatcher.resolve_sender_type", return_value=("governor", "Stéphane"))
    def test_empty_text_defaults_to_help(self, mock_resolve):
        event = _make_event(text="", argument_text="")
        cmd = parse_command(event)

        assert cmd.skill_name == "help"


# ── log_chat_audit ───────────────────────────────────────────────────

class TestLogChatAudit:
    def test_writes_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.chat_dispatcher.INSTANCE_DIR", tmp_path)
        cmd = _make_command()
        log_chat_audit(cmd, "success", 123)

        journal = tmp_path / "journal.jsonl"
        assert journal.exists()
        entry = json.loads(journal.read_text().strip())
        assert entry["source"] == "gchat"
        assert entry["skill"] == "status"
        assert entry["result_status"] == "success"
        assert entry["response_time_ms"] == 123


# ── PERMISSIONS structure ────────────────────────────────────────────

class TestPermissionsStructure:
    def test_governor_wildcard(self):
        assert "*" in PERMISSIONS["governor"]

    def test_citizen_limited(self):
        assert "vault" not in PERMISSIONS["citizen"]
        assert "status" in PERMISSIONS["citizen"]

    def test_all_types_have_help(self):
        for user_type, perms in PERMISSIONS.items():
            assert "*" in perms or "help" in perms, f"{user_type} missing help access"


# ── SLASH_COMMAND_MAP ────────────────────────────────────────────────

class TestSlashCommandMap:
    def test_has_8_commands(self):
        assert len(SLASH_COMMAND_MAP) == 8

    def test_ids_are_sequential(self):
        assert set(SLASH_COMMAND_MAP.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_status_is_1(self):
        assert SLASH_COMMAND_MAP[1] == "status"

    def test_help_is_8(self):
        assert SLASH_COMMAND_MAP[8] == "help"
