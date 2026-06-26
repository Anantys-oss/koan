from app import run as koan_run


def test_normal_mode_skill_success_is_one_line(monkeypatch):
    sent = []
    monkeypatch.setattr(koan_run, "_notify", lambda inst, msg: sent.append(msg))
    monkeypatch.setattr(koan_run, "is_debug", lambda: False)
    monkeypatch.setattr(
        koan_run, "_completion_pr_url",
        lambda *a, **k: "https://github.com/Org/repo/pull/713",
    )
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="/review fix the parser")
    assert sent == ["✅ [proj] 🔍 Reviewed https://github.com/Org/repo/pull/713"]


def test_normal_mode_autonomous_success_is_suppressed(monkeypatch):
    sent = []
    monkeypatch.setattr(koan_run, "_notify", lambda inst, msg: sent.append(msg))
    monkeypatch.setattr(koan_run, "is_debug", lambda: False)
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="")
    assert sent == []  # logged only, not pushed to bridge


def test_normal_mode_operator_mission_success_is_one_line(monkeypatch):
    # A user-queued, non-skill mission (real title, no leading slash) is
    # operator-initiated work — it should still get a minimal completion line,
    # not be silently suppressed like a background autonomous run.
    sent = []
    monkeypatch.setattr(koan_run, "_notify", lambda inst, msg: sent.append(msg))
    monkeypatch.setattr(koan_run, "is_debug", lambda: False)
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="fix the parser bug")
    assert sent == ["✅ [proj] Done: fix the parser bug"]


def test_normal_mode_skill_success_prefers_threaded_pr_url(monkeypatch):
    # The PR URL captured during post-mission processing (pending.md already
    # deleted by then) is threaded in via pr_url and must win over a re-read.
    sent = []
    monkeypatch.setattr(koan_run, "_notify", lambda inst, msg: sent.append(msg))
    monkeypatch.setattr(koan_run, "is_debug", lambda: False)
    monkeypatch.setattr(koan_run, "_completion_pr_url", lambda *a, **k: "")
    koan_run._notify_mission_end(
        "/i", "proj", 1, 3, 0,
        mission_title="/review fix the parser",
        pr_url="https://github.com/Org/repo/pull/713",
    )
    assert sent == ["✅ [proj] 🔍 Reviewed https://github.com/Org/repo/pull/713"]


def test_normal_mode_failure_still_shown(monkeypatch):
    sent = []
    monkeypatch.setattr(koan_run, "_notify", lambda inst, msg: sent.append(msg))
    monkeypatch.setattr(koan_run, "is_debug", lambda: False)
    koan_run._notify_mission_end("/i", "proj", 2, 3, 1, mission_title="/fix flaky test")
    assert sent and sent[0].startswith("❌ [proj]")


def test_debug_mode_keeps_verbose_summary(monkeypatch):
    sent = []
    monkeypatch.setattr(koan_run, "_notify", lambda inst, msg: sent.append(msg))
    monkeypatch.setattr(koan_run, "is_debug", lambda: True)
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="/review x")
    assert sent and "Run 1/3" in sent[0]


# --- Phase 4: per-notification gating + aggregate ---

from app import loop_manager  # noqa: E402


def test_per_mention_gated_in_normal_mode(monkeypatch):
    sent = []
    monkeypatch.setattr(loop_manager, "is_debug", lambda: False)
    import app.notify as notify
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: sent.append(a))
    notif = {"repository": {"full_name": "Org/repo"},
             "subject": {"title": "t", "type": "Issue", "url": ""},
             "_koan_command": "fix", "_koan_author": "bob"}
    loop_manager._notify_mission_from_mention(notif)
    assert sent == []  # suppressed in normal mode (logged only)


def test_per_mention_sent_in_debug_mode(monkeypatch):
    sent = []
    monkeypatch.setattr(loop_manager, "is_debug", lambda: True)
    import app.notify as notify
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: sent.append(a))
    notif = {"repository": {"full_name": "Org/repo"},
             "subject": {"title": "t", "type": "Issue", "url": ""},
             "_koan_command": "fix", "_koan_author": "bob"}
    loop_manager._notify_mission_from_mention(notif)
    assert len(sent) == 1


def test_github_aggregate_emitted_in_normal_mode(monkeypatch):
    sent = []
    monkeypatch.setattr(loop_manager, "is_debug", lambda: False)
    loop_manager._emit_queued_aggregate("GitHub", 12, lambda m: sent.append(m))
    assert sent == ["📬 GitHub: 12 new missions queued."]


def test_no_aggregate_when_zero(monkeypatch):
    sent = []
    monkeypatch.setattr(loop_manager, "is_debug", lambda: False)
    loop_manager._emit_queued_aggregate("GitHub", 0, lambda m: sent.append(m))
    assert sent == []


def test_no_aggregate_in_debug_mode(monkeypatch):
    sent = []
    monkeypatch.setattr(loop_manager, "is_debug", lambda: True)
    loop_manager._emit_queued_aggregate("GitHub", 5, lambda m: sent.append(m))
    assert sent == []


def test_singular_aggregate_label(monkeypatch):
    sent = []
    monkeypatch.setattr(loop_manager, "is_debug", lambda: False)
    loop_manager._emit_queued_aggregate("Jira", 1, lambda m: sent.append(m))
    assert sent == ["📬 Jira: 1 new mission queued."]
