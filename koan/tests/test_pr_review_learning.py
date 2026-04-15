"""Tests for pr_review_learning.py — PR review learning for autonomous alignment."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.pr_review_learning import (
    _analyze_rejection_with_cli,
    _append_lessons_to_learnings,
    _compute_review_hash,
    _fetch_issue_comments_for_pr,
    _fetch_review_comments_for_pr,
    _fetch_reviews_for_pr,
    _increment_failure_count,
    _is_cache_fresh,
    _notify_analysis_failures,
    _parse_iso,
    _read_failure_count,
    _reset_failure_count,
    _write_cache,
    _write_rejection_journal_entries,
    _FAILURE_ALERT_THRESHOLD,
    analyze_reviews_with_cli,
    fetch_pr_reviews,
    format_reviews_for_analysis,
    learn_from_reviews,
)


# ─── _parse_iso ──────────────────────────────────────────────────────────


class TestParseIso:
    def test_z_suffix(self):
        dt = _parse_iso("2026-03-01T12:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3

    def test_offset_suffix(self):
        dt = _parse_iso("2026-03-01T12:00:00+00:00")
        assert dt is not None
        assert dt.hour == 12

    def test_empty_string(self):
        assert _parse_iso("") is None

    def test_none(self):
        assert _parse_iso(None) is None

    def test_invalid(self):
        assert _parse_iso("not-a-date") is None


# ─── format_reviews_for_analysis ─────────────────────────────────────────


class TestFormatReviewsForAnalysis:
    def _make_pr(self, number, title, was_merged=True, reviews=None, comments=None):
        return {
            "number": number,
            "title": title,
            "was_merged": was_merged,
            "reviews": reviews or [],
            "review_comments": comments or [],
        }

    def test_empty_prs(self):
        assert format_reviews_for_analysis([]) == ""

    def test_pr_with_no_reviews(self):
        prs = [self._make_pr(1, "feat: add X")]
        # No reviews or comments → no section
        assert format_reviews_for_analysis(prs) == ""

    def test_merged_pr_with_review(self):
        prs = [self._make_pr(1, "feat: add X", reviews=[
            {"state": "APPROVED", "body": "LGTM", "user": "reviewer"},
        ])]
        result = format_reviews_for_analysis(prs)
        assert "PR #1" in result
        assert "MERGED" in result
        assert "LGTM" in result

    def test_closed_pr_shows_status(self):
        prs = [self._make_pr(1, "feat: bad idea", was_merged=False, reviews=[
            {"state": "CHANGES_REQUESTED", "body": "Too big", "user": "reviewer"},
        ])]
        result = format_reviews_for_analysis(prs)
        assert "CLOSED (not merged)" in result

    def test_inline_comments_included(self):
        prs = [self._make_pr(1, "fix: thing", comments=[
            {"body": "Don't touch this", "path": "src/core.py", "user": "reviewer"},
        ])]
        result = format_reviews_for_analysis(prs)
        assert "src/core.py" in result
        assert "Don't touch this" in result

    def test_review_without_body_but_with_state(self):
        prs = [self._make_pr(1, "fix: thing", reviews=[
            {"state": "APPROVED", "body": "", "user": "reviewer"},
        ])]
        result = format_reviews_for_analysis(prs)
        assert "APPROVED" in result
        assert "[no comment]" in result

    def test_multiple_prs(self):
        prs = [
            self._make_pr(1, "feat: A", reviews=[
                {"state": "APPROVED", "body": "Nice!", "user": "r"},
            ]),
            self._make_pr(2, "feat: B", reviews=[
                {"state": "CHANGES_REQUESTED", "body": "Too big", "user": "r"},
            ]),
        ]
        result = format_reviews_for_analysis(prs)
        assert "PR #1" in result
        assert "PR #2" in result


# ─── _compute_review_hash ────────────────────────────────────────────────


class TestComputeReviewHash:
    def test_stable_for_same_input(self):
        prs = [{"number": 1, "reviews": [{"body": "ok"}], "review_comments": []}]
        h1 = _compute_review_hash(prs)
        h2 = _compute_review_hash(prs)
        assert h1 == h2

    def test_changes_with_new_review(self):
        prs1 = [{"number": 1, "reviews": [{"body": "ok"}], "review_comments": []}]
        prs2 = [{"number": 1, "reviews": [{"body": "ok"}, {"body": "more"}], "review_comments": []}]
        assert _compute_review_hash(prs1) != _compute_review_hash(prs2)

    def test_order_independent(self):
        prs_a = [
            {"number": 1, "reviews": [], "review_comments": []},
            {"number": 2, "reviews": [], "review_comments": []},
        ]
        prs_b = [
            {"number": 2, "reviews": [], "review_comments": []},
            {"number": 1, "reviews": [], "review_comments": []},
        ]
        assert _compute_review_hash(prs_a) == _compute_review_hash(prs_b)

    def test_returns_full_sha256_hex_digest(self):
        """Hash must be a full 64-char SHA-256 hex digest to prevent cache collisions."""
        prs = [{"number": 1, "reviews": [{"body": "lgtm"}], "review_comments": []}]
        h = _compute_review_hash(prs)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ─── cache ───────────────────────────────────────────────────────────────


class TestCache:
    def test_fresh_when_hash_matches(self, tmp_path):
        _write_cache(str(tmp_path), "abc123")
        assert _is_cache_fresh(str(tmp_path), "abc123") is True

    def test_stale_when_hash_differs(self, tmp_path):
        _write_cache(str(tmp_path), "abc123")
        assert _is_cache_fresh(str(tmp_path), "def456") is False

    def test_stale_when_no_cache(self, tmp_path):
        assert _is_cache_fresh(str(tmp_path), "abc123") is False

    def test_write_cache_creates_parent_dirs(self, tmp_path):
        """_write_cache should create missing parent directories."""
        nested = tmp_path / "deep" / "nested" / "dir"
        _write_cache(str(nested), "hash42")
        assert _is_cache_fresh(str(nested), "hash42") is True


# ─── _append_lessons_to_learnings ────────────────────────────────────────


class TestAppendLessonsToLearnings:
    def test_creates_file_if_missing(self, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        lessons = "- Reviewer prefers small PRs\n- Always add tests"
        added = _append_lessons_to_learnings(str(instance_dir), "myproject", lessons)

        assert added == 2
        learnings = (instance_dir / "memory" / "projects" / "myproject" / "learnings.md")
        assert learnings.exists()
        content = learnings.read_text()
        assert "small PRs" in content
        assert "add tests" in content
        assert "# Learnings" in content

    def test_appends_to_existing(self, tmp_path):
        instance_dir = tmp_path / "instance"
        learnings_dir = instance_dir / "memory" / "projects" / "myproject"
        learnings_dir.mkdir(parents=True)
        learnings_file = learnings_dir / "learnings.md"
        learnings_file.write_text("# Learnings — myproject\n\n- Old lesson\n")

        lessons = "- New lesson from reviews"
        added = _append_lessons_to_learnings(str(instance_dir), "myproject", lessons)

        assert added == 1
        content = learnings_file.read_text()
        assert "Old lesson" in content
        assert "New lesson from reviews" in content

    def test_skips_duplicates(self, tmp_path):
        instance_dir = tmp_path / "instance"
        learnings_dir = instance_dir / "memory" / "projects" / "myproject"
        learnings_dir.mkdir(parents=True)
        learnings_file = learnings_dir / "learnings.md"
        learnings_file.write_text("# Learnings\n\n- Existing lesson\n")

        lessons = "- Existing lesson\n- Brand new lesson"
        added = _append_lessons_to_learnings(str(instance_dir), "myproject", lessons)

        assert added == 1
        content = learnings_file.read_text()
        assert content.count("Existing lesson") == 1
        assert "Brand new lesson" in content

    def test_returns_zero_when_all_duplicates(self, tmp_path):
        instance_dir = tmp_path / "instance"
        learnings_dir = instance_dir / "memory" / "projects" / "myproject"
        learnings_dir.mkdir(parents=True)
        learnings_file = learnings_dir / "learnings.md"
        learnings_file.write_text("# Learnings\n\n- Already known\n")

        added = _append_lessons_to_learnings(str(instance_dir), "myproject", "- Already known")
        assert added == 0

    def test_empty_lessons_returns_zero(self, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        added = _append_lessons_to_learnings(str(instance_dir), "myproject", "")
        assert added == 0


# ─── analyze_reviews_with_cli ────────────────────────────────────────────


class TestAnalyzeReviewsWithCli:
    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command")
    @patch("app.config.get_model_config")
    @patch("app.prompts.load_prompt")
    def test_returns_stdout_on_success(self, mock_prompt, mock_models,
                                       mock_build, mock_run):
        mock_prompt.return_value = "analysis prompt"
        mock_models.return_value = {"lightweight": "haiku", "fallback": "sonnet"}
        mock_build.return_value = ["claude", "-p", "..."]
        mock_run.return_value = MagicMock(
            returncode=0, stdout="- Lesson 1\n- Lesson 2\n", stderr=""
        )

        result = analyze_reviews_with_cli("some review text", "/fake/path")
        assert "Lesson 1" in result
        assert "Lesson 2" in result

    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command")
    @patch("app.config.get_model_config")
    @patch("app.prompts.load_prompt")
    def test_returns_empty_on_failure(self, mock_prompt, mock_models,
                                      mock_build, mock_run):
        mock_prompt.return_value = "prompt"
        mock_models.return_value = {"lightweight": "haiku", "fallback": "sonnet"}
        mock_build.return_value = ["claude", "-p", "..."]
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="quota exceeded"
        )

        result = analyze_reviews_with_cli("review text", "/fake/path")
        assert result == ""

    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command")
    @patch("app.config.get_model_config")
    @patch("app.prompts.load_prompt")
    def test_returns_empty_on_exception(self, mock_prompt, mock_models,
                                        mock_build, mock_run):
        mock_prompt.return_value = "prompt"
        mock_models.return_value = {"lightweight": "haiku", "fallback": "sonnet"}
        mock_build.return_value = ["claude", "-p", "..."]
        mock_run.side_effect = OSError("timeout")

        result = analyze_reviews_with_cli("review text", "/fake/path")
        assert result == ""


# ─── fetch_pr_reviews ────────────────────────────────────────────────────


class TestFetchPrReviews:
    @patch("subprocess.run")
    def test_empty_when_no_prs(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="[]", stderr=""
        )
        result = fetch_pr_reviews("/fake/path")
        assert result == []

    @patch("subprocess.run")
    def test_filters_non_koan_branches(self, mock_run):
        now = datetime.now(timezone.utc)
        prs = [{
            "number": 1,
            "title": "fix: something",
            "createdAt": now.isoformat(),
            "mergedAt": now.isoformat(),
            "closedAt": None,
            "headRefName": "feature/not-koan",
            "state": "MERGED",
        }]
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(prs), stderr=""
        )
        result = fetch_pr_reviews("/fake/path")
        assert len(result) == 0

    @patch("subprocess.run")
    def test_filters_open_prs(self, mock_run):
        """Open PRs should be excluded — only merged/closed have review learnings."""
        now = datetime.now(timezone.utc)
        prs = [
            {
                "number": 1,
                "title": "feat: open PR",
                "createdAt": now.isoformat(),
                "mergedAt": None,
                "closedAt": None,
                "headRefName": "koan/open-one",
                "state": "OPEN",
            },
            {
                "number": 2,
                "title": "feat: merged PR",
                "createdAt": now.isoformat(),
                "mergedAt": now.isoformat(),
                "closedAt": now.isoformat(),
                "headRefName": "koan/merged-one",
                "state": "MERGED",
            },
        ]
        # Single call returns both open and merged — only merged should survive
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(prs), stderr=""
        )
        result = fetch_pr_reviews("/fake/path")
        assert len(result) == 1
        assert result[0]["number"] == 2

    @patch("subprocess.run")
    def test_single_api_call(self, mock_run):
        """Regression: should make ONE gh pr list call, not two (merged+closed)."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="[]", stderr=""
        )
        fetch_pr_reviews("/fake/path")
        # Only one subprocess.run call for gh pr list
        pr_list_calls = [
            c for c in mock_run.call_args_list
            if "pr" in str(c) and "list" in str(c)
        ]
        assert len(pr_list_calls) == 1

    def test_import_error_returns_empty(self):
        with patch.dict("sys.modules", {"app.github": None}):
            result = fetch_pr_reviews("/fake/path")
            assert result == []


# ─── _fetch_reviews_for_pr / _fetch_review_comments_for_pr warnings ─────


class TestFetchReviewsWarnsOnMalformedJson:
    """Malformed gh --jq output should log a warning, not be silently discarded."""

    @patch("app.github.run_gh")
    def test_malformed_review_line_logs_warning(self, mock_gh, caplog):
        good = json.dumps({"state": "APPROVED", "body": "lgtm", "user": "r"})
        mock_gh.return_value = f"{good}\nNOT-JSON\n"

        import logging
        logger = logging.getLogger("app.pr_review_learning")
        logger.addHandler(logging.NullHandler())
        with caplog.at_level(logging.DEBUG, logger="app.pr_review_learning"):
            result = _fetch_reviews_for_pr("/fake", 42)

        assert len(result) == 1  # good line parsed
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("PR #42" in r.message for r in warnings)

    @patch("app.github.run_gh")
    def test_malformed_comment_line_logs_warning(self, mock_gh, caplog):
        good = json.dumps({"body": "fix this", "path": "a.py", "user": "r"})
        mock_gh.return_value = f"{good}\n{{broken\n"

        import logging
        logger = logging.getLogger("app.pr_review_learning")
        logger.addHandler(logging.NullHandler())
        with caplog.at_level(logging.DEBUG, logger="app.pr_review_learning"):
            result = _fetch_review_comments_for_pr("/fake", 7)

        assert len(result) == 1
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("PR #7" in r.message for r in warnings)


# ─── learn_from_reviews (integration) ────────────────────────────────────


class TestLearnFromReviews:
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_returns_skipped_when_no_prs(self, mock_fetch):
        mock_fetch.return_value = []
        result = learn_from_reviews("/instance", "proj", "/path")
        assert result["skipped_reason"] == "no_reviews"
        assert result["lessons_added"] == 0

    @patch("app.pr_review_learning._write_cache")
    @patch("app.pr_review_learning._is_cache_fresh")
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_skips_when_cache_fresh(self, mock_fetch, mock_cache, mock_write):
        mock_fetch.return_value = [
            {"number": 1, "reviews": [{"body": "ok"}], "review_comments": [],
             "was_merged": True, "title": "fix"},
        ]
        mock_cache.return_value = True
        result = learn_from_reviews("/instance", "proj", "/path")
        assert result["skipped_reason"] == "cache_fresh"
        mock_write.assert_not_called()

    @patch("app.pr_review_learning._write_rejection_journal_entries")
    @patch("app.pr_review_learning._append_lessons_to_learnings")
    @patch("app.pr_review_learning._write_cache")
    @patch("app.pr_review_learning._is_cache_fresh")
    @patch("app.pr_review_learning._analyze_rejection_with_cli")
    @patch("app.pr_review_learning.analyze_reviews_with_cli")
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_full_flow(self, mock_fetch, mock_analyze, mock_reject_analyze,
                       mock_cache_check, mock_cache_write, mock_append,
                       mock_journal):
        mock_fetch.return_value = [
            {
                "number": 1, "title": "feat: X", "was_merged": False,
                "reviews": [
                    {"state": "CHANGES_REQUESTED", "body": "Too big!", "user": "r"},
                ],
                "review_comments": [],
                "issue_comments": [],
            },
        ]
        mock_cache_check.return_value = False
        mock_reject_analyze.return_value = "- Keep PRs small and focused"
        mock_append.return_value = 1

        result = learn_from_reviews("/instance", "proj", "/path")

        assert result["fetched"] == 1
        assert result["analyzed"] is True
        assert result["lessons_added"] == 1
        assert result["skipped_reason"] is None
        mock_cache_write.assert_called_once()
        mock_analyze.assert_not_called()
        mock_reject_analyze.assert_called_once()

    @patch("app.pr_review_learning._write_cache")
    @patch("app.pr_review_learning._is_cache_fresh")
    @patch("app.pr_review_learning.analyze_reviews_with_cli")
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_empty_analysis_skips_cache(self, mock_fetch, mock_analyze,
                                        mock_cache_check, mock_cache_write):
        mock_fetch.return_value = [
            {
                "number": 1, "title": "feat: X", "was_merged": True,
                "reviews": [
                    {"state": "APPROVED", "body": "ok", "user": "r"},
                ],
                "review_comments": [],
            },
        ]
        mock_cache_check.return_value = False
        mock_analyze.return_value = ""

        result = learn_from_reviews("/instance", "proj", "/path")
        assert result["skipped_reason"] == "empty_analysis"
        # Cache must NOT be written on empty analysis (API failure),
        # so future retries can re-attempt the analysis
        mock_cache_write.assert_not_called()

    @patch("app.pr_review_learning._notify_analysis_failures")
    @patch("app.pr_review_learning._increment_failure_count")
    @patch("app.pr_review_learning._is_cache_fresh")
    @patch("app.pr_review_learning.analyze_reviews_with_cli")
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_empty_analysis_increments_failure_counter(
        self, mock_fetch, mock_analyze, mock_cache_check,
        mock_increment, mock_notify,
    ):
        mock_fetch.return_value = [
            {
                "number": 1, "title": "feat: X", "was_merged": True,
                "reviews": [{"state": "APPROVED", "body": "ok", "user": "r"}],
                "review_comments": [],
            },
        ]
        mock_cache_check.return_value = False
        mock_analyze.return_value = ""
        mock_increment.return_value = 2

        result = learn_from_reviews("/instance", "proj", "/path")
        assert result["skipped_reason"] == "empty_analysis"
        mock_increment.assert_called_once_with("/instance")
        mock_notify.assert_called_once_with("/instance", 2)

    @patch("app.pr_review_learning._reset_failure_count")
    @patch("app.pr_review_learning._append_lessons_to_learnings")
    @patch("app.pr_review_learning._write_cache")
    @patch("app.pr_review_learning._is_cache_fresh")
    @patch("app.pr_review_learning.analyze_reviews_with_cli")
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_successful_analysis_resets_failure_counter(
        self, mock_fetch, mock_analyze, mock_cache_check,
        mock_cache_write, mock_append, mock_reset,
    ):
        mock_fetch.return_value = [
            {
                "number": 1, "title": "feat: X", "was_merged": True,
                "reviews": [{"state": "APPROVED", "body": "ok", "user": "r"}],
                "review_comments": [],
            },
        ]
        mock_cache_check.return_value = False
        mock_analyze.return_value = "- New lesson"
        mock_append.return_value = 1

        result = learn_from_reviews("/instance", "proj", "/path")
        assert result["lessons_added"] == 1
        mock_reset.assert_called_once_with("/instance")


# ─── Consecutive failure tracking ───────────────────────────────────────


class TestFailureCounter:
    def test_read_returns_zero_when_no_file(self, tmp_path):
        assert _read_failure_count(str(tmp_path)) == 0

    def test_increment_from_zero(self, tmp_path):
        count = _increment_failure_count(str(tmp_path))
        assert count == 1
        assert _read_failure_count(str(tmp_path)) == 1

    def test_increment_accumulates(self, tmp_path):
        _increment_failure_count(str(tmp_path))
        _increment_failure_count(str(tmp_path))
        count = _increment_failure_count(str(tmp_path))
        assert count == 3
        assert _read_failure_count(str(tmp_path)) == 3

    def test_reset_removes_file(self, tmp_path):
        _increment_failure_count(str(tmp_path))
        _increment_failure_count(str(tmp_path))
        _reset_failure_count(str(tmp_path))
        assert _read_failure_count(str(tmp_path)) == 0

    def test_reset_noop_when_no_file(self, tmp_path):
        # Should not raise
        _reset_failure_count(str(tmp_path))

    def test_read_handles_corrupt_file(self, tmp_path):
        counter_path = tmp_path / ".koan-pr-review-analysis-failures"
        counter_path.write_text("not-a-number\n")
        assert _read_failure_count(str(tmp_path)) == 0


class TestNotifyAnalysisFailures:
    def test_no_alert_below_threshold(self, tmp_path):
        with patch("app.utils.append_to_outbox") as mock_append:
            _notify_analysis_failures(str(tmp_path), _FAILURE_ALERT_THRESHOLD - 1)
            mock_append.assert_not_called()

    def test_alert_at_threshold(self, tmp_path):
        with patch("app.utils.append_to_outbox") as mock_append:
            _notify_analysis_failures(str(tmp_path), _FAILURE_ALERT_THRESHOLD)
            mock_append.assert_called_once()
            msg = mock_append.call_args[0][1]
            assert "failed" in msg
            assert str(_FAILURE_ALERT_THRESHOLD) in msg

    def test_no_alert_above_threshold(self, tmp_path):
        """Only alert at exact threshold to avoid spamming."""
        with patch("app.utils.append_to_outbox") as mock_append:
            _notify_analysis_failures(str(tmp_path), _FAILURE_ALERT_THRESHOLD + 1)
            mock_append.assert_not_called()


# ─── Issue comments for closed PRs ────────────────────────────────────


class TestFetchIssueCommentsForPr:
    @patch("app.github.run_gh")
    def test_fetches_issue_comments(self, mock_gh):
        comment = json.dumps({"body": "closing this", "user": "reviewer", "created_at": "2026-01-01T00:00:00Z"})
        mock_gh.return_value = comment + "\n"
        result = _fetch_issue_comments_for_pr("/fake", 42)
        assert len(result) == 1
        assert result[0]["body"] == "closing this"

    @patch("app.github.run_gh")
    def test_returns_empty_on_no_comments(self, mock_gh):
        mock_gh.return_value = ""
        result = _fetch_issue_comments_for_pr("/fake", 42)
        assert result == []


class TestFetchPrReviewsIssueComments:
    """Verify issue comments are only fetched for closed-unmerged PRs."""

    @patch("subprocess.run")
    def test_closed_pr_gets_issue_comments(self, mock_run):
        now = datetime.now(timezone.utc)
        prs = [{
            "number": 1, "title": "feat: bad",
            "createdAt": now.isoformat(), "mergedAt": None,
            "closedAt": now.isoformat(),
            "headRefName": "koan/bad-idea", "state": "CLOSED",
        }]
        reviews_json = json.dumps({"state": "CHANGES_REQUESTED", "body": "no", "user": "r"})
        comment_json = json.dumps({"body": "fix", "path": "a.py", "user": "r"})
        issue_json = json.dumps({"body": "closing", "user": "r", "created_at": now.isoformat()})

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(str(c) for c in cmd)
            if "pr" in cmd_str and "list" in cmd_str:
                return MagicMock(returncode=0, stdout=json.dumps(prs), stderr="")
            if "issues" in cmd_str:
                return MagicMock(returncode=0, stdout=issue_json + "\n", stderr="")
            if "reviews" in cmd_str:
                return MagicMock(returncode=0, stdout=reviews_json + "\n", stderr="")
            return MagicMock(returncode=0, stdout=comment_json + "\n", stderr="")

        mock_run.side_effect = side_effect
        result = fetch_pr_reviews("/fake/path")
        assert len(result) == 1
        assert len(result[0]["issue_comments"]) == 1
        assert result[0]["issue_comments"][0]["body"] == "closing"

    @patch("subprocess.run")
    def test_merged_pr_no_issue_comments(self, mock_run):
        now = datetime.now(timezone.utc)
        prs = [{
            "number": 1, "title": "feat: good",
            "createdAt": now.isoformat(),
            "mergedAt": now.isoformat(),
            "closedAt": now.isoformat(),
            "headRefName": "koan/good", "state": "MERGED",
        }]
        reviews_json = json.dumps({"state": "APPROVED", "body": "lgtm", "user": "r"})

        def side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(str(c) for c in cmd)
            if "pr" in cmd_str and "list" in cmd_str:
                return MagicMock(returncode=0, stdout=json.dumps(prs), stderr="")
            if "reviews" in cmd_str:
                return MagicMock(returncode=0, stdout=reviews_json + "\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fetch_pr_reviews("/fake/path")
        assert len(result) == 1
        assert result[0]["issue_comments"] == []


# ─── Format includes issue comments for closed PRs ────────────────────


class TestFormatWithIssueComments:
    def test_closed_pr_includes_issue_comments(self):
        prs = [{
            "number": 1, "title": "feat: bad", "was_merged": False,
            "reviews": [], "review_comments": [],
            "issue_comments": [{"body": "This isn't useful", "user": "human"}],
        }]
        result = format_reviews_for_analysis(prs)
        assert "Comment by human: This isn't useful" in result
        assert "CLOSED (not merged)" in result

    def test_merged_pr_excludes_issue_comments(self):
        prs = [{
            "number": 1, "title": "feat: good", "was_merged": True,
            "reviews": [{"state": "APPROVED", "body": "nice", "user": "r"}],
            "review_comments": [],
            "issue_comments": [{"body": "should not appear", "user": "human"}],
        }]
        result = format_reviews_for_analysis(prs)
        assert "should not appear" not in result


# ─── Rejection learning uses dedicated prompt ─────────────────────────


class TestRejectionLearningPrompt:
    @patch("app.pr_review_learning._write_rejection_journal_entries")
    @patch("app.pr_review_learning._append_lessons_to_learnings")
    @patch("app.pr_review_learning._write_cache")
    @patch("app.pr_review_learning._is_cache_fresh")
    @patch("app.pr_review_learning._analyze_rejection_with_cli")
    @patch("app.pr_review_learning.analyze_reviews_with_cli")
    @patch("app.pr_review_learning.fetch_pr_reviews")
    def test_rejected_prs_use_rejection_prompt(
        self, mock_fetch, mock_analyze, mock_reject,
        mock_cache_check, mock_cache_write, mock_append, mock_journal,
    ):
        mock_fetch.return_value = [
            {
                "number": 1, "title": "feat: unwanted", "was_merged": False,
                "reviews": [{"state": "CHANGES_REQUESTED", "body": "no", "user": "r"}],
                "review_comments": [], "issue_comments": [],
            },
            {
                "number": 2, "title": "feat: good", "was_merged": True,
                "reviews": [{"state": "APPROVED", "body": "nice", "user": "r"}],
                "review_comments": [], "issue_comments": [],
            },
        ]
        mock_cache_check.return_value = False
        mock_analyze.return_value = "- Good pattern"
        mock_reject.return_value = "- Do not do X"
        mock_append.return_value = 1

        learn_from_reviews("/instance", "proj", "/path")

        mock_analyze.assert_called_once()
        mock_reject.assert_called_once()
        # Verify rejection learnings get distinct section header
        calls = mock_append.call_args_list
        headers = [c.kwargs.get("section_header", c[0][3] if len(c[0]) > 3 else "PR review learnings") for c in calls]
        assert "Rejected PR learnings" in headers


# ─── Rejected PR journal entry ────────────────────────────────────────


class TestRejectedPrJournalEntry:
    @patch("app.journal.append_to_journal")
    def test_writes_journal_for_rejected_prs(self, mock_journal):
        rejected_prs = [
            {"number": 42, "title": "feat: bad idea"},
        ]
        _write_rejection_journal_entries(
            "/instance", "myproject", rejected_prs,
            "- Do not touch the auth module\n- Keep scope narrow",
        )
        mock_journal.assert_called_once()
        content = mock_journal.call_args[0][2]
        assert "PR #42" in content
        assert "bad idea" in content
        assert "Do not touch the auth module" in content
        assert "myproject" in content

    @patch("app.journal.append_to_journal")
    def test_no_lessons_uses_fallback_reason(self, mock_journal):
        _write_rejection_journal_entries(
            "/instance", "proj", [{"number": 1, "title": "X"}], "no bullets here",
        )
        mock_journal.assert_called_once()
        content = mock_journal.call_args[0][2]
        assert "No specific reason extracted" in content


# ─── Rejected PR learnings section header ──────────────────────────────


class TestRejectedPrLearningsSectionHeader:
    def test_custom_section_header(self, tmp_path):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        added = _append_lessons_to_learnings(
            str(instance_dir), "proj", "- Never refactor logging",
            section_header="Rejected PR learnings",
        )
        assert added == 1
        learnings = instance_dir / "memory" / "projects" / "proj" / "learnings.md"
        content = learnings.read_text()
        assert "## Rejected PR learnings (" in content
        assert "Never refactor logging" in content


# ─── Cache includes issue comments ─────────────────────────────────────


class TestCacheIncludesIssueComments:
    def test_hash_changes_with_issue_comments(self):
        prs1 = [{"number": 1, "reviews": [], "review_comments": [], "issue_comments": []}]
        prs2 = [{"number": 1, "reviews": [], "review_comments": [],
                 "issue_comments": [{"body": "closing"}]}]
        assert _compute_review_hash(prs1) != _compute_review_hash(prs2)
