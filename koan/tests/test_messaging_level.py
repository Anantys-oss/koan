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
    # Isolate from agent-loop env: KOAN_SUPPRESS_RUNNER_OUTCOME=1 would
    # suppress the send and make this assertion order/env dependent.
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    ml.notify_outcome("✅ Reviewed https://github.com/o/r/pull/1", lambda m: sent.append(m))
    assert sent == ["✅ Reviewed https://github.com/o/r/pull/1"]
    assert logged and logged[0][1].startswith("✅ Reviewed")


def test_notify_outcome_suppressed_when_agent_loop_reports(monkeypatch):
    """When the agent loop emits the canonical tracked-skill completion line,
    KOAN_SUPPRESS_RUNNER_OUTCOME=1 suppresses the runner's duplicate outcome
    line (still logged) so the user sees only one row."""
    logged, sent = [], []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: logged.append((cat, msg)))
    monkeypatch.setenv("KOAN_SUPPRESS_RUNNER_OUTCOME", "1")
    ml.notify_outcome("✅ Reviewed https://github.com/o/r/pull/1", lambda m: sent.append(m))
    assert sent == []  # duplicate suppressed
    assert logged and logged[0][1].startswith("✅ Reviewed")  # still logged


def test_notify_outcome_failure_handed_to_agent_loop_when_flag_set(monkeypatch, capsys):
    """With KOAN_SUPPRESS_RUNNER_OUTCOME=1 the agent loop sends the one failure
    message ("❌ [p] 🔍 Review failed: <url>" + reason). The runner does not send
    its own line; it prints the reason as a marker the loop reads from stdout."""
    logged, sent = [], []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: logged.append((cat, msg)))
    monkeypatch.setenv("KOAN_SUPPRESS_RUNNER_OUTCOME", "1")
    ml.notify_outcome(
        "❌ Could not review https://github.com/o/r/pull/1: lint errors",
        lambda m: sent.append(m),
    )
    assert sent == []
    assert capsys.readouterr().out == "KOAN_OUTCOME_OK: 0\nKOAN_OUTCOME_REASON: lint errors\n"
    assert logged and logged[0][1].startswith("❌ Could not review")


def test_notify_outcome_failure_sent_when_flag_unset(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: None)
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    line = "❌ Rebase failed https://github.com/o/r/pull/1: conflict"
    ml.notify_outcome(line, lambda m: sent.append(m))
    assert sent == [line]
    assert "KOAN_OUTCOME_REASON" not in capsys.readouterr().out


@pytest.mark.parametrize("line, reason", [
    ("❌ Rebase failed https://github.com/o/r/pull/1: conflict in a.py", "conflict in a.py"),
    ("🚦 CI failed https://github.com/o/r/pull/1: lint: E501", "lint: E501"),
    ("❌ Squash failed https://x/pull/1: line one\n  line two", "line one line two"),
    ("❌ Something broke", "Something broke"),
])
def test_failure_reason(line, reason):
    assert ml.failure_reason(line) == reason


def test_failure_reason_is_capped():
    reason = ml.failure_reason("❌ X failed https://x: " + "a" * 2000)
    assert len(reason) == 500 and reason.endswith("…")


def test_report_outcome_sends_fixed_template(monkeypatch):
    sent = []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: None)
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    ml.report_outcome(
        "squash", True, project="proj", url="https://x/pull/1",
        detail="line one\nline two", send_fn=sent.append,
    )
    ml.report_outcome("squash", False, project="proj", url="https://x/pull/1",
                      reason="conflict\n in a.py", send_fn=sent.append)
    ml.report_outcome("squash", False, url="https://x/pull/1", send_fn=sent.append)
    assert sent == [
        "✅ [proj] 🗜️ Squashed https://x/pull/1\nline one\nline two",
        "❌ [proj] 🗜️ Squash failed: https://x/pull/1\nconflict in a.py",
        "❌ 🗜️ Squash failed: https://x/pull/1\nNo reason given.",
    ]


def test_report_outcome_hands_over_when_loop_owns_it(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: None)
    monkeypatch.setenv("KOAN_SUPPRESS_RUNNER_OUTCOME", "1")
    ml.report_outcome("plan", True, url="https://x/issues/2", detail="# Plan\n1. Do it",
                      send_fn=sent.append)
    assert sent == []
    assert capsys.readouterr().out == (
        "KOAN_OUTCOME_OK: 1\n"
        "KOAN_OUTCOME_URL: https://x/issues/2\n"
        "KOAN_OUTCOME_DETAIL: # Plan\n"
        "KOAN_OUTCOME_DETAIL: 1. Do it\n"
    )


def test_report_outcome_redacts_secrets(monkeypatch):
    sent = []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: None)
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    token = "ghp_" + "A" * 36
    ml.report_outcome("review", False, project="p", url=f"https://x/pull/1?token={token}",
                      reason=f"gh failed with {token}", send_fn=sent.append)
    assert len(sent) == 1 and token not in sent[0]


def test_hand_over_detail_only_when_loop_owns_outcome(monkeypatch, capsys):
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    ml.hand_over_detail("note")
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("KOAN_SUPPRESS_RUNNER_OUTCOME", "1")
    ml.hand_over_detail("one\ntwo")
    assert capsys.readouterr().out == "KOAN_OUTCOME_DETAIL: one\nKOAN_OUTCOME_DETAIL: two\n"


def test_report_failure_unless_reported(monkeypatch):
    sent = []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: None)
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    ml.report_failure_unless_reported("fix", "boom", project="p", send_fn=sent.append)
    ml.report_failure_unless_reported("fix", "again", project="p", send_fn=sent.append)
    assert sent == ["❌ [p] 🐞 Fix failed\nboom"]


def test_notify_outcome_not_suppressed_when_flag_unset(monkeypatch):
    sent = []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: None)
    monkeypatch.delenv("KOAN_SUPPRESS_RUNNER_OUTCOME", raising=False)
    ml.notify_outcome("✅ Recreated https://github.com/o/r/pull/2", lambda m: sent.append(m))
    assert sent == ["✅ Recreated https://github.com/o/r/pull/2"]


def test_notify_outcome_multiline_success_still_sent_when_flag_set(monkeypatch):
    """A multi-line ✅ outcome carries content the canonical agent-loop line does
    not (e.g. /plan's no-tracker '✅ Plan generated inline:\\n\\n<body>'). The
    suppression flag must NOT drop it, otherwise the plan vanishes from chat in
    normal mode (#2153 regression)."""
    logged, sent = [], []
    monkeypatch.setattr(ml, "_log", lambda cat, msg: logged.append((cat, msg)))
    monkeypatch.setenv("KOAN_SUPPRESS_RUNNER_OUTCOME", "1")
    body = "✅ Plan generated inline:\n\n# Plan\n1. Do the thing\n2. Verify it"
    ml.notify_outcome(body, lambda m: sent.append(m))
    assert sent == [body]  # content-bearing line reaches chat
    assert logged and logged[0][1] == body


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
