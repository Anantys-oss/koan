"""Tests for the /ci_check queue-safety speedup (spec 005-ci-check-async-speedup).

Covers the two config accessors that bound the CI-fix step and the decoupling of
the per-mission fix budget from the ## CI total-attempt budget.
"""

from unittest.mock import patch

import pytest


PR_URL = "https://github.com/owner/repo/pull/42"
PROJECT_PATH = "/tmp/test-project"


class TestCiCheckConfigAccessors:
    """get_ci_check_step_timeout / get_ci_check_max_fix_attempts defaults + overrides."""

    def test_step_timeout_default(self):
        from app.config import get_ci_check_step_timeout

        with patch("app.config._load_config", return_value={}):
            assert get_ci_check_step_timeout() == 3600

    def test_step_timeout_override(self):
        from app.config import get_ci_check_step_timeout

        with patch("app.config._load_config", return_value={"ci_check": {"timeout": 1800}}):
            assert get_ci_check_step_timeout() == 1800

    def test_step_timeout_tolerates_bare_bool(self):
        """ci_check: true (bool shorthand) must fall back to the default, not crash."""
        from app.config import get_ci_check_step_timeout

        with patch("app.config._load_config", return_value={"ci_check": True}):
            assert get_ci_check_step_timeout() == 3600

    def test_step_timeout_tolerates_garbage(self):
        from app.config import get_ci_check_step_timeout

        with patch("app.config._load_config", return_value={"ci_check": {"timeout": "oops"}}):
            assert get_ci_check_step_timeout() == 3600

    def test_max_fix_attempts_default(self):
        from app.config import get_ci_check_max_fix_attempts

        with patch("app.config._load_config", return_value={}):
            assert get_ci_check_max_fix_attempts() == 1

    def test_max_fix_attempts_override(self):
        from app.config import get_ci_check_max_fix_attempts

        with patch(
            "app.config._load_config",
            return_value={"ci_check": {"max_fix_attempts_per_mission": 3}},
        ):
            assert get_ci_check_max_fix_attempts() == 3

    def test_max_fix_attempts_floored_at_one(self):
        """A configured 0 (or negative) must floor to 1 — never a no-op loop."""
        from app.config import get_ci_check_max_fix_attempts

        with patch(
            "app.config._load_config",
            return_value={"ci_check": {"max_fix_attempts_per_mission": 0}},
        ):
            assert get_ci_check_max_fix_attempts() == 1

    def test_idle_timeout_defaults_to_first_output_timeout(self):
        """Unset ci_check.idle_timeout must fall back to first_output_timeout."""
        from app.config import get_ci_check_idle_timeout

        with patch(
            "app.config._load_config",
            return_value={"first_output_timeout": 720},
        ):
            assert get_ci_check_idle_timeout() == 720

    def test_idle_timeout_override(self):
        from app.config import get_ci_check_idle_timeout

        with patch(
            "app.config._load_config",
            return_value={
                "first_output_timeout": 720,
                "ci_check": {"idle_timeout": 90},
            },
        ):
            assert get_ci_check_idle_timeout() == 90

    def test_idle_timeout_zero_disables_guard(self):
        """A configured 0 disables the idle guard (overall cap still bounds)."""
        from app.config import get_ci_check_idle_timeout

        with patch(
            "app.config._load_config",
            return_value={"ci_check": {"idle_timeout": 0}},
        ):
            assert get_ci_check_idle_timeout() == 0


@pytest.fixture
def _mock_pr_context():
    """Patch externals so run_ci_check_and_fix runs without real git/GitHub."""
    fake_context = {"branch": "fix-branch", "base": "main", "url": PR_URL}
    with (
        patch("app.rebase_pr.fetch_pr_context", return_value=fake_context),
        patch(
            "app.ci_queue_runner.check_ci_status",
            return_value=("failure", 123, "Error: test failed"),
        ),
        patch("app.rebase_pr.check_pr_state", return_value=("OPEN", "MERGEABLE")),
        patch("app.claude_step._get_current_branch", return_value="main"),
        patch("app.claude_step._run_git"),
        patch("app.claude_step._safe_checkout"),
        patch("app.claude_step._fetch_branch"),
        patch("app.rebase_pr._find_remote_for_repo", return_value="origin"),
        patch("app.git_utils.ordered_remotes", return_value=["origin"]),
    ):
        yield


class TestPerMissionAttemptDecoupling:
    """The per-mission internal loop uses get_ci_check_max_fix_attempts(), NOT the
    ## CI total budget (ci_fix_max_attempts)."""

    @pytest.mark.usefixtures("_mock_pr_context")
    def test_per_mission_attempts_default_one(self):
        from app.ci_queue_runner import run_ci_check_and_fix

        with (
            # ## CI total budget is 5, but the mission must only do 1 attempt.
            patch("app.utils.load_config", return_value={"ci_fix_max_attempts": 5}),
            patch("app.config.get_ci_check_max_fix_attempts", return_value=1) as mock_cap,
            patch(
                "app.ci_queue_runner._attempt_ci_fixes", return_value=True
            ) as mock_attempt,
        ):
            run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        assert mock_cap.called
        assert mock_attempt.call_args[1]["max_attempts"] == 1

    @pytest.mark.usefixtures("_mock_pr_context")
    def test_per_mission_attempts_honor_override(self):
        from app.ci_queue_runner import run_ci_check_and_fix

        with (
            patch("app.utils.load_config", return_value={"ci_fix_max_attempts": 5}),
            patch("app.config.get_ci_check_max_fix_attempts", return_value=2),
            patch(
                "app.ci_queue_runner._attempt_ci_fixes", return_value=True
            ) as mock_attempt,
        ):
            run_ci_check_and_fix(PR_URL, PROJECT_PATH)

        # Uses the ci_check cap (2), not the ## CI budget (5).
        assert mock_attempt.call_args[1]["max_attempts"] == 2
