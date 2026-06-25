import threading
from unittest.mock import MagicMock, patch

import pytest

import app.awake as awake
import app.command_handlers as ch


@pytest.fixture(autouse=True)
def _reset_lanes():
    # Ensure each test starts with empty lanes and no leftover live thread.
    awake._worker_threads = {"chat": None, "bg": None}
    yield
    for t in awake._worker_threads.values():
        if t is not None and t.is_alive():
            t.join(timeout=2)


def test_chat_and_bg_run_concurrently():
    """A busy chat lane must NOT prevent a bg task from starting."""
    chat_started = threading.Event()
    release_chat = threading.Event()
    bg_ran = threading.Event()

    def chat_task():
        chat_started.set()
        release_chat.wait(timeout=2)

    def bg_task():
        bg_ran.set()

    awake._run_in_worker(chat_task, lane="chat")
    assert chat_started.wait(timeout=2), "chat lane never started"

    # While chat is still blocked, bg must start immediately.
    awake._run_in_worker(bg_task, lane="bg")
    assert bg_ran.wait(timeout=2), "bg lane was blocked by busy chat lane"

    release_chat.set()


def test_busy_chat_lane_sends_busy_message():
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(timeout=2)

    with patch("app.notify.send_telegram") as mock_send:
        awake._run_in_worker(slow, lane="chat")
        assert started.wait(timeout=2)
        awake._run_in_worker(lambda: None, lane="chat")  # second chat → busy
        release.set()

    assert mock_send.called, "busy chat lane should notify the user"


def test_busy_bg_lane_is_silent():
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(timeout=2)

    with patch("app.notify.send_telegram") as mock_send:
        awake._run_in_worker(slow, lane="bg")
        assert started.wait(timeout=2)
        awake._run_in_worker(lambda: None, lane="bg")  # second bg → dropped silently
        release.set()

    assert not mock_send.called, "bg lane must not spam the chat channel when busy"


def test_unknown_lane_rejected():
    with pytest.raises(ValueError):
        awake._run_in_worker(lambda: None, lane="nope")


def test_worker_skill_dispatches_on_bg_lane():
    skill = MagicMock()
    skill.cli_skill = False
    skill.worker = True
    skill.audience = "human"

    with patch.object(ch, "_run_in_worker_cb") as mock_worker, \
         patch.object(ch, "record_usage", create=True):
        ch._dispatch_skill(skill, "review", "some args")

    assert mock_worker.called
    # lane must be the background lane, not chat
    assert mock_worker.call_args.kwargs.get("lane") == "bg"


def test_busy_bg_lane_notifies_user_initiated_worker_skill():
    """A typed worker skill dropped by a busy bg lane must not be silent.

    The bg lane stays silent for autonomous background work, but a
    user-initiated command (e.g. ``/review`` typed in chat) that lands on a
    busy lane should tell the user instead of vanishing.
    """
    skill = MagicMock()
    skill.cli_skill = False
    skill.worker = True
    skill.audience = "human"

    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(timeout=2)

    # Occupy the real bg lane so the next dispatch is dropped.
    with patch("app.notify.send_telegram"):
        assert awake._run_in_worker(slow, lane="bg") is True
        assert started.wait(timeout=2)

    # Dispatch a user-initiated worker skill through the real lane callback.
    with patch.object(ch, "_run_in_worker_cb", awake._run_in_worker), \
         patch.object(ch, "send_telegram") as mock_send, \
         patch.object(ch, "record_usage", create=True):
        ch._dispatch_skill(skill, "review", "some args")

    release.set()

    assert mock_send.called, "dropped user-initiated worker skill must notify the user"
    sent = " ".join(str(c.args[0]) for c in mock_send.call_args_list if c.args)
    assert "review" in sent
