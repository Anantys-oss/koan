import pytest

from app import run as koan_run

PR = "https://github.com/Org/repo/pull/713"


@pytest.fixture
def sent(monkeypatch):
    """Capture what reaches the chat sink; fail if the reformatter is used.

    End-of-mission lines are fixed templates: they must go to send_telegram
    verbatim, never through the Claude-CLI personality reformatter.
    """
    out = []
    monkeypatch.setattr("app.notify.send_telegram", lambda msg, **k: out.append(msg))

    def _reformatter(*a, **k):
        raise AssertionError("end-of-mission line went through format_and_send")

    monkeypatch.setattr("app.notify.format_and_send", _reformatter)
    monkeypatch.setattr(koan_run, "_completion_pr_url", lambda *a, **k: "")
    return out


@pytest.fixture
def normal(monkeypatch):
    monkeypatch.setattr(koan_run, "is_debug", lambda: False)


@pytest.fixture
def debug(monkeypatch):
    monkeypatch.setattr(koan_run, "is_debug", lambda: True)
    monkeypatch.setattr("app.mission_summary.get_mission_summary", lambda *a, **k: "")
    monkeypatch.setattr("app.mission_summary.get_failure_context", lambda *a, **k: "")


@pytest.mark.parametrize("title, line", [
    ("/review fix the parser", f"✅ [proj] 🔍 Reviewed {PR}"),
    ("/fix 42", f"✅ [proj] 🐞 Fixed {PR}"),
    ("/rebase x", f"✅ [proj] 🔄 Rebased {PR}"),
    ("/implement x", f"✅ [proj] 🔨 Implemented {PR}"),
])
def test_normal_mode_skill_success_is_one_fixed_line(sent, normal, title, line):
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title=title, pr_url=PR)
    assert sent == [line]


def test_same_outcome_reads_the_same_every_time(sent, normal):
    for n in (6480, 6481, 6482):
        koan_run._notify_mission_end(
            "/i", "my-toolkit", 1, 3, 0, mission_title=f"/review https://github.com/o/my-toolkit/pull/{n}",
            pr_url=f"https://github.com/o/my-toolkit/pull/{n}",
        )
    assert sent == [f"✅ [my-toolkit] 🔍 Reviewed https://github.com/o/my-toolkit/pull/{n}"
                    for n in (6480, 6481, 6482)]


def test_normal_mode_skill_success_falls_back_to_pending_url(sent, normal, monkeypatch):
    monkeypatch.setattr(koan_run, "_completion_pr_url", lambda *a, **k: PR)
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="/review fix the parser")
    assert sent == [f"✅ [proj] 🔍 Reviewed {PR}"]


def test_normal_mode_autonomous_success_is_suppressed(sent, normal):
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="")
    assert sent == []  # logged only, not pushed to bridge


def test_normal_mode_plan_success_carries_issue_url_and_detail(sent, normal):
    koan_run._notify_mission_end(
        "/i", "proj", 1, 3, 0, mission_title="/plan an idea",
        pr_url="https://github.com/o/r/issues/5", detail="Posted on #5.",
    )
    assert sent == ["✅ [proj] 🧠 Planned https://github.com/o/r/issues/5\nPosted on #5."]


def test_normal_mode_inline_plan_body_follows_the_line(sent, normal):
    koan_run._notify_mission_end(
        "/i", "proj", 1, 3, 0, mission_title="[project:proj] /plan an idea",
        detail="# Plan\n1. Do it",
    )
    assert sent == ["✅ [proj] 🧠 Planned\n# Plan\n1. Do it"]


def test_normal_mode_alias_resolves_to_skill(sent, normal):
    koan_run._notify_mission_end(
        "/i", "proj", 1, 3, 1, mission_title="/dbg https://x/issues/1", reason="boom",
    )
    assert sent == ["❌ [proj] 🔬 Debug failed: /dbg https://x/issues/1\nboom"]


def test_reported_outcome_without_url_skips_pending_guess(sent, normal, monkeypatch):
    monkeypatch.setattr(koan_run, "_completion_pr_url", lambda *a, **k: PR)
    koan_run._notify_mission_end(
        "/i", "proj", 1, 3, 0, mission_title="/plan an idea", detail="# Plan",
        reported=True,
    )
    assert sent == ["✅ [proj] 🧠 Planned\n# Plan"]


def test_skill_md_alias_resolves_to_skill(sent, normal):
    koan_run._notify_mission_end(
        "/i", "proj", 1, 3, 0, mission_title=f"/rb {PR}", pr_url=PR, reported=True,
    )
    assert sent == [f"✅ [proj] 🔄 Rebased {PR}"]


def test_normal_mode_operator_mission_success_is_one_line(sent, normal):
    # A user-queued, non-skill mission (real title, no leading slash) is
    # operator-initiated work — it should still get a minimal completion line,
    # not be silently suppressed like a background autonomous run.
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="fix the parser bug")
    assert sent == ["✅ [proj] Done: fix the parser bug"]


def test_normal_mode_skill_failure_is_one_message_with_reason(sent, normal):
    koan_run._notify_mission_end(
        "/i", "proj", 2, 3, 1, mission_title=f"/review {PR}", pr_url=PR,
        reason="PR is closed",
    )
    assert sent == [f"❌ [proj] 🔍 Review failed: {PR}\nPR is closed"]


def test_normal_mode_skill_failure_without_reason_says_so(sent, normal):
    koan_run._notify_mission_end("/i", "proj", 2, 3, 1, mission_title=f"/review {PR}", pr_url=PR)
    assert sent == [f"❌ [proj] 🔍 Review failed: {PR}\nNo reason given."]


def test_normal_mode_skill_failure_without_url_names_the_mission(sent, normal):
    koan_run._notify_mission_end("/i", "proj", 2, 3, 1, mission_title="/fix flaky test")
    assert sent == ["❌ [proj] 🐞 Fix failed: /fix flaky test\nNo reason given."]


def test_normal_mode_operator_mission_failure(sent, normal):
    koan_run._notify_mission_end("/i", "proj", 2, 3, 1, mission_title="fix the parser bug")
    assert sent == ["❌ [proj] Failed: fix the parser bug"]


def test_normal_mode_ci_failure_keeps_traffic_light(sent, normal):
    title = "/ci_check https://github.com/o/r/pull/42"
    koan_run._notify_mission_end("/i", "proj", 2, 3, 1, mission_title=title)
    assert sent == [f"🚦 [proj] Failed: {title}"]


def test_debug_mode_first_line_matches_normal_mode(sent, debug):
    koan_run._notify_mission_end("/i", "proj", 1, 3, 0, mission_title="/review x", pr_url=PR)
    assert sent == [f"✅ [proj] 🔍 Reviewed {PR}\nRun 1/3"]


def test_debug_mode_failure_does_not_claim_a_missing_reason(sent, debug):
    # Debug mode keeps the runner's own failure line, which carries the reason.
    koan_run._notify_mission_end("/i", "proj", 1, 3, 1, mission_title="/review x", pr_url=PR)
    assert sent == [f"❌ [proj] 🔍 Review failed: {PR}\nRun 1/3"]


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
