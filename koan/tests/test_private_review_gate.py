"""Tests for the private post-implementation review gate."""

from unittest.mock import MagicMock, patch

from app.private_review_gate import (
    _actionable_findings,
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


def _cfg(enabled=True, max_rounds=3, min_severity="warning"):
    return {
        "enabled": enabled,
        "max_rounds": max_rounds,
        "min_severity": min_severity,
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
