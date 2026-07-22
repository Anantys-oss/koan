"""Behavior tests for the re-review freeze (spec 010, US1, FR-003)."""

from app.review_reconcile import PRE_EXISTING_PREFIX, compute_freeze


def _fc(file="a.py", line=10, severity="warning", title="issue", comment=""):
    return {"file": file, "line_start": line, "line_end": line,
            "severity": severity, "title": title, "comment": comment}


def _rd(*comments):
    return {"file_comments": list(comments)}


class TestFreezeFailOpen:
    def test_no_prior_head_no_freeze(self):
        rd = _rd(_fc())
        drop, summary = compute_freeze(rd, [], {"a.py"}, prior_head="")
        assert drop == set() and summary["suppressed"] == 0

    def test_changed_files_none_no_freeze(self):
        rd = _rd(_fc())
        drop, summary = compute_freeze(rd, [], None, prior_head="oldsha")
        assert drop == set() and summary["suppressed"] == 0

    def test_not_a_dict_no_crash(self):
        drop, summary = compute_freeze(None, [], {"a.py"}, prior_head="oldsha")
        assert drop == set()


class TestFreezePartition:
    def test_first_time_noncritical_on_unchanged_file_is_frozen(self):
        # Finding in b.py, but only a.py changed since the prior review -> freeze.
        rd = _rd(_fc(file="b.py", severity="warning",
                     title="magic number here", comment="style"))
        drop, summary = compute_freeze(rd, [], {"a.py"}, prior_head="oldsha")
        assert drop == {0}
        assert summary["suppressed"] == 1

    def test_finding_on_changed_file_survives(self):
        rd = _rd(_fc(file="a.py", severity="warning", title="bug in new code"))
        drop, summary = compute_freeze(rd, [], {"a.py"}, prior_head="oldsha")
        assert drop == set()
        assert summary["suppressed"] == 0

    def test_recurring_finding_survives_even_on_unchanged_file(self):
        prior = [_fc(file="b.py", line=10, severity="warning",
                     title="magic number here", comment="style")]
        rd = _rd(_fc(file="b.py", line=12, severity="warning",
                     title="magic number", comment="style"))
        drop, summary = compute_freeze(rd, prior, {"a.py"}, prior_head="oldsha")
        assert drop == set()  # recurring by identity -> not frozen

    def test_first_time_critical_on_unchanged_file_surfaces_labeled(self):
        rd = _rd(_fc(file="b.py", severity="critical",
                     title="SQL injection", comment="unsanitized"))
        drop, summary = compute_freeze(rd, [], {"a.py"}, prior_head="oldsha")
        assert drop == set()  # critical is not dropped
        assert summary["kept_pre_existing_critical"] == 1
        assert rd["file_comments"][0]["title"].startswith(PRE_EXISTING_PREFIX)

    def test_critical_label_not_doubled(self):
        rd = _rd(_fc(file="b.py", severity="critical",
                     title=f"{PRE_EXISTING_PREFIX} SQL injection"))
        compute_freeze(rd, [], {"a.py"}, prior_head="oldsha")
        assert rd["file_comments"][0]["title"].count(PRE_EXISTING_PREFIX) == 1

    def test_mixed_set(self):
        rd = _rd(
            _fc(file="a.py", severity="warning", title="in changed file"),   # survives
            _fc(file="b.py", severity="warning", title="frozen nit"),        # frozen
            _fc(file="c.py", severity="critical", title="frozen but critical"),  # surfaces labeled
        )
        drop, summary = compute_freeze(rd, [], {"a.py"}, prior_head="oldsha")
        assert drop == {1}
        assert summary == {"suppressed": 1, "kept_pre_existing_critical": 1}
