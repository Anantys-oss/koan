"""Tests for focus_manager.py — focus mode state management."""

import json
import os
import subprocess
import sys
import time

import pytest


class TestFocusState:
    """Test FocusState dataclass."""

    def test_expires_at(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.expires_at == 4600

    def test_is_expired_before_expiry(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.is_expired(now=2000) is False

    def test_is_expired_after_expiry(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.is_expired(now=5000) is True

    def test_is_expired_at_exact_boundary(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.is_expired(now=4600) is True

    def test_remaining_seconds(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.remaining_seconds(now=2000) == 2600

    def test_remaining_seconds_when_expired(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.remaining_seconds(now=5000) == 0

    def test_remaining_display_hours_and_minutes(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=18000, reason="missions")
        # 18000s = 5h, at now=1000 → 5h remaining
        assert state.remaining_display(now=1000) == "5h00m"

    def test_remaining_display_minutes_only(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        # At now=3100, remaining = 1500s = 25m
        assert state.remaining_display(now=3100) == "25m"

    def test_remaining_display_expired(self):
        from app.focus_manager import FocusState

        state = FocusState(activated_at=1000, duration=3600, reason="missions")
        assert state.remaining_display(now=5000) == "expired"

    def test_remaining_display_mixed(self):
        from app.focus_manager import FocusState

        # 2h30m remaining
        state = FocusState(activated_at=0, duration=9000, reason="missions")
        assert state.remaining_display(now=0) == "2h30m"


class TestCheckFocusAsBooleanCheck:
    """Test check_focus used as a boolean-style is-focused check."""

    def test_not_focused_when_no_file(self, tmp_path):
        from app.focus_manager import check_focus

        assert check_focus(str(tmp_path)) is None

    def test_focused_when_active(self, tmp_path):
        from app.focus_manager import check_focus, create_focus

        create_focus(str(tmp_path), duration=3600)
        assert check_focus(str(tmp_path)) is not None

    def test_not_focused_when_expired(self, tmp_path):
        from app.focus_manager import check_focus

        now = int(time.time())
        data = {"activated_at": now - 7200, "duration": 3600, "reason": "missions"}
        (tmp_path / ".koan-focus").write_text(json.dumps(data))
        assert check_focus(str(tmp_path)) is None

    def test_auto_cleans_expired_file(self, tmp_path):
        from app.focus_manager import check_focus

        now = int(time.time())
        data = {"activated_at": now - 7200, "duration": 3600, "reason": "missions"}
        focus_file = tmp_path / ".koan-focus"
        focus_file.write_text(json.dumps(data))
        check_focus(str(tmp_path))
        assert not focus_file.exists()


class TestGetFocusState:
    """Test get_focus_state function."""

    def test_returns_none_when_no_file(self, tmp_path):
        from app.focus_manager import get_focus_state

        assert get_focus_state(str(tmp_path)) is None

    def test_reads_focus_state(self, tmp_path):
        from app.focus_manager import get_focus_state

        data = {"activated_at": 1000, "duration": 3600, "reason": "missions"}
        (tmp_path / ".koan-focus").write_text(json.dumps(data))

        state = get_focus_state(str(tmp_path))
        assert state is not None
        assert state.activated_at == 1000
        assert state.duration == 3600
        assert state.reason == "missions"

    def test_returns_none_on_invalid_json(self, tmp_path):
        from app.focus_manager import get_focus_state

        (tmp_path / ".koan-focus").write_text("not json")
        assert get_focus_state(str(tmp_path)) is None

    def test_returns_none_on_empty_file(self, tmp_path):
        from app.focus_manager import get_focus_state

        (tmp_path / ".koan-focus").write_text("")
        assert get_focus_state(str(tmp_path)) is None

    def test_defaults_missing_fields(self, tmp_path):
        from app.focus_manager import get_focus_state, DEFAULT_FOCUS_DURATION

        data = {"activated_at": 5000}
        (tmp_path / ".koan-focus").write_text(json.dumps(data))

        state = get_focus_state(str(tmp_path))
        assert state is not None
        assert state.duration == DEFAULT_FOCUS_DURATION
        assert state.reason == ""


class TestCreateFocus:
    """Test create_focus function."""

    def test_creates_focus_file(self, tmp_path):
        from app.focus_manager import create_focus

        state = create_focus(str(tmp_path), duration=7200, reason="deep work")
        assert (tmp_path / ".koan-focus").exists()
        assert state.duration == 7200
        assert state.reason == "deep work"

    def test_creates_with_default_duration(self, tmp_path):
        from app.focus_manager import create_focus, DEFAULT_FOCUS_DURATION

        state = create_focus(str(tmp_path))
        assert state.duration == DEFAULT_FOCUS_DURATION
        assert state.reason == "missions"

    def test_file_contains_valid_json(self, tmp_path):
        from app.focus_manager import create_focus

        create_focus(str(tmp_path), duration=3600)
        data = json.loads((tmp_path / ".koan-focus").read_text())
        assert "activated_at" in data
        assert data["duration"] == 3600
        assert data["reason"] == "missions"

    def test_overwrites_existing_focus(self, tmp_path):
        from app.focus_manager import create_focus, get_focus_state

        create_focus(str(tmp_path), duration=3600, reason="first")
        create_focus(str(tmp_path), duration=7200, reason="second")
        state = get_focus_state(str(tmp_path))
        assert state.duration == 7200
        assert state.reason == "second"


class TestRemoveFocus:
    """Test remove_focus function."""

    def test_removes_focus_file(self, tmp_path):
        from app.focus_manager import create_focus, remove_focus

        create_focus(str(tmp_path))
        remove_focus(str(tmp_path))
        assert not (tmp_path / ".koan-focus").exists()

    def test_noop_when_no_file(self, tmp_path):
        from app.focus_manager import remove_focus

        remove_focus(str(tmp_path))  # Should not raise


class TestSignalLockUsage:
    """Test that focus operations use signal_lock for cross-process safety."""

    def test_create_focus_uses_signal_lock(self, tmp_path):
        from unittest.mock import patch

        from app.focus_manager import create_focus

        with patch("app.utils.signal_lock") as mock_lock:
            create_focus(str(tmp_path), duration=3600)
            assert mock_lock.call_count == 1
            lock_path = str(mock_lock.call_args_list[0][0][0])
            assert lock_path.endswith(".koan-focus")

    def test_remove_focus_uses_signal_lock(self, tmp_path):
        from unittest.mock import patch

        from app.focus_manager import remove_focus

        with patch("app.utils.signal_lock") as mock_lock:
            remove_focus(str(tmp_path))
            assert mock_lock.call_count == 1
            lock_path = str(mock_lock.call_args_list[0][0][0])
            assert lock_path.endswith(".koan-focus")

    def test_check_focus_uses_signal_lock(self, tmp_path):
        from unittest.mock import patch

        from app.focus_manager import check_focus

        with patch("app.utils.signal_lock") as mock_lock:
            check_focus(str(tmp_path))
            assert mock_lock.call_count == 1
            lock_path = str(mock_lock.call_args_list[0][0][0])
            assert lock_path.endswith(".koan-focus")

    def test_lock_file_created_by_create_focus(self, tmp_path):
        from app.focus_manager import create_focus

        create_focus(str(tmp_path), duration=3600)
        assert (tmp_path / ".koan-focus").exists()


class TestCheckFocus:
    """Test check_focus function."""

    def test_returns_state_when_active(self, tmp_path):
        from app.focus_manager import check_focus, create_focus

        create_focus(str(tmp_path), duration=3600)
        state = check_focus(str(tmp_path))
        assert state is not None
        assert state.duration == 3600

    def test_returns_none_when_no_file(self, tmp_path):
        from app.focus_manager import check_focus

        assert check_focus(str(tmp_path)) is None

    def test_returns_none_and_cleans_up_when_expired(self, tmp_path):
        from app.focus_manager import check_focus

        now = int(time.time())
        data = {"activated_at": now - 7200, "duration": 3600, "reason": "missions"}
        (tmp_path / ".koan-focus").write_text(json.dumps(data))

        result = check_focus(str(tmp_path))
        assert result is None
        assert not (tmp_path / ".koan-focus").exists()


class TestParseDuration:
    """Test parse_duration function."""

    def test_hours_suffix(self):
        from app.focus_manager import parse_duration

        assert parse_duration("5h") == 18000

    def test_minutes_suffix(self):
        from app.focus_manager import parse_duration

        assert parse_duration("30m") == 1800

    def test_hours_and_minutes(self):
        from app.focus_manager import parse_duration

        assert parse_duration("2h30m") == 9000

    def test_bare_number_as_hours(self):
        from app.focus_manager import parse_duration

        assert parse_duration("3") == 10800

    def test_fractional_hours(self):
        from app.focus_manager import parse_duration

        assert parse_duration("1.5") == 5400

    def test_empty_string(self):
        from app.focus_manager import parse_duration

        assert parse_duration("") is None

    def test_invalid_string(self):
        from app.focus_manager import parse_duration

        assert parse_duration("abc") is None

    def test_whitespace_stripped(self):
        from app.focus_manager import parse_duration

        assert parse_duration("  2h  ") == 7200

    def test_zero_duration(self):
        from app.focus_manager import parse_duration

        assert parse_duration("0") is None

    def test_hours_with_minutes_suffix(self):
        from app.focus_manager import parse_duration

        assert parse_duration("1h15m") == 4500


class TestFocusManagerCLI:
    """Test CLI interface."""

    def test_check_when_focused(self, tmp_path):
        from app.focus_manager import create_focus

        create_focus(str(tmp_path), duration=3600)
        result = subprocess.run(
            [sys.executable, "-m", "app.focus_manager", "check", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        assert result.returncode == 0
        assert "m" in result.stdout  # Should contain remaining time

    def test_check_when_not_focused(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "app.focus_manager", "check", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        assert result.returncode == 1

    def test_status_when_focused(self, tmp_path):
        from app.focus_manager import create_focus

        create_focus(str(tmp_path), duration=3600)
        result = subprocess.run(
            [sys.executable, "-m", "app.focus_manager", "status", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["focused"] is True
        assert "remaining" in data

    def test_status_when_not_focused(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "app.focus_manager", "status", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["focused"] is False

    def test_unknown_command(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "app.focus_manager", "bogus", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        assert result.returncode == 1


class TestFocusSkillHandler:
    """Test the /focus skill handler."""

    def _make_ctx(self, tmp_path, command_name="focus", args=""):
        """Create a minimal skill context."""

        class FakeCtx:
            pass

        ctx = FakeCtx()
        ctx.koan_root = tmp_path
        ctx.instance_dir = tmp_path / "instance"
        ctx.command_name = command_name
        ctx.args = args
        return ctx

    def test_focus_activates(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "Focus mode ON" in result
        assert (tmp_path / ".koan-focus").exists()

    def test_focus_with_duration(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path, args="3h")
        result = handle(ctx)
        assert "Focus mode ON" in result
        assert "3h" in result

    def test_focus_with_invalid_duration(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path, args="xyz")
        result = handle(ctx)
        assert "Invalid duration" in result
        assert not (tmp_path / ".koan-focus").exists()

    def test_unfocus_when_focused(self, tmp_path):
        from app.focus_manager import create_focus
        from skills.core.focus.handler import handle

        create_focus(str(tmp_path))
        ctx = self._make_ctx(tmp_path, command_name="unfocus")
        result = handle(ctx)
        assert "Focus mode OFF" in result
        assert not (tmp_path / ".koan-focus").exists()

    def test_unfocus_when_not_focused(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path, command_name="unfocus")
        result = handle(ctx)
        assert "Not in focus mode" in result

    def test_focus_with_minutes(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path, args="90m")
        result = handle(ctx)
        assert "Focus mode ON" in result
        assert "1h30m" in result

    def test_focus_with_hours_and_minutes(self, tmp_path):
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path, args="2h30m")
        result = handle(ctx)
        assert "Focus mode ON" in result

    def test_focus_default_duration(self, tmp_path):
        from app.focus_manager import get_focus_state, DEFAULT_FOCUS_DURATION
        from skills.core.focus.handler import handle

        ctx = self._make_ctx(tmp_path)
        handle(ctx)
        state = get_focus_state(str(tmp_path))
        assert state.duration == DEFAULT_FOCUS_DURATION


class TestPromptBuilderFocusIntegration:
    """Test focus mode integration in prompt_builder."""

    def test_focus_section_when_active(self, tmp_path, monkeypatch):
        from app.focus_manager import create_focus

        instance = str(tmp_path / "instance")
        os.makedirs(instance, exist_ok=True)
        koan_root = str(tmp_path)
        create_focus(koan_root, duration=3600)

        from app.prompt_builder import _get_focus_section

        section = _get_focus_section(instance)
        assert "Focus Mode (ACTIVE" in section
        assert "remaining" in section
        assert "EXCLUSIVELY" in section

    def test_focus_section_when_not_active(self, tmp_path):
        instance = str(tmp_path / "instance")
        os.makedirs(instance, exist_ok=True)

        from app.prompt_builder import _get_focus_section

        section = _get_focus_section(instance)
        assert section == ""

    def test_focus_section_when_expired(self, tmp_path):
        import json

        instance = str(tmp_path / "instance")
        os.makedirs(instance, exist_ok=True)
        now = int(time.time())
        data = {"activated_at": now - 7200, "duration": 3600, "reason": "missions"}
        (tmp_path / ".koan-focus").write_text(json.dumps(data))

        from app.prompt_builder import _get_focus_section

        section = _get_focus_section(instance)
        assert section == ""


class TestStatusHandlerFocusIntegration:
    """Test focus mode in /status output."""

    def _make_ctx(self, tmp_path):
        """Create minimal status skill context."""

        class FakeCtx:
            pass

        ctx = FakeCtx()
        ctx.koan_root = tmp_path
        ctx.instance_dir = tmp_path / "instance"
        ctx.command_name = "status"
        ctx.args = ""
        os.makedirs(ctx.instance_dir, exist_ok=True)
        return ctx

    def test_status_shows_focus_when_active(self, tmp_path):
        from app.focus_manager import create_focus
        from skills.core.status.handler import _handle_status

        create_focus(str(tmp_path), duration=3600)
        ctx = self._make_ctx(tmp_path)
        result = _handle_status(ctx)
        assert "Focus" in result
        assert "missions only" in result

    def test_status_no_focus_when_inactive(self, tmp_path):
        from skills.core.status.handler import _handle_status

        ctx = self._make_ctx(tmp_path)
        result = _handle_status(ctx)
        assert "Focus" not in result


