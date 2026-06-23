"""Tests for the private post-implementation review gate."""

from unittest.mock import MagicMock, patch

from types import SimpleNamespace

from app.private_review_gate import (
    _actionable_findings,
    _budget_preflight,
    _build_fix_prompt,
    _dedup_precheck,
    _diffstat_from_diff,
    _maybe_record_clean,
    format_gate_note,
    run_gate_for_skill,
    run_private_review_gate,
)


def _review(*severities):
    return {
        "file_comments": [
            {
                "file": "app.py",
                "line_start": 1,
                "line_end": 1,
                "severity": severity,
                "title": f"{severity} issue",
                "comment": "Fix it.",
                "code_snippet": "",
            }
            for severity in severities
        ],
        "review_summary": {"lgtm": not severities, "summary": "", "checklist": []},
    }


def _context():
    return {
        "title": "Fix thing",
        "body": "",
        "branch": "koan/fix-thing",
        "base": "main",
        "diff": "diff --git a/app.py b/app.py",
    }


def _cfg(
    enabled=True,
    max_rounds=3,
    min_severity="warning",
    budget_aware=False,
    dedup=False,
    tracker_max_age_days=30,
):
    # budget_aware/dedup default off so loop-behavior tests stay isolated from
    # the governor and the dedup tracker; Phase-2 tests opt in explicitly.
    return {
        "enabled": enabled,
        "max_rounds": max_rounds,
        "min_severity": min_severity,
        "budget_aware": budget_aware,
        "dedup": dedup,
        "tracker_max_age_days": tracker_max_age_days,
    }


class TestActionableFindings:
    def test_warning_includes_critical_and_warning(self):
        findings = _actionable_findings(
            _review("critical", "warning", "suggestion"),
            "warning",
        )
        assert [f["severity"] for f in findings] == ["critical", "warning"]

    def test_critical_only(self):
        findings = _actionable_findings(
            _review("critical", "warning"),
            "critical",
        )
        assert [f["severity"] for f in findings] == ["critical"]


class TestImplementationReviewGate:
    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(enabled=False),
    )
    def test_disabled_skips(self, _mock_cfg, tmp_path):
        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
        )

        assert result.ran is False
        assert result.clean is True
        assert "disabled" in result.skipped_reason

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(),
    )
    @patch("app.private_review_gate._push_current_branch")
    @patch("app.private_review_gate._fix_findings")
    @patch("app.private_review_gate._run_private_review")
    def test_clean_review_passes_without_fix(
        self, mock_review, mock_fix, mock_push, _mock_cfg, tmp_path,
    ):
        mock_review.return_value = (True, "ok", _review(), _context())

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.ran is True
        assert result.clean is True
        assert result.fixed_rounds == 0
        mock_fix.assert_not_called()
        mock_push.assert_not_called()

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(max_rounds=3),
    )
    @patch("app.private_review_gate._push_current_branch")
    @patch("app.private_review_gate._fix_findings", return_value=(True, "fixed"))
    @patch("app.private_review_gate._run_private_review")
    def test_fixes_then_rereviews_until_clean(
        self, mock_review, mock_fix, mock_push, _mock_cfg, tmp_path,
    ):
        mock_review.side_effect = [
            (True, "found", _review("warning"), _context()),
            (True, "clean", _review(), _context()),
        ]

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
            skill_origin="fix",
        )

        assert result.clean is True
        assert result.fixed_rounds == 1
        assert mock_review.call_count == 2
        mock_fix.assert_called_once()
        mock_push.assert_called_once()

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(max_rounds=3),
    )
    @patch("app.private_review_gate._fix_findings", return_value=(True, "fixed"))
    @patch("app.private_review_gate._run_private_review")
    def test_custom_push_callback_is_used(
        self, mock_review, mock_fix, _mock_cfg, tmp_path,
    ):
        mock_review.side_effect = [
            (True, "found", _review("warning"), _context()),
            (True, "clean", _review(), _context()),
        ]
        push_fn = MagicMock()

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
            skill_origin="rebase",
            push_fn=push_fn,
        )

        assert result.clean is True
        mock_fix.assert_called_once()
        push_fn.assert_called_once()

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(max_rounds=2),
    )
    @patch("app.private_review_gate._push_current_branch")
    @patch("app.private_review_gate._fix_findings", return_value=(True, "fixed"))
    @patch("app.private_review_gate._run_private_review")
    def test_exhausts_after_max_fix_rounds(
        self, mock_review, mock_fix, mock_push, _mock_cfg, tmp_path,
    ):
        mock_review.side_effect = [
            (True, "found", _review("warning"), _context()),
            (True, "still found", _review("critical"), _context()),
            (True, "final found", _review("warning"), _context()),
        ]

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.clean is False
        assert result.exhausted is True
        assert result.fixed_rounds == 2
        assert len(result.remaining_findings) == 1
        assert mock_review.call_count == 3
        assert mock_fix.call_count == 2
        assert mock_push.call_count == 2

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(),
    )
    @patch("app.private_review_gate._fix_findings", return_value=(False, "no changes"))
    @patch("app.private_review_gate._run_private_review")
    def test_stops_when_fix_step_produces_no_changes(
        self, mock_review, mock_fix, _mock_cfg, tmp_path,
    ):
        mock_review.return_value = (True, "found", _review("warning"), _context())

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.clean is False
        assert result.exhausted is False
        assert "no changes" in result.summary
        mock_fix.assert_called_once()

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(max_rounds=3),
    )
    @patch("app.private_review_gate._push_current_branch")
    @patch("app.private_review_gate._fix_findings", return_value=(True, "fixed"))
    @patch("app.private_review_gate._run_private_review")
    def test_bails_when_findings_do_not_change(
        self, mock_review, mock_fix, mock_push, _mock_cfg, tmp_path,
    ):
        # The fix does not change the findings between rounds -> converge bail
        # before burning the remaining rounds and the trailing review.
        mock_review.side_effect = [
            (True, "found", _review("warning"), _context()),
            (True, "still found", _review("warning"), _context()),
        ]

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.clean is False
        assert result.converged is True
        assert result.exhausted is False
        assert result.fixed_rounds == 1
        assert len(result.remaining_findings) == 1
        # Only two reviews (round 1 + round 2), one fix, no trailing review.
        assert mock_review.call_count == 2
        assert mock_fix.call_count == 1
        assert mock_push.call_count == 1

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(max_rounds=2),
    )
    @patch("app.private_review_gate._push_current_branch")
    @patch("app.private_review_gate._fix_findings", return_value=(True, "fixed"))
    @patch("app.private_review_gate._run_private_review")
    def test_trailing_review_passes_after_final_fix(
        self, mock_review, mock_fix, mock_push, _mock_cfg, tmp_path,
    ):
        # Findings change each round (no convergence), the loop runs to
        # max_rounds, and the trailing review verifies the final fix is clean.
        mock_review.side_effect = [
            (True, "found", _review("warning"), _context()),
            (True, "found more", _review("critical"), _context()),
            (True, "clean", _review(), _context()),
        ]

        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.clean is True
        assert result.converged is False
        assert result.fixed_rounds == 2
        # Two in-loop reviews + one trailing verification review.
        assert mock_review.call_count == 3
        assert mock_fix.call_count == 2
        assert mock_push.call_count == 2


class TestBudgetGating:
    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(budget_aware=True),
    )
    @patch(
        "app.private_review_gate._budget_preflight",
        return_value=(0, "budget near exhaustion (~12 min at current burn rate)"),
    )
    @patch("app.private_review_gate._run_private_review")
    def test_skips_when_budget_exhausted(
        self, mock_review, _mock_pre, _mock_cfg, tmp_path,
    ):
        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.ran is False
        assert "near exhaustion" in result.skipped_reason
        mock_review.assert_not_called()

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(budget_aware=True, max_rounds=3),
    )
    @patch(
        "app.private_review_gate._budget_preflight",
        return_value=(1, "governor mode=review — limiting to 1 round"),
    )
    @patch("app.private_review_gate._fix_findings", return_value=(True, "fixed"))
    @patch("app.private_review_gate._run_private_review")
    def test_reduces_rounds_under_pressure(
        self, mock_review, _mock_fix, _mock_pre, _mock_cfg, tmp_path,
    ):
        # max_rounds reduced to 1 -> a single review; findings remain -> the
        # one fix runs, then the loop ends (no further rounds).
        mock_review.side_effect = [
            (True, "found", _review("warning"), _context()),
            (True, "clean", _review(), _context()),
        ]

        with patch("app.private_review_gate._push_current_branch"):
            result = run_private_review_gate(
                project_path=str(tmp_path),
                project_name="app",
                pr_url="https://github.com/o/r/pull/42",
                notify_fn=MagicMock(),
            )

        # One in-loop review (round 1/1) + the trailing verification review.
        assert mock_review.call_count == 2
        assert result.fixed_rounds == 1

    @patch(
        "app.config.get_private_review_gate_config",
        return_value=_cfg(dedup=True),
    )
    @patch(
        "app.private_review_gate._dedup_precheck",
        return_value="already reviewed head abc12345 (clean)",
    )
    @patch("app.private_review_gate._run_private_review")
    def test_skips_when_head_already_reviewed_clean(
        self, mock_review, _mock_dedup, _mock_cfg, tmp_path,
    ):
        result = run_private_review_gate(
            project_path=str(tmp_path),
            project_name="app",
            pr_url="https://github.com/o/r/pull/42",
            notify_fn=MagicMock(),
        )

        assert result.ran is False
        assert "already reviewed" in result.skipped_reason
        mock_review.assert_not_called()


class TestBudgetPreflight:
    def _write_usage(self, tmp_path, session_pct):
        (tmp_path / "usage.md").write_text(
            f"Session (5hr) : {session_pct}% (reset in 3h)\n"
            "Weekly (7 day) : 10% (Resets in 3d)\n"
        )

    def _patches(self):
        return (
            patch("app.config.is_unlimited_quota", return_value=False),
            patch(
                "app.usage_tracker._get_budget_mode",
                return_value="session_only",
            ),
            patch(
                "app.usage_tracker._get_budget_thresholds",
                return_value=(70, 85),
            ),
        )

    def test_ample_budget_keeps_full_rounds(self, tmp_path):
        self._write_usage(tmp_path, 20)  # ~70% remaining -> deep
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            rounds, note = _budget_preflight(tmp_path, 3)
        assert rounds == 3
        assert note == ""

    def test_low_budget_reduces_rounds(self, tmp_path):
        self._write_usage(tmp_path, 70)  # ~20% remaining -> review
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            rounds, note = _budget_preflight(tmp_path, 3)
        assert rounds == 1
        assert "review" in note

    def test_exhausted_budget_skips(self, tmp_path):
        self._write_usage(tmp_path, 90)  # ~0% remaining -> wait
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            rounds, _note = _budget_preflight(tmp_path, 3)
        assert rounds == 0

    def test_unlimited_quota_bypasses(self, tmp_path):
        self._write_usage(tmp_path, 95)
        with patch("app.config.is_unlimited_quota", return_value=True):
            rounds, note = _budget_preflight(tmp_path, 3)
        assert rounds == 3
        assert note == ""

    def test_none_instance_dir_is_noop(self):
        assert _budget_preflight(None, 3) == (3, "")


class TestDedupTracker:
    def test_record_then_precheck_round_trip(self, tmp_path):
        cfg = {"dedup": True, "tracker_max_age_days": 30}
        with patch(
            "app.private_review_gate._pr_head_sha",
            return_value="abc1234567890",
        ):
            _maybe_record_clean(
                cfg=cfg,
                instance_dir=tmp_path,
                owner="o",
                repo="r",
                pr_number="42",
                project_path="x",
                rounds=2,
            )
            reason = _dedup_precheck(tmp_path, "o", "r", "42", "x", cfg)
        assert "already reviewed head abc12345" in reason

    def test_precheck_misses_on_different_head(self, tmp_path):
        cfg = {"dedup": True, "tracker_max_age_days": 30}
        with patch(
            "app.private_review_gate._pr_head_sha", return_value="aaa111",
        ):
            _maybe_record_clean(
                cfg=cfg, instance_dir=tmp_path, owner="o", repo="r",
                pr_number="42", project_path="x", rounds=1,
            )
        with patch(
            "app.private_review_gate._pr_head_sha", return_value="bbb222",
        ):
            reason = _dedup_precheck(tmp_path, "o", "r", "42", "x", cfg)
        assert reason == ""

    def test_precheck_empty_tracker_skips_sha_fetch(self, tmp_path):
        cfg = {"dedup": True, "tracker_max_age_days": 30}
        with patch(
            "app.private_review_gate._pr_head_sha",
        ) as mock_sha:
            reason = _dedup_precheck(tmp_path, "o", "r", "42", "x", cfg)
        assert reason == ""
        mock_sha.assert_not_called()


_SAMPLE_DIFF = (
    "diff --git a/src/auth.py b/src/auth.py\n"
    "index abc..def 100644\n"
    "--- a/src/auth.py\n"
    "+++ b/src/auth.py\n"
    "@@ -1,3 +1,3 @@\n"
    " import os\n"
    "+import jwt\n"
    "-import legacy\n"
    " UNIQUE_CONTEXT_LINE_XYZ\n"
    "diff --git a/tests/test_auth.py b/tests/test_auth.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tests/test_auth.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_x():\n"
    "+    assert True\n"
)


class TestDiffstatFromDiff:
    def test_parses_files_and_churn(self):
        stat = _diffstat_from_diff(_SAMPLE_DIFF)
        assert "src/auth.py | +1 -1" in stat
        assert "tests/test_auth.py | +2 -0" in stat
        assert "2 file(s) changed" in stat
        # The +++/--- header lines must not be counted as churn.
        # Context/hunk bodies must not leak into the stat.
        assert "UNIQUE_CONTEXT_LINE_XYZ" not in stat
        assert "import jwt" not in stat

    def test_empty_diff(self):
        assert _diffstat_from_diff("") == "(no diff available)"

    def test_diff_without_file_headers(self):
        assert _diffstat_from_diff("just some text\nno headers") == (
            "(no file changes detected)"
        )


class TestBuildFixPrompt:
    def _findings(self):
        return [{
            "file": "src/auth.py",
            "line_start": 2,
            "line_end": 2,
            "severity": "warning",
            "title": "Unvalidated token",
            "comment": "Validate the token before decoding.",
            "code_snippet": "jwt.decode(token)",
        }]

    def test_sends_diffstat_not_full_diff(self):
        context = {
            "title": "Add auth",
            "body": "short body",
            "branch": "koan/add-auth",
            "base": "main",
            "diff": _SAMPLE_DIFF,
        }
        prompt = _build_fix_prompt(context, self._findings(), "warning")

        # Diffstat (file list) is present; raw diff hunk bodies are not.
        assert "src/auth.py | +1 -1" in prompt
        assert "UNIQUE_CONTEXT_LINE_XYZ" not in prompt
        # Findings content reaches the prompt.
        assert "Unvalidated token" in prompt
        assert "jwt.decode(token)" in prompt

    def test_caps_long_body(self):
        context = {
            "title": "t",
            "body": "B" * 2000 + "BODY_TAIL_MARKER",
            "branch": "koan/x",
            "base": "main",
            "diff": _SAMPLE_DIFF,
        }
        prompt = _build_fix_prompt(context, self._findings(), "warning")

        assert "BODY_TAIL_MARKER" not in prompt
        assert "(truncated)" in prompt


class TestFormatGateNote:
    def test_none_result_is_empty(self):
        assert format_gate_note(None) == ""

    def test_not_run_is_empty(self):
        result = SimpleNamespace(ran=False, summary="skipped: disabled")
        assert format_gate_note(result) == ""

    def test_ran_result_renders_summary(self):
        result = SimpleNamespace(ran=True, summary="Private review gate passed")
        assert format_gate_note(result) == "\nPrivate gate: Private review gate passed"


class TestRunGateForSkill:
    def test_no_pr_url_does_not_run(self, tmp_path):
        with patch(
            "app.private_review_gate.run_private_review_gate",
        ) as mock_gate:
            result = run_gate_for_skill(
                project_path=str(tmp_path),
                project_name="app",
                pr_url="",
                skill_origin="fix",
            )
        assert result is None
        mock_gate.assert_not_called()

    def test_forwards_kwargs_to_gate(self, tmp_path):
        sentinel = SimpleNamespace(ran=True, summary="ok")
        with patch(
            "app.private_review_gate.run_private_review_gate",
            return_value=sentinel,
        ) as mock_gate:
            result = run_gate_for_skill(
                project_path=str(tmp_path),
                project_name="app",
                pr_url="https://github.com/o/r/pull/7",
                skill_origin="implement",
                plan_url="https://github.com/o/r/issues/3",
            )
        assert result is sentinel
        kwargs = mock_gate.call_args.kwargs
        assert kwargs["skill_origin"] == "implement"
        assert kwargs["pr_url"] == "https://github.com/o/r/pull/7"
        assert kwargs["plan_url"] == "https://github.com/o/r/issues/3"

    def test_gate_exception_is_swallowed_and_notified(self, tmp_path):
        notify = MagicMock()
        with patch(
            "app.private_review_gate.run_private_review_gate",
            side_effect=RuntimeError("boom"),
        ):
            result = run_gate_for_skill(
                project_path=str(tmp_path),
                project_name="app",
                pr_url="https://github.com/o/r/pull/7",
                skill_origin="fix",
                notify_fn=notify,
            )
        assert result is None
        assert notify.call_count == 1
        assert "failed after fix" in notify.call_args.args[0]
