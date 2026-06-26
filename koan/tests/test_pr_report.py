"""Tests for the PR activity report engine and the /report skill handler."""

import json
from datetime import date
from unittest.mock import patch

from app import pr_report
from app.skills import SkillContext


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

def _counts(created, merged, interacted, interacted_merged):
    return {
        "created": created,
        "merged": merged,
        "interacted": interacted,
        "interacted_merged": interacted_merged,
    }


def test_format_report_global_and_per_project_totals():
    counts = {
        "koan": _counts(7, 6, 11, 7),
        "my-toolkit": _counts(5, 3, 9, 4),
    }
    out = pr_report.format_report(
        counts, usage_by_project={}, days=7,
        start=date(2026, 6, 18), end=date(2026, 6, 24),
    )
    # Fenced code block
    assert out.startswith("```\n") and out.endswith("\n```")
    # Header reflects the window
    assert "PR Report — week (2026-06-18 .. 2026-06-24)" in out
    # Global totals: created 12, merged 9 (75%), interacted 20, i+m 11
    assert "Created:            12" in out
    assert "Merged:             9  (75% of created)" in out
    assert "Interacted:         20" in out
    assert "Interacted+merged:  11" in out
    # TOTAL row present with summed columns
    assert "TOTAL" in out
    # Both projects appear
    assert "koan" in out and "my-toolkit" in out


def test_format_report_month_label():
    out = pr_report.format_report(
        {"koan": _counts(1, 1, 1, 1)}, usage_by_project={}, days=30,
        start=date(2026, 5, 26), end=date(2026, 6, 24),
    )
    assert "PR Report — month" in out


def test_format_report_cohort_pct_zero_guard():
    # Zero created -> 0% (no division error)
    out = pr_report.format_report(
        {"koan": _counts(0, 0, 3, 0)}, usage_by_project={}, days=7,
        start=date(2026, 6, 18), end=date(2026, 6, 24),
    )
    assert "Merged:             0  (0% of created)" in out


def test_format_report_includes_usage_summary():
    usage = {
        "koan": {"input_tokens": 3_000_000, "output_tokens": 1_200_000,
                 "total_cost_usd": 6.10},
    }
    out = pr_report.format_report(
        {"koan": _counts(2, 1, 2, 1)}, usage_by_project=usage, days=7,
        start=date(2026, 6, 18), end=date(2026, 6, 24),
    )
    assert "Usage:" in out
    assert "4.2M tok" in out
    assert "$6.10" in out


def test_format_report_partial_flag():
    out = pr_report.format_report(
        {"koan": _counts(1, 0, 1, 0)}, usage_by_project={}, days=7,
        start=date(2026, 6, 18), end=date(2026, 6, 24), partial=True,
    )
    assert "partial" in out.lower()


# ---------------------------------------------------------------------------
# fetch_pr_counts (GraphQL parsing)
# ---------------------------------------------------------------------------

def test_fetch_pr_counts_parses_aliased_issue_counts():
    repos = [("koan", "owner/koan"), ("tool", "owner/tool")]
    # Mock the aliased GraphQL response: r{i}_{metric} -> {issueCount}
    data = {"data": {}}
    expected = {
        "koan": _counts(7, 6, 11, 7),
        "tool": _counts(5, 3, 9, 4),
    }
    order = [("koan", 0), ("tool", 1)]
    for name, i in order:
        for metric in pr_report._METRICS:
            data["data"][f"r{i}_{metric}"] = {"issueCount": expected[name][metric]}

    with patch("app.github.run_gh", return_value=json.dumps(data)) as mock_gh:
        counts, partial = pr_report.fetch_pr_counts(
            repos, "koanbot", date(2026, 6, 18), date(2026, 6, 24)
        )

    assert partial is False
    assert counts == expected
    # Single GraphQL call for a small repo set
    assert mock_gh.call_count == 1
    # The query carries the right search filters
    sent_query = mock_gh.call_args[0]
    assert any("involves:koanbot" in str(a) for a in sent_query)


def test_fetch_pr_counts_marks_partial_on_failure():
    repos = [("koan", "owner/koan")]
    with patch("app.github.run_gh", side_effect=RuntimeError("boom")):
        counts, partial = pr_report.fetch_pr_counts(
            repos, "koanbot", date(2026, 6, 18), date(2026, 6, 24)
        )
    assert partial is True
    assert counts == {"koan": _counts(0, 0, 0, 0)}


def test_fetch_pr_counts_isolates_one_bad_repo_in_chunk():
    # A chunk-wide GraphQL failure must not zero the healthy repos: the engine
    # retries each repo individually. Here "owner/bad" always fails; "owner/ok"
    # succeeds on its individual retry.
    repos = [("ok", "owner/ok"), ("bad", "owner/bad")]

    def fake_run_gh(*args, **kwargs):
        query = next((a for a in args if "owner/" in str(a)), "")
        if "owner/bad" in query:
            raise RuntimeError("no access to owner/bad")
        # Single-repo retry for owner/ok -> aliases are r0_<metric>
        data = {"data": {f"r0_{m}": {"issueCount": 4} for m in pr_report._METRICS}}
        return json.dumps(data)

    with patch("app.github.run_gh", side_effect=fake_run_gh):
        counts, partial = pr_report.fetch_pr_counts(
            repos, "koanbot", date(2026, 6, 18), date(2026, 6, 24)
        )

    assert partial is True
    assert counts["ok"] == _counts(4, 4, 4, 4)  # healthy repo preserved
    assert counts["bad"] == _counts(0, 0, 0, 0)  # only the bad repo zeroed


def test_fetch_pr_counts_multi_chunk(monkeypatch):
    # More repos than a single chunk -> multiple GraphQL calls, all parsed.
    monkeypatch.setattr(pr_report, "_REPOS_PER_QUERY", 2)
    repos = [("a", "o/a"), ("b", "o/b"), ("c", "o/c")]

    calls = []

    def fake_run_gh(*args, **kwargs):
        # Each batch numbers aliases from r0 within that batch; return 1 each.
        query = next((a for a in args if str(a).startswith("query=")), "")
        calls.append(query)
        # One issueCount per aliased field present in this batch (r0_, r1_, ...).
        idx = 0
        data = {"data": {}}
        while f"r{idx}_created" in query:
            for m in pr_report._METRICS:
                data["data"][f"r{idx}_{m}"] = {"issueCount": 1}
            idx += 1
        return json.dumps(data)

    with patch("app.github.run_gh", side_effect=fake_run_gh):
        counts, partial = pr_report.fetch_pr_counts(
            repos, "koanbot", date(2026, 6, 18), date(2026, 6, 24)
        )

    assert partial is False
    assert len(calls) == 2  # two chunks (2 + 1)
    for name in ("a", "b", "c"):
        assert counts[name] == _counts(1, 1, 1, 1)


def test_fetch_pr_counts_no_user_returns_zeros():
    repos = [("koan", "owner/koan")]
    counts, partial = pr_report.fetch_pr_counts(
        repos, "", date(2026, 6, 18), date(2026, 6, 24)
    )
    assert counts == {"koan": _counts(0, 0, 0, 0)}


# ---------------------------------------------------------------------------
# build_report orchestration
# ---------------------------------------------------------------------------

def test_build_report_no_repos_message(tmp_path):
    with patch("app.pr_report.resolve_repos", return_value=[]):
        out = pr_report.build_report(tmp_path, days=7)
    assert "No GitHub-backed projects" in out


def test_build_report_no_user_message(tmp_path):
    with patch("app.pr_report.resolve_repos", return_value=[("koan", "o/koan")]), \
         patch("app.github.get_gh_username", return_value=""):
        out = pr_report.build_report(tmp_path, days=7)
    assert "Could not resolve the GitHub user" in out


def test_build_report_happy_path(tmp_path):
    data = {"data": {}}
    for metric in pr_report._METRICS:
        data["data"][f"r0_{metric}"] = {"issueCount": 2}
    with patch("app.pr_report.resolve_repos", return_value=[("koan", "o/koan")]), \
         patch("app.github.get_gh_username", return_value="koanbot"), \
         patch("app.github.run_gh", return_value=json.dumps(data)):
        out = pr_report.build_report(tmp_path, days=7)
    assert out.startswith("```")
    assert "PR Report — week" in out
    assert "Created:            2" in out


# ---------------------------------------------------------------------------
# /report skill handler
# ---------------------------------------------------------------------------

def _ctx(tmp_path, command_name="report", args=""):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name=command_name,
        args=args,
    )


def test_handler_default_reports_both_windows(tmp_path):
    from skills.core.report import handler as h
    with patch("app.pr_report.build_report", side_effect=["WEEK", "MONTH"]) as mock_build:
        assert h.handle(_ctx(tmp_path, "report")) == "WEEK\n\nMONTH"
    assert [c.args[1] for c in mock_build.call_args_list] == [7, 30]


def test_handler_weekly_alias_selects_7d_only(tmp_path):
    from skills.core.report import handler as h
    with patch("app.pr_report.build_report", return_value="OK") as mock_build:
        assert h.handle(_ctx(tmp_path, "weekly_report")) == "OK"
    assert [c.args[1] for c in mock_build.call_args_list] == [7]


def test_handler_monthly_alias_selects_30d_only(tmp_path):
    from skills.core.report import handler as h
    with patch("app.pr_report.build_report", return_value="OK") as mock_build:
        h.handle(_ctx(tmp_path, "monthly_report"))
    assert [c.args[1] for c in mock_build.call_args_list] == [30]


def test_handler_explicit_week_flag_single_window(tmp_path):
    from skills.core.report import handler as h
    with patch("app.pr_report.build_report", return_value="OK") as mock_build:
        h.handle(_ctx(tmp_path, "report", args="--month"))
    assert [c.args[1] for c in mock_build.call_args_list] == [30]


def test_resolve_windows_matrix():
    from skills.core.report.handler import _resolve_windows
    assert _resolve_windows("report", "") == [7, 30]
    assert _resolve_windows("weekly_report", "") == [7]
    assert _resolve_windows("monthly_report", "") == [30]
    assert _resolve_windows("report", "--month") == [30]
    assert _resolve_windows("report", "--week") == [7]
    assert _resolve_windows("report", "--week --month") == [7, 30]
    # Aliases pin their window regardless of flags.
    assert _resolve_windows("monthly_report", "--week") == [30]
