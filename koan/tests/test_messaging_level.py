import types

import pytest

from app import messaging_level as ml


@pytest.fixture(autouse=True)
def _reset_messaging_cache():
    """Reset the shared resolved-level memoization cache around each test.

    get_messaging_level() memoizes with a 5s TTL, so without this the resolved
    level bleeds between tests and makes the precedence assertions order-dependent.
    """
    ml._invalidate_cache()
    yield
    ml._invalidate_cache()


def test_default_is_normal(tmp_path, monkeypatch):
    monkeypatch.delenv("KOAN_MESSAGING_LEVEL", raising=False)
    monkeypatch.setattr(ml, "_state_path", lambda: tmp_path / ".koan-messaging-level")
    monkeypatch.setattr(ml, "get_configured_messaging_level", lambda: "normal")
    assert ml.get_messaging_level() == "normal"
    assert ml.is_debug() is False


def test_env_overrides_everything(tmp_path, monkeypatch):
    (tmp_path / ".koan-messaging-level").write_text("normal")
    monkeypatch.setattr(ml, "_state_path", lambda: tmp_path / ".koan-messaging-level")
    monkeypatch.setenv("KOAN_MESSAGING_LEVEL", "debug")
    assert ml.get_messaging_level() == "debug"


def test_state_file_overrides_config(tmp_path, monkeypatch):
    monkeypatch.delenv("KOAN_MESSAGING_LEVEL", raising=False)
    (tmp_path / ".koan-messaging-level").write_text("debug\n")
    monkeypatch.setattr(ml, "_state_path", lambda: tmp_path / ".koan-messaging-level")
    monkeypatch.setattr(ml, "get_configured_messaging_level", lambda: "normal")
    assert ml.get_messaging_level() == "debug"


def test_unknown_value_coerces_to_normal(monkeypatch):
    monkeypatch.setenv("KOAN_MESSAGING_LEVEL", "loud")
    assert ml.get_messaging_level() == "normal"


def test_debug_only_always_logs_but_sends_only_in_debug(monkeypatch):
    logged, sent = [], []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: logged.append((cat, msg)))
    monkeypatch.setattr(ml, "is_debug", lambda: False)
    ml.debug_only("hello", lambda: sent.append("x"), log_category="github")
    assert logged == [("github", "hello")] and sent == []
    monkeypatch.setattr(ml, "is_debug", lambda: True)
    ml.debug_only("hi", lambda: sent.append("y"))
    assert sent == ["y"]


def test_set_and_clear_override(tmp_path, monkeypatch):
    monkeypatch.delenv("KOAN_MESSAGING_LEVEL", raising=False)
    state = tmp_path / ".koan-messaging-level"
    monkeypatch.setattr(ml, "_state_path", lambda: state)
    monkeypatch.setattr(ml, "get_configured_messaging_level", lambda: "normal")
    assert ml.set_messaging_level("debug") == "debug"
    assert ml.get_messaging_level() == "debug"
    ml.clear_override()
    assert ml.get_messaging_level() == "normal"


def test_progress_notify_logs_always_sends_only_in_debug(monkeypatch):
    logged, sent = [], []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: logged.append((cat, msg)))
    monkeypatch.setattr(ml, "is_debug", lambda: False)
    notify = ml.progress_notify(lambda m: sent.append(m), log_category="review")
    notify("Reviewing PR #1...")
    assert logged == [("review", "Reviewing PR #1...")]
    assert sent == []  # suppressed under normal

    monkeypatch.setattr(ml, "is_debug", lambda: True)
    notify("Posting review on PR #1...")
    assert sent == ["Posting review on PR #1..."]  # forwarded under debug


def test_notify_outcome_always_logs_and_sends(monkeypatch):
    logged, sent = [], []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: logged.append((cat, msg)))
    monkeypatch.setattr(ml, "is_debug", lambda: False)  # even under normal
    ml.notify_outcome("✅ Reviewed https://github.com/o/r/pull/1", lambda m: sent.append(m))
    assert sent == ["✅ Reviewed https://github.com/o/r/pull/1"]
    assert logged and logged[0][1].startswith("✅ Reviewed")


# --- Phase 2: skill handler ---


def _ctx(args="", command_name="messaging_level"):
    return types.SimpleNamespace(args=args, command_name=command_name)


def test_skill_shows_current_when_no_args(monkeypatch):
    from skills.core.messaging_level import handler
    monkeypatch.setattr(handler.ml, "get_messaging_level", lambda: "normal")
    out = handler.handle(_ctx(""))
    assert "normal" in out.lower()


def test_skill_sets_debug(monkeypatch):
    from skills.core.messaging_level import handler
    stored = {}
    monkeypatch.setattr(
        handler.ml, "set_messaging_level",
        lambda lvl: stored.setdefault("l", lvl) or lvl,
    )
    out = handler.handle(_ctx("debug"))
    assert stored["l"] == "debug" and "debug" in out.lower()


def test_skill_rejects_unknown(monkeypatch):
    from skills.core.messaging_level import handler
    out = handler.handle(_ctx("loud"))
    assert "debug" in out.lower() and "normal" in out.lower()  # usage hint


# --- Phase 5: one-time startup notice ---


def test_notice_sent_once(tmp_path, monkeypatch):
    from app import startup_manager
    sent = []
    sentinel = tmp_path / ".messaging-level-notice-sent"
    monkeypatch.setattr(startup_manager, "_messaging_notice_sentinel", lambda inst: sentinel)
    monkeypatch.setattr(startup_manager, "get_configured_messaging_level_explicit", lambda: None)
    monkeypatch.setattr(startup_manager, "_notify_raw", lambda inst, msg: sent.append(msg))
    startup_manager.maybe_send_messaging_level_notice("inst")
    startup_manager.maybe_send_messaging_level_notice("inst")  # second call no-op
    assert len(sent) == 1 and sentinel.exists()


def test_notice_skipped_when_explicitly_configured(tmp_path, monkeypatch):
    from app import startup_manager
    sent = []
    monkeypatch.setattr(startup_manager, "_messaging_notice_sentinel", lambda inst: tmp_path / ".s")
    monkeypatch.setattr(startup_manager, "get_configured_messaging_level_explicit", lambda: "debug")
    monkeypatch.setattr(startup_manager, "_notify_raw", lambda inst, msg: sent.append(msg))
    startup_manager.maybe_send_messaging_level_notice("inst")
    assert sent == []
