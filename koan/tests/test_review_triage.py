"""Behavior tests for deterministic triage enforcement (spec 010, US6)."""

from app.review_reconcile import PRE_EXISTING_PREFIX
from app.review_triage import derive_lgtm, enforce_pre_existing


def _fc(severity="warning", title="issue", file="a.py"):
    return {"file": file, "line_start": 1, "severity": severity, "title": title}


def _rd(*comments, lgtm=False):
    return {"file_comments": list(comments),
            "review_summary": {"lgtm": lgtm, "checklist": []}}


class TestDeriveLgtm:
    def test_blocking_when_warning_present(self):
        assert derive_lgtm(_rd(_fc(severity="warning"))) is False

    def test_blocking_when_critical_present(self):
        assert derive_lgtm(_rd(_fc(severity="critical"))) is False

    def test_lgtm_when_only_suggestions(self):
        assert derive_lgtm(_rd(_fc(severity="suggestion"))) is True

    def test_lgtm_when_empty(self):
        assert derive_lgtm(_rd()) is True

    def test_malformed_defaults_true(self):
        assert derive_lgtm({}) is True
        assert derive_lgtm(None) is True


class TestEnforcePreExisting:
    def test_noncritical_pre_existing_demoted_to_suggestion(self):
        rd = _rd(_fc(severity="warning",
                     title=f"{PRE_EXISTING_PREFIX} long-standing style issue"))
        summary = enforce_pre_existing(rd)
        assert summary["demoted"] == 1
        assert rd["file_comments"][0]["severity"] == "suggestion"

    def test_critical_pre_existing_keeps_severity(self):
        rd = _rd(_fc(severity="critical",
                     title=f"{PRE_EXISTING_PREFIX} pre-existing SQL injection"))
        summary = enforce_pre_existing(rd)
        assert summary["critical_labeled"] == 1
        assert rd["file_comments"][0]["severity"] == "critical"

    def test_untagged_finding_untouched(self):
        rd = _rd(_fc(severity="warning", title="new bug the PR introduced"))
        summary = enforce_pre_existing(rd)
        assert summary == {"demoted": 0, "critical_labeled": 0}
        assert rd["file_comments"][0]["severity"] == "warning"

    def test_prefix_normalized_to_single_leading(self):
        rd = _rd(_fc(severity="suggestion",
                     title=f"{PRE_EXISTING_PREFIX} nit {PRE_EXISTING_PREFIX}"))
        enforce_pre_existing(rd)
        title = rd["file_comments"][0]["title"]
        assert title.count(PRE_EXISTING_PREFIX) == 1
        assert title.startswith(PRE_EXISTING_PREFIX)

    def test_demotion_flips_lgtm_when_last_blocker_removed(self):
        # A single pre-existing warning was blocking; demoting it -> merge-ready.
        rd = _rd(_fc(severity="warning",
                     title=f"{PRE_EXISTING_PREFIX} old smell"), lgtm=False)
        enforce_pre_existing(rd)
        assert rd["review_summary"]["lgtm"] is True

    def test_demotion_keeps_lgtm_false_if_other_blocker_remains(self):
        rd = _rd(
            _fc(severity="warning", title=f"{PRE_EXISTING_PREFIX} old smell"),
            _fc(severity="critical", title="real new blocker"),
            lgtm=False,
        )
        enforce_pre_existing(rd)
        assert rd["review_summary"]["lgtm"] is False

    def test_fail_open_on_malformed(self):
        assert enforce_pre_existing({}) == {"demoted": 0, "critical_labeled": 0}
        assert enforce_pre_existing(None) == {"demoted": 0, "critical_labeled": 0}
