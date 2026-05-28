"""Tests for outbox_manager — message queue management and delivery."""

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from app.notify import NotificationPriority, NOTIFICATION_SUPPRESSED
from app.outbox_manager import OutboxManager, parse_outbox_priority


# ---------------------------------------------------------------------------
# parse_outbox_priority (pure function)
# ---------------------------------------------------------------------------


class TestParseOutboxPriority:
    """Priority header parsing from outbox content."""

    def test_no_header_defaults_to_action(self):
        priority, content = parse_outbox_priority("Hello world")
        assert priority == NotificationPriority.ACTION
        assert content == "Hello world"

    def test_single_info_header(self):
        priority, content = parse_outbox_priority("[priority:info]\nSome update")
        assert priority == NotificationPriority.INFO
        assert content == "Some update"

    def test_single_urgent_header(self):
        priority, content = parse_outbox_priority("[priority:urgent]\nCritical!")
        assert priority == NotificationPriority.URGENT
        assert content == "Critical!"

    def test_single_warning_header(self):
        priority, content = parse_outbox_priority("[priority:warning]\nQuota low")
        assert priority == NotificationPriority.WARNING
        assert content == "Quota low"

    def test_multiple_headers_picks_highest(self):
        raw = "[priority:info]\nFirst\n[priority:urgent]\nSecond"
        priority, content = parse_outbox_priority(raw)
        assert priority == NotificationPriority.URGENT
        # Both headers stripped
        assert "[priority:" not in content

    def test_multiple_same_priority(self):
        raw = "[priority:action]\nA\n[priority:action]\nB"
        priority, content = parse_outbox_priority(raw)
        assert priority == NotificationPriority.ACTION
        assert "[priority:" not in content

    def test_header_stripped_from_content(self):
        raw = "[priority:info]\n\nHello there"
        priority, content = parse_outbox_priority(raw)
        assert priority == NotificationPriority.INFO
        assert "Hello there" in content
        assert "[priority:" not in content

    def test_empty_content(self):
        priority, content = parse_outbox_priority("")
        assert priority == NotificationPriority.ACTION
        assert content == ""


# ---------------------------------------------------------------------------
# OutboxManager
# ---------------------------------------------------------------------------


@pytest.fixture
def outbox_env(tmp_path):
    """Create a minimal outbox environment and return (manager, paths)."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    outbox_file = instance_dir / "outbox.md"
    outbox_file.write_text("")
    conv_file = instance_dir / "conversation.jsonl"
    mgr = OutboxManager(outbox_file, instance_dir, conv_file)
    return mgr, outbox_file, instance_dir


class TestOutboxManagerInit:
    """Basic construction and properties."""

    def test_outbox_file_property(self, outbox_env):
        mgr, outbox_file, _ = outbox_env
        assert mgr.outbox_file == outbox_file

    def test_staging_path(self, outbox_env):
        mgr, outbox_file, _ = outbox_env
        assert mgr.staging_path == outbox_file.parent / "outbox-sending.md"


class TestRecoverStaged:
    """Crash recovery from staging file."""

    def test_no_staging_file_is_noop(self, outbox_env):
        mgr, _, _ = outbox_env
        assert not mgr.staging_path.exists()
        mgr.recover_staged()  # should not raise

    @patch("app.outbox_manager.log")
    def test_recovers_staged_content(self, mock_log, outbox_env):
        mgr, outbox_file, _ = outbox_env
        mgr.staging_path.write_text("recovered message")
        mgr.recover_staged()
        # Content should be requeued to outbox
        assert "recovered message" in outbox_file.read_text()
        # Staging file should be cleaned up
        assert not mgr.staging_path.exists()

    @patch("app.outbox_manager.log")
    def test_empty_staging_file_deleted(self, mock_log, outbox_env):
        mgr, outbox_file, _ = outbox_env
        mgr.staging_path.write_text("   ")
        mgr.recover_staged()
        assert not mgr.staging_path.exists()
        # Empty content should not be requeued
        assert outbox_file.read_text().strip() == ""


class TestRequeue:
    """Re-append content to outbox on failed send."""

    def test_requeue_appends_content(self, outbox_env):
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("existing\n")
        mgr.requeue("new message")
        content = outbox_file.read_text()
        assert "existing" in content
        assert "new message" in content

    @patch("app.outbox_manager.log")
    def test_requeue_to_nonexistent_file_creates_it(self, mock_log, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        outbox_file = instance_dir / "outbox.md"
        # Don't create the file
        conv_file = instance_dir / "conversation.jsonl"
        mgr = OutboxManager(outbox_file, instance_dir, conv_file)
        mgr.requeue("hello")
        assert "hello" in outbox_file.read_text()


class TestWriteFailed:
    """Last-resort persistence for lost messages."""

    @patch("app.outbox_manager.log")
    def test_writes_to_failed_file(self, mock_log, outbox_env):
        mgr, _, instance_dir = outbox_env
        mgr._write_failed("lost content", RuntimeError("send error"))
        failed_file = instance_dir / "outbox-failed.md"
        assert failed_file.exists()
        content = failed_file.read_text()
        assert "lost content" in content
        assert "send error" in content

    @patch("app.outbox_manager.log")
    def test_appends_multiple_failures(self, mock_log, outbox_env):
        mgr, _, instance_dir = outbox_env
        mgr._write_failed("first", RuntimeError("err1"))
        mgr._write_failed("second", RuntimeError("err2"))
        content = (instance_dir / "outbox-failed.md").read_text()
        assert "first" in content
        assert "second" in content


class TestFlush:
    """Main flush lifecycle — read, scan, format, send."""

    @patch("app.outbox_manager.log")
    def test_flush_empty_outbox_is_noop(self, mock_log, outbox_env):
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("")
        mgr.flush()
        # No send should happen

    @patch("app.outbox_manager.log")
    def test_flush_nonexistent_outbox_is_noop(self, mock_log, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        outbox_file = instance_dir / "outbox.md"
        conv_file = instance_dir / "conversation.jsonl"
        mgr = OutboxManager(outbox_file, instance_dir, conv_file)
        mgr.flush()  # should not raise

    @patch("app.outbox_manager.OutboxManager._get_last_message_id", return_value=42)
    @patch("app.outbox_manager.save_conversation_message")
    @patch("app.outbox_manager.send_telegram", return_value=True)
    @patch("app.outbox_manager.scan_and_log")
    @patch("app.outbox_manager.log")
    def test_flush_sends_formatted_message(
        self, mock_log, mock_scan, mock_send, mock_save, mock_id, outbox_env
    ):
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("Mission done!")
        mock_scan.return_value = MagicMock(blocked=False)

        with patch.object(mgr, "_format_message", return_value="Formatted!") as mock_fmt:
            with patch.object(mgr, "_expand_github_refs", return_value="Formatted!"):
                mgr.flush()

        # Outbox should be truncated
        assert outbox_file.read_text() == ""
        # Message should be sent
        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "Formatted!"
        # Conversation should be saved
        mock_save.assert_called_once()
        # Staging file should be cleaned up
        assert not mgr.staging_path.exists()

    @patch("app.outbox_manager.send_telegram", return_value=False)
    @patch("app.outbox_manager.scan_and_log")
    @patch("app.outbox_manager.log")
    def test_flush_requeues_on_send_failure(
        self, mock_log, mock_scan, mock_send, outbox_env
    ):
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("Will fail to send")
        mock_scan.return_value = MagicMock(blocked=False)

        with patch.object(mgr, "_format_message", return_value="formatted"):
            with patch.object(mgr, "_expand_github_refs", return_value="formatted"):
                mgr.flush()

        # Content should be requeued
        assert "Will fail to send" in outbox_file.read_text()

    @patch("app.outbox_manager.send_telegram", return_value=NOTIFICATION_SUPPRESSED)
    @patch("app.outbox_manager.scan_and_log")
    @patch("app.outbox_manager.log")
    def test_flush_handles_suppressed_notification(
        self, mock_log, mock_scan, mock_send, outbox_env
    ):
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("[priority:info]\nLow priority update")
        mock_scan.return_value = MagicMock(blocked=False)

        with patch.object(mgr, "_format_message", return_value="formatted"):
            with patch.object(mgr, "_expand_github_refs", return_value="formatted"):
                mgr.flush()

        # Outbox should be cleared (not requeued)
        assert outbox_file.read_text() == ""
        # Staging should be cleaned up
        assert not mgr.staging_path.exists()

    @patch("app.outbox_manager.log")
    @patch("app.outbox_manager.scan_and_log")
    def test_flush_blocks_quarantined_content(self, mock_scan, mock_log, outbox_env):
        mgr, outbox_file, instance_dir = outbox_env
        outbox_file.write_text("KOAN_TELEGRAM_TOKEN=secret")
        mock_scan.return_value = MagicMock(blocked=True, reason="contains secrets")

        mgr.flush()

        # Should NOT send
        quarantine = instance_dir / "outbox-quarantine.md"
        assert quarantine.exists()
        assert "BLOCKED" in quarantine.read_text()
        # Staging cleaned up
        assert not mgr.staging_path.exists()

    @patch("app.outbox_manager.log")
    def test_flush_creates_staging_file_for_crash_safety(self, mock_log, outbox_env):
        """Verify that staging file exists during the slow send phase."""
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("Important message")

        staging_existed = []

        def fake_scan(content):
            # At this point, staging file should exist
            staging_existed.append(mgr.staging_path.exists())
            return MagicMock(blocked=False)

        with patch("app.outbox_manager.scan_and_log", side_effect=fake_scan):
            with patch("app.outbox_manager.send_telegram", return_value=True):
                with patch.object(mgr, "_format_message", return_value="fmt"):
                    with patch.object(mgr, "_expand_github_refs", return_value="fmt"):
                        with patch("app.outbox_manager.save_conversation_message"):
                            with patch.object(mgr, "_get_last_message_id", return_value=0):
                                mgr.flush()

        assert staging_existed == [True]


class TestFlushAsync:
    """Background thread management."""

    @patch("app.outbox_manager.log")
    def test_flush_async_starts_thread(self, mock_log, outbox_env):
        mgr, outbox_file, _ = outbox_env
        outbox_file.write_text("")

        with patch.object(mgr, "flush") as mock_flush:
            mgr.flush_async()
            # Wait for thread to complete
            if mgr._thread:
                mgr._thread.join(timeout=5)
            mock_flush.assert_called_once()

    @patch("app.outbox_manager.log")
    def test_flush_async_skips_if_already_running(self, mock_log, outbox_env):
        mgr, _, _ = outbox_env
        import threading
        import time

        # Simulate a long-running flush
        barrier = threading.Event()

        def slow_flush():
            barrier.wait(timeout=5)

        with patch.object(mgr, "flush", side_effect=slow_flush):
            mgr.flush_async()  # starts thread
            mgr.flush_async()  # should skip (thread alive)
            # Only one thread should exist
            thread = mgr._thread
            barrier.set()
            thread.join(timeout=5)


class TestFormatMessage:
    """Claude formatting with fallback."""

    @patch("app.outbox_manager.format_message", return_value="Bien formaté")
    @patch("app.outbox_manager.load_memory_context", return_value="memory")
    @patch("app.outbox_manager.load_human_prefs", return_value="prefs")
    @patch("app.outbox_manager.load_soul", return_value="soul")
    @patch("app.outbox_manager.log")
    def test_formats_with_full_context(
        self, mock_log, mock_soul, mock_prefs, mock_memory, mock_format, outbox_env
    ):
        mgr, _, _ = outbox_env
        result = mgr._format_message("raw content")
        assert result == "Bien formaté"
        mock_format.assert_called_once_with("raw content", "soul", "prefs", "memory")

    @patch("app.outbox_manager.fallback_format", return_value="fallback result")
    @patch("app.outbox_manager.load_soul", side_effect=OSError("file not found"))
    @patch("app.outbox_manager.log")
    def test_falls_back_on_os_error(self, mock_log, mock_soul, mock_fallback, outbox_env):
        mgr, _, _ = outbox_env
        result = mgr._format_message("raw")
        assert result == "fallback result"
        mock_fallback.assert_called_once_with("raw")

    @patch("app.outbox_manager.fallback_format", return_value="fallback result")
    @patch("app.outbox_manager.load_soul", side_effect=RuntimeError("unexpected"))
    @patch("app.outbox_manager.log")
    def test_falls_back_on_unexpected_error(
        self, mock_log, mock_soul, mock_fallback, outbox_env
    ):
        mgr, _, _ = outbox_env
        result = mgr._format_message("raw")
        assert result == "fallback result"


class TestExpandGitHubRefs:
    """GitHub reference expansion in formatted messages."""

    @patch("app.outbox_manager.log")
    def test_no_project_context_returns_unchanged(self, mock_log):
        with patch("app.text_utils.extract_project_from_message", return_value=None):
            result = OutboxManager._expand_github_refs("message #42", "message #42")
        assert result == "message #42"

    @patch("app.outbox_manager.log")
    def test_expands_refs_with_project_context(self, mock_log):
        with patch("app.text_utils.extract_project_from_message", return_value="koan"):
            with patch("app.projects_merged.get_github_url", return_value="https://github.com/org/koan"):
                with patch("app.text_utils.expand_github_refs", return_value="expanded") as mock_expand:
                    result = OutboxManager._expand_github_refs("msg #42", "msg #42")
        assert result == "expanded"
        mock_expand.assert_called_once_with("msg #42", "https://github.com/org/koan")

    @patch("app.outbox_manager.log")
    def test_github_url_lookup_failure_returns_unchanged(self, mock_log):
        with patch("app.text_utils.extract_project_from_message", return_value="koan"):
            with patch("app.projects_merged.get_github_url", side_effect=RuntimeError("fail")):
                result = OutboxManager._expand_github_refs("msg #42", "msg #42")
        assert result == "msg #42"

    @patch("app.outbox_manager.log")
    def test_no_github_url_returns_unchanged(self, mock_log):
        with patch("app.text_utils.extract_project_from_message", return_value="koan"):
            with patch("app.projects_merged.get_github_url", return_value=None):
                result = OutboxManager._expand_github_refs("msg #42", "msg #42")
        assert result == "msg #42"


class TestGetLastMessageId:
    """Message ID retrieval from messaging provider."""

    def test_returns_last_id(self):
        mock_provider = MagicMock()
        mock_provider.get_last_message_ids.return_value = [10, 20, 30]
        with patch("app.messaging.get_messaging_provider", return_value=mock_provider):
            result = OutboxManager._get_last_message_id()
        assert result == 30

    def test_returns_zero_on_empty_ids(self):
        mock_provider = MagicMock()
        mock_provider.get_last_message_ids.return_value = []
        with patch("app.messaging.get_messaging_provider", return_value=mock_provider):
            result = OutboxManager._get_last_message_id()
        assert result == 0

    def test_returns_zero_on_exception(self):
        with patch("app.messaging.get_messaging_provider", side_effect=RuntimeError):
            result = OutboxManager._get_last_message_id()
        assert result == 0
