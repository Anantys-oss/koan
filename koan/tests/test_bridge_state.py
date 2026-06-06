"""Tests for app.bridge_state — shared module-level state for the messaging bridge."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestMigrateHistoryFile:
    """Tests for _migrate_history_file()."""

    def test_migrates_old_to_new(self, tmp_path, monkeypatch):
        """When old file exists and new doesn't, rename old -> new."""
        old = tmp_path / "telegram-history.jsonl"
        new = tmp_path / "conversation-history.jsonl"
        old.write_text('{"msg": "hello"}\n')

        monkeypatch.setattr("app.bridge_state.INSTANCE_DIR", tmp_path)

        from app.bridge_state import _migrate_history_file
        result = _migrate_history_file()

        assert result == new
        assert new.exists()
        assert not old.exists()
        assert new.read_text() == '{"msg": "hello"}\n'

    def test_skips_when_new_exists(self, tmp_path, monkeypatch):
        """When new file already exists, don't migrate (idempotent)."""
        old = tmp_path / "telegram-history.jsonl"
        new = tmp_path / "conversation-history.jsonl"
        old.write_text("old content")
        new.write_text("new content")

        monkeypatch.setattr("app.bridge_state.INSTANCE_DIR", tmp_path)

        from app.bridge_state import _migrate_history_file
        result = _migrate_history_file()

        assert result == new
        assert old.exists()  # Old file left untouched
        assert new.read_text() == "new content"

    def test_returns_new_path_when_no_old(self, tmp_path, monkeypatch):
        """When neither file exists, returns the new path."""
        monkeypatch.setattr("app.bridge_state.INSTANCE_DIR", tmp_path)

        from app.bridge_state import _migrate_history_file
        result = _migrate_history_file()

        assert result == tmp_path / "conversation-history.jsonl"

    def test_returns_old_on_rename_failure(self, tmp_path, monkeypatch):
        """When rename fails, returns old path as fallback."""
        old = tmp_path / "telegram-history.jsonl"
        old.write_text("data")

        monkeypatch.setattr("app.bridge_state.INSTANCE_DIR", tmp_path)

        from app.bridge_state import _migrate_history_file

        # Make rename fail by patching the old Path object
        with patch.object(Path, "rename", side_effect=OSError("permission denied")):
            result = _migrate_history_file()

        assert result == old


class TestResolveDefaultProjectPath:
    """Tests for _resolve_default_project_path()."""

    @patch("app.utils.get_known_projects", return_value=[("proj1", "/path/to/proj1")])
    def test_returns_first_project_path(self, mock_projects):
        """Returns the path of the first known project."""
        from app.bridge_state import _resolve_default_project_path
        assert _resolve_default_project_path() == "/path/to/proj1"

    @patch("app.utils.get_known_projects", return_value=[])
    def test_returns_empty_when_no_projects(self, mock_projects):
        """Returns empty string when no projects are configured."""
        from app.bridge_state import _resolve_default_project_path
        assert _resolve_default_project_path() == ""

    @patch("app.utils.get_known_projects", side_effect=Exception("config broken"))
    def test_returns_empty_on_error(self, mock_projects):
        """Returns empty string on any exception (defensive)."""
        from app.bridge_state import _resolve_default_project_path
        assert _resolve_default_project_path() == ""

    @patch("app.utils.get_known_projects", return_value=[
        ("alpha", "/a"), ("beta", "/b"), ("gamma", "/c"),
    ])
    def test_returns_first_of_multiple(self, mock_projects):
        """With multiple projects, returns only the first."""
        from app.bridge_state import _resolve_default_project_path
        assert _resolve_default_project_path() == "/a"


class TestSkillRegistry:
    """Tests for _get_registry() and _reset_registry()."""

    def test_reset_clears_registry(self):
        """_reset_registry() sets the singleton to None."""
        import app.bridge_state as bs
        bs._skill_registry = "something"
        bs._reset_registry()
        assert bs._skill_registry is None

    @patch("app.bridge_state.build_registry")
    def test_get_registry_creates_on_first_call(self, mock_build, tmp_path, monkeypatch):
        """_get_registry() builds a registry on first access."""
        import app.bridge_state as bs
        bs._reset_registry()

        # Ensure INSTANCE_DIR/skills doesn't exist (no extra dirs)
        monkeypatch.setattr(bs, "INSTANCE_DIR", tmp_path)

        mock_registry = MagicMock()
        mock_build.return_value = mock_registry

        result = bs._get_registry()
        assert result is mock_registry
        mock_build.assert_called_once_with([])

        # Cleanup
        bs._reset_registry()

    @patch("app.bridge_state.build_registry")
    def test_get_registry_caches(self, mock_build, tmp_path, monkeypatch):
        """_get_registry() returns cached instance on second call."""
        import app.bridge_state as bs
        bs._reset_registry()
        monkeypatch.setattr(bs, "INSTANCE_DIR", tmp_path)

        mock_registry = MagicMock()
        mock_build.return_value = mock_registry

        result1 = bs._get_registry()
        result2 = bs._get_registry()

        assert result1 is result2
        assert mock_build.call_count == 1  # Only built once

        bs._reset_registry()

    @patch("app.bridge_state.build_registry")
    def test_get_registry_with_instance_skills(self, mock_build, tmp_path, monkeypatch):
        """_get_registry() includes instance/skills/ when present."""
        import app.bridge_state as bs
        bs._reset_registry()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        monkeypatch.setattr(bs, "INSTANCE_DIR", tmp_path)

        mock_registry = MagicMock()
        mock_build.return_value = mock_registry

        bs._get_registry()

        # Should pass the instance skills dir as extra
        mock_build.assert_called_once()
        call_args = mock_build.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0] == skills_dir

        bs._reset_registry()

    @patch("app.bridge_state.build_registry")
    def test_get_registry_invalidates_on_mtime_change(self, mock_build, tmp_path, monkeypatch):
        """_get_registry() rebuilds when skills directory mtime changes."""
        import app.bridge_state as bs
        bs._reset_registry()
        monkeypatch.setattr(bs, "INSTANCE_DIR", tmp_path)

        mock_registry_1 = MagicMock()
        mock_registry_2 = MagicMock()
        mock_build.side_effect = [mock_registry_1, mock_registry_2]

        # First call builds the registry
        result1 = bs._get_registry()
        assert result1 is mock_registry_1
        assert mock_build.call_count == 1

        # Second call returns cached (same mtime)
        result2 = bs._get_registry()
        assert result2 is mock_registry_1
        assert mock_build.call_count == 1

        # Simulate skills directory change by bumping the stored mtime
        bs._skill_registry_mtime -= 1.0

        # Third call detects mtime change and rebuilds
        result3 = bs._get_registry()
        assert result3 is mock_registry_2
        assert mock_build.call_count == 2

        bs._reset_registry()


class TestModuleLevelConstants:
    """Tests for module-level constant derivation."""

    def test_koan_root_is_path(self):
        """KOAN_ROOT should be a Path object."""
        from app.bridge_state import KOAN_ROOT
        assert isinstance(KOAN_ROOT, Path)

    def test_instance_dir_under_koan_root(self):
        """INSTANCE_DIR should be KOAN_ROOT / 'instance'."""
        from app.bridge_state import KOAN_ROOT, INSTANCE_DIR
        assert INSTANCE_DIR == KOAN_ROOT / "instance"

    def test_missions_file_path(self):
        """MISSIONS_FILE should be under INSTANCE_DIR."""
        from app.bridge_state import INSTANCE_DIR, MISSIONS_FILE
        assert MISSIONS_FILE == INSTANCE_DIR / "missions.md"

    def test_outbox_file_path(self):
        """OUTBOX_FILE should be under INSTANCE_DIR."""
        from app.bridge_state import INSTANCE_DIR, OUTBOX_FILE
        assert OUTBOX_FILE == INSTANCE_DIR / "outbox.md"

    def test_poll_interval_is_int(self):
        """POLL_INTERVAL should be an integer."""
        from app.bridge_state import POLL_INTERVAL
        assert isinstance(POLL_INTERVAL, int)

    def test_chat_timeout_is_int(self):
        """CHAT_TIMEOUT should be an integer."""
        from app.bridge_state import CHAT_TIMEOUT
        assert isinstance(CHAT_TIMEOUT, int)

    def test_topics_file_path(self):
        """TOPICS_FILE should be a known filename."""
        from app.bridge_state import INSTANCE_DIR, TOPICS_FILE
        assert TOPICS_FILE == INSTANCE_DIR / "previous-discussions-topics.json"


class TestPendingActionState:
    """Tests for pending action confirmation state."""

    @pytest.fixture(autouse=True)
    def _clear_pending(self):
        """Reset module-level pending-action store between tests."""
        import app.bridge_state as bs
        with bs._pending_actions_lock:
            bs._pending_actions.clear()
        yield
        with bs._pending_actions_lock:
            bs._pending_actions.clear()

    def test_set_and_get_pending_action(self):
        """Can store and retrieve a pending action."""
        import app.bridge_state as bs
        import time

        chat_id = "123456"
        action = {
            "command": "/recurring",
            "expires_at": time.time() + 100,
        }

        bs.set_pending_action(chat_id, action)
        result = bs.get_pending_action(chat_id)

        assert result is not None
        assert result["command"] == "/recurring"

    def test_get_nonexistent_action(self):
        """Getting nonexistent action returns None."""
        import app.bridge_state as bs

        result = bs.get_pending_action("nonexistent_chat")
        assert result is None

    def test_expired_action_returns_none(self):
        """Expired actions are returned as None and cleaned up."""
        import app.bridge_state as bs
        import time

        chat_id = "expired_chat"
        action = {
            "command": "/mission test",
            "expires_at": time.time() - 1,  # Already expired
        }

        bs.set_pending_action(chat_id, action)
        result = bs.get_pending_action(chat_id)

        assert result is None
        # Verify it was cleaned up
        assert bs.get_pending_action(chat_id) is None

    def test_clear_pending_action(self):
        """Clear removes a pending action."""
        import app.bridge_state as bs
        import time

        chat_id = "clear_test"
        action = {
            "command": "/status",
            "expires_at": time.time() + 100,
        }

        bs.set_pending_action(chat_id, action)
        assert bs.get_pending_action(chat_id) is not None

        bs.clear_pending_action(chat_id)
        assert bs.get_pending_action(chat_id) is None

    def test_clear_nonexistent_action(self):
        """Clearing nonexistent action is a no-op (doesn't raise)."""
        import app.bridge_state as bs

        # Should not raise
        bs.clear_pending_action("nonexistent")

    def test_replace_pending_action(self):
        """Setting a new action for a chat replaces the old one."""
        import app.bridge_state as bs
        import time

        chat_id = "replace_test"
        action1 = {"command": "/status", "expires_at": time.time() + 100}
        action2 = {"command": "/mission new", "expires_at": time.time() + 100}

        bs.set_pending_action(chat_id, action1)
        result1 = bs.get_pending_action(chat_id)
        assert result1["command"] == "/status"

        bs.set_pending_action(chat_id, action2)
        result2 = bs.get_pending_action(chat_id)
        assert result2["command"] == "/mission new"

    def test_multiple_chats_isolated(self):
        """Pending actions for different chats are isolated."""
        import app.bridge_state as bs
        import time

        chat_1 = "chat_1"
        chat_2 = "chat_2"

        action1 = {"command": "/recurring", "expires_at": time.time() + 100}
        action2 = {"command": "/mission test", "expires_at": time.time() + 100}

        bs.set_pending_action(chat_1, action1)
        bs.set_pending_action(chat_2, action2)

        assert bs.get_pending_action(chat_1)["command"] == "/recurring"
        assert bs.get_pending_action(chat_2)["command"] == "/mission test"
