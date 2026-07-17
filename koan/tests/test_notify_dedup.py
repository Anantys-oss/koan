"""Tests for cross-restart lifecycle-notice dedup (#2426)."""

import json

import pytest

from app import notify_dedup


@pytest.fixture
def koan_root(tmp_path):
    (tmp_path / "instance").mkdir()
    return str(tmp_path)


# --- claim_notice / release_notice ----------------------------------------


def test_first_claim_sends(koan_root):
    assert notify_dedup.claim_notice("hello", window=300, koan_root=koan_root) is True


def test_identical_claim_within_window_suppressed(koan_root):
    assert notify_dedup.claim_notice("hi", window=300, koan_root=koan_root) is True
    # Same text again inside the window → suppress.
    assert notify_dedup.claim_notice("hi", window=300, koan_root=koan_root) is False


def test_distinct_text_not_suppressed(koan_root):
    assert notify_dedup.claim_notice("a", window=300, koan_root=koan_root) is True
    assert notify_dedup.claim_notice("b", window=300, koan_root=koan_root) is True


def test_zero_window_always_sends(koan_root):
    assert notify_dedup.claim_notice("x", window=0, koan_root=koan_root) is True
    assert notify_dedup.claim_notice("x", window=0, koan_root=koan_root) is True


def test_claim_expires_after_window(koan_root, monkeypatch):
    times = iter([1000.0, 1400.0])
    monkeypatch.setattr(notify_dedup.time, "time", lambda: next(times))
    # t=1000: first send recorded.
    assert notify_dedup.claim_notice("m", window=300, koan_root=koan_root) is True
    # t=1400: 400s later, window (300s) has passed → send again.
    assert notify_dedup.claim_notice("m", window=300, koan_root=koan_root) is True


def test_release_allows_resend(koan_root):
    assert notify_dedup.claim_notice("z", window=300, koan_root=koan_root) is True
    assert notify_dedup.claim_notice("z", window=300, koan_root=koan_root) is False
    notify_dedup.release_notice("z", koan_root=koan_root)
    # After release, the notice can be sent again.
    assert notify_dedup.claim_notice("z", window=300, koan_root=koan_root) is True


def test_no_koan_root_fails_open(monkeypatch):
    monkeypatch.delenv("KOAN_ROOT", raising=False)
    assert notify_dedup.claim_notice("q", window=300, koan_root=None) is True
    assert notify_dedup.claim_notice("q", window=300, koan_root=None) is True


def test_corrupt_state_fails_open(koan_root):
    path = notify_dedup._dedup_path(notify_dedup.Path(koan_root))
    path.write_text("{not valid json")
    # Corrupt state is treated as empty — the notice sends and state is reset.
    assert notify_dedup.claim_notice("c", window=300, koan_root=koan_root) is True
    data = json.loads(path.read_text())
    assert isinstance(data, dict) and len(data) == 1


def test_prune_drops_stale_entries(koan_root, monkeypatch):
    times = iter([100.0, 100000.0])
    monkeypatch.setattr(notify_dedup.time, "time", lambda: next(times))
    notify_dedup.claim_notice("old", window=300, koan_root=koan_root)
    # Far in the future: the stale "old" entry is pruned; only "new" remains.
    notify_dedup.claim_notice("new", window=300, koan_root=koan_root)
    data = json.loads(notify_dedup._dedup_path(notify_dedup.Path(koan_root)).read_text())
    assert notify_dedup._key("old") not in data
    assert notify_dedup._key("new") in data


# --- send_telegram integration --------------------------------------------


class _FakeProvider:
    def __init__(self):
        self.sent = []

    def send_message(self, text, reply_to_message_id=0):
        self.sent.append(text)
        return True


def _install_provider(monkeypatch, provider):
    import app.messaging as messaging
    monkeypatch.setattr(messaging, "get_messaging_provider", lambda: provider)


def test_send_telegram_dedup_suppresses_repeat(koan_root, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", koan_root)
    from app import notify

    provider = _FakeProvider()
    _install_provider(monkeypatch, provider)

    msg = "🌅 Running morning ritual (Claude CLI, up to ~90s)..."
    assert notify.send_telegram(msg, dedup_window=300) is True
    assert notify.send_telegram(msg, dedup_window=300) is True  # suppressed
    # Only delivered once despite two calls.
    assert len(provider.sent) == 1


def test_send_telegram_without_dedup_sends_every_time(koan_root, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", koan_root)
    from app import notify

    provider = _FakeProvider()
    _install_provider(monkeypatch, provider)

    msg = "🛑 Shutting down — operator requested stop."
    notify.send_telegram(msg)
    notify.send_telegram(msg)
    # No dedup_window → provider sees both (no cross-restart suppression).
    assert len(provider.sent) == 2


def test_send_telegram_failed_send_releases_claim(koan_root, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", koan_root)
    from app import notify

    class _FailingProvider:
        def __init__(self):
            self.calls = 0

        def send_message(self, text, reply_to_message_id=0):
            self.calls += 1
            return False

    provider = _FailingProvider()
    _install_provider(monkeypatch, provider)

    msg = "some lifecycle notice"
    assert notify.send_telegram(msg, dedup_window=300) is False
    # A failed send released the reservation, so a retry is attempted (not
    # suppressed as a duplicate).
    assert notify.send_telegram(msg, dedup_window=300) is False
    assert provider.calls == 2


def test_send_telegram_exception_releases_claim(koan_root, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", koan_root)
    from app import notify

    class _RaisingProvider:
        def send_message(self, text, reply_to_message_id=0):
            raise RuntimeError("boom")

    _install_provider(monkeypatch, _RaisingProvider())

    msg = "🛑 Shutting down — operator requested stop."
    with pytest.raises(RuntimeError):
        notify.send_telegram(msg, dedup_window=300)
    # The exception must not leave a claimed reservation — the notice can still
    # be re-attempted within the window rather than being suppressed forever.
    assert notify_dedup.claim_notice(msg, window=300, koan_root=koan_root) is True
