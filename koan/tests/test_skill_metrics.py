"""Tests for skill_metrics.py — per-project skill metrics tracking."""

from pathlib import Path

import pytest

from app.skill_metrics import (
    _metrics_path,
    _ensure_table,
    _sanitize,
    record_plan_metric,
    record_pr_metric,
    read_metrics,
    compute_summary,
    format_skill_metrics_summary,
    _METRICS_FILENAME,
    _TABLE_HEADER,
)


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    inst = tmp_path / "instance"
    inst.mkdir()
    return str(inst)


# --- _metrics_path ---

class TestMetricsPath:
    def test_resolves_correctly(self, instance_dir):
        p = _metrics_path(instance_dir, "myproject")
        assert p == Path(instance_dir) / "memory" / "projects" / "myproject" / _METRICS_FILENAME

    def test_different_projects(self, instance_dir):
        p1 = _metrics_path(instance_dir, "alpha")
        p2 = _metrics_path(instance_dir, "beta")
        assert p1 != p2
        assert "alpha" in str(p1)
        assert "beta" in str(p2)


# --- _ensure_table ---

class TestEnsureTable:
    def test_creates_file_with_header(self, instance_dir):
        path = _metrics_path(instance_dir, "proj")
        _ensure_table(path)
        assert path.exists()
        content = path.read_text()
        assert "# Skill Metrics" in content
        assert "| Date |" in content

    def test_idempotent(self, instance_dir):
        path = _metrics_path(instance_dir, "proj")
        _ensure_table(path)
        content1 = path.read_text()
        _ensure_table(path)
        content2 = path.read_text()
        assert content1 == content2

    def test_creates_parent_dirs(self, instance_dir):
        path = _metrics_path(instance_dir, "newproject")
        assert not path.parent.exists()
        _ensure_table(path)
        assert path.parent.exists()


# --- _sanitize ---

class TestSanitize:
    def test_removes_pipes(self):
        assert "|" not in _sanitize("foo|bar|baz")

    def test_removes_newlines(self):
        result = _sanitize("line1\nline2\r\n")
        assert "\n" not in result
        assert "\r" not in result

    def test_truncates_long_strings(self):
        long_str = "x" * 200
        result = _sanitize(long_str, max_len=80)
        assert len(result) <= 80
        assert result.endswith("...")

    def test_short_strings_unchanged(self):
        assert _sanitize("hello") == "hello"


# --- record_plan_metric ---

class TestRecordPlanMetric:
    def test_appends_approved_row(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1, issues_summary="")
        path = _metrics_path(instance_dir, "proj")
        content = path.read_text()
        assert "| plan | APPROVED | 1 |" in content

    def test_appends_rejected_row(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=False, rounds=3, issues_summary="phases too large")
        path = _metrics_path(instance_dir, "proj")
        content = path.read_text()
        assert "| plan | REJECTED | 3 |" in content
        assert "phases too large" in content

    def test_multiple_entries(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        record_plan_metric(instance_dir, "proj", approved=False, rounds=2, issues_summary="issues")
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        rows = read_metrics(instance_dir, "proj", days=1)
        assert len(rows) == 3

    def test_truncates_long_issues(self, instance_dir):
        long_issues = "x" * 200
        record_plan_metric(instance_dir, "proj", approved=False, rounds=2, issues_summary=long_issues)
        path = _metrics_path(instance_dir, "proj")
        content = path.read_text()
        # Each line should be reasonable length (no 200-char cell)
        for line in content.splitlines():
            if line.startswith("| 2"):
                assert len(line) < 200


# --- record_pr_metric ---

class TestRecordPrMetric:
    def test_appends_ci_pass(self, instance_dir):
        record_pr_metric(instance_dir, "proj", "fix", pr_url="https://github.com/o/r/pull/1", ci_status="pass")
        path = _metrics_path(instance_dir, "proj")
        content = path.read_text()
        assert "| fix | CI:pass |" in content
        assert "github.com" in content

    def test_appends_ci_fail(self, instance_dir):
        record_pr_metric(instance_dir, "proj", "implement", ci_status="fail")
        content = _metrics_path(instance_dir, "proj").read_text()
        assert "| implement | CI:fail |" in content

    def test_no_ci_status(self, instance_dir):
        record_pr_metric(instance_dir, "proj", "fix")
        content = _metrics_path(instance_dir, "proj").read_text()
        assert "| submitted |" in content


# --- read_metrics ---

class TestReadMetrics:
    def test_empty_when_no_file(self, instance_dir):
        rows = read_metrics(instance_dir, "nonexistent", days=30)
        assert rows == []

    def test_reads_plan_rows(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        rows = read_metrics(instance_dir, "proj", days=30)
        assert len(rows) == 1
        assert rows[0]["skill"] == "plan"
        assert rows[0]["outcome"] == "APPROVED"
        assert rows[0]["rounds"] == "1"

    def test_filters_by_date(self, instance_dir):
        # Write a row with an old date directly
        path = _metrics_path(instance_dir, "proj")
        _ensure_table(path)
        with open(path, "a") as f:
            f.write("| 2020-01-01 | plan | APPROVED | 1 | old |\n")

        record_plan_metric(instance_dir, "proj", approved=True, rounds=2)
        rows = read_metrics(instance_dir, "proj", days=30)
        # Only the recent row should appear
        assert len(rows) == 1
        assert rows[0]["rounds"] == "2"

    def test_handles_malformed_lines(self, instance_dir):
        path = _metrics_path(instance_dir, "proj")
        _ensure_table(path)
        with open(path, "a") as f:
            f.write("| 2026-01-01 | bad |\n")  # too few columns
        rows = read_metrics(instance_dir, "proj", days=3650)
        assert len(rows) == 0


# --- compute_summary ---

class TestComputeSummary:
    def test_empty_project(self, instance_dir):
        s = compute_summary(instance_dir, "empty", days=30)
        assert s["plan_total"] == 0
        assert s["pr_total"] == 0

    def test_plan_metrics(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        record_plan_metric(instance_dir, "proj", approved=True, rounds=2)
        record_plan_metric(instance_dir, "proj", approved=False, rounds=3)

        s = compute_summary(instance_dir, "proj", days=30)
        assert s["plan_total"] == 3
        assert s["plan_approved"] == 2
        assert abs(s["plan_approval_rate"] - 2 / 3) < 0.01
        assert abs(s["plan_avg_rounds"] - 2.0) < 0.01

    def test_pr_metrics(self, instance_dir):
        record_pr_metric(instance_dir, "proj", "fix", ci_status="pass")
        record_pr_metric(instance_dir, "proj", "implement", ci_status="pass")
        record_pr_metric(instance_dir, "proj", "fix", ci_status="fail")

        s = compute_summary(instance_dir, "proj", days=30)
        assert s["pr_total"] == 3
        assert s["pr_ci_pass"] == 2
        assert s["pr_ci_fail"] == 1
        assert abs(s["pr_ci_pass_rate"] - 2 / 3) < 0.01

    def test_mixed_skills(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        record_pr_metric(instance_dir, "proj", "fix", ci_status="pass")

        s = compute_summary(instance_dir, "proj", days=30)
        assert s["plan_total"] == 1
        assert s["pr_total"] == 1


# --- format_skill_metrics_summary ---

class TestFormatSkillMetricsSummary:
    def test_empty_returns_empty_string(self, instance_dir):
        result = format_skill_metrics_summary(instance_dir, "empty")
        assert result == ""

    def test_plan_summary(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        record_plan_metric(instance_dir, "proj", approved=False, rounds=3)

        result = format_skill_metrics_summary(instance_dir, "proj")
        assert "Plan reviews:" in result
        assert "50%" in result
        assert "1/2" in result

    def test_pr_summary(self, instance_dir):
        record_pr_metric(instance_dir, "proj", "fix", ci_status="pass")
        record_pr_metric(instance_dir, "proj", "fix", ci_status="fail")

        result = format_skill_metrics_summary(instance_dir, "proj")
        assert "PR CI:" in result
        assert "50%" in result

    def test_combined_summary(self, instance_dir):
        record_plan_metric(instance_dir, "proj", approved=True, rounds=1)
        record_pr_metric(instance_dir, "proj", "fix", ci_status="pass")

        result = format_skill_metrics_summary(instance_dir, "proj")
        assert "Plan reviews:" in result
        assert "PR CI:" in result
