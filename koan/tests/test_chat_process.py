"""Tests for the dedicated chat process (specs/007-chat-process/).

Cover the inbox JSONL protocol (round-trip, FIFO, unconditional truncation of
malformed input) and the drain loop (FIFO handling, batch finishes on stop).
No Claude is invoked — handle_chat is a stub.
"""

from unittest.mock import patch

import pytest

from app import chat_process as cp


@pytest.fixture(autouse=True)
def _reset_stop():
    """Keep the module-level stop flag from leaking across tests."""
    cp._stop = False
    yield
    cp._stop = False


@pytest.fixture
def inbox(tmp_path):
    """Point the module's INSTANCE_DIR at a temp dir and return the inbox path."""
    with patch.object(cp, "INSTANCE_DIR", tmp_path):
        yield tmp_path / cp.CHAT_INBOX_FILE


def test_write_then_read_roundtrip(inbox):
    assert cp.write_to_inbox("hello") is True
    entries = cp.read_and_clear_inbox()
    assert [e["text"] for e in entries] == ["hello"]


def test_read_clears_the_inbox(inbox):
    cp.write_to_inbox("one")
    cp.read_and_clear_inbox()
    # Second read returns nothing — the file was truncated.
    assert cp.read_and_clear_inbox() == []
    assert inbox.stat().st_size == 0


def test_messages_are_fifo(inbox):
    for m in ["first", "second", "third"]:
        cp.write_to_inbox(m)
    entries = cp.read_and_clear_inbox()
    assert [e["text"] for e in entries] == ["first", "second", "third"]


def test_read_missing_inbox_is_empty(inbox):
    assert not inbox.exists()
    assert cp.read_and_clear_inbox() == []


def test_malformed_only_input_is_truncated_not_replayed(inbox):
    # A partial/garbage write with no valid JSON line.
    inbox.write_text("{ not json\nalso not json\n")
    entries = cp.read_and_clear_inbox()
    assert entries == []
    # Crucially: the file is cleared so it is never replayed on the next poll.
    assert inbox.stat().st_size == 0


def test_valid_and_malformed_mixed(inbox):
    inbox.write_text('{"text": "good"}\nGARBAGE\n{"text": "also good"}\n')
    entries = cp.read_and_clear_inbox()
    assert [e["text"] for e in entries] == ["good", "also good"]
    assert inbox.stat().st_size == 0


def test_blank_and_textless_lines_skipped(inbox):
    inbox.write_text('\n{"ts": 1}\n{"text": ""}\n{"text": "keep"}\n')
    entries = cp.read_and_clear_inbox()
    assert [e["text"] for e in entries] == ["keep"]


def test_has_pending_requests(inbox):
    assert cp.has_pending_requests() is False
    cp.write_to_inbox("hi")
    assert cp.has_pending_requests() is True
    cp.read_and_clear_inbox()
    assert cp.has_pending_requests() is False


def test_drain_handles_each_in_order(inbox):
    for m in ["a", "b", "c"]:
        cp.write_to_inbox(m)
    seen = []
    n = cp._drain_once(lambda t: seen.append(t))
    assert n == 3
    assert seen == ["a", "b", "c"]


def test_drain_isolates_handler_errors(inbox):
    cp.write_to_inbox("boom")
    cp.write_to_inbox("ok")
    seen = []

    def handler(t):
        if t == "boom":
            raise RuntimeError("handler blew up")
        seen.append(t)

    # One failing message must not stop the rest of the batch.
    n = cp._drain_once(handler)
    assert n == 2
    assert seen == ["ok"]


def test_write_failure_returns_false(inbox, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom)
    assert cp.write_to_inbox("hi") is False


def test_main_finishes_batch_then_stops(inbox, monkeypatch):
    """SIGTERM mid-run: the current batch finishes, then the loop exits."""
    cp.write_to_inbox("m1")
    cp.write_to_inbox("m2")
    handled = []

    # Simulate SIGTERM arriving while the first message is being handled.
    def fake_handle(text):
        handled.append(text)
        if text == "m1":
            cp._request_stop(None, None)  # request stop mid-batch

    monkeypatch.setattr(cp, "_stop", False)
    with patch("app.pid_manager.acquire_pidfile", return_value=object()), \
         patch("app.pid_manager.release_pidfile"), \
         patch("app.awake.handle_chat", side_effect=fake_handle), \
         patch.object(cp, "KOAN_ROOT", inbox.parent), \
         patch("time.sleep"):
        cp.main()

    # Both messages in the batch were handled (no read message lost), and the
    # loop exited after the batch rather than sleeping/looping again.
    assert handled == ["m1", "m2"]
    cp._stop = False  # reset module global for other tests
