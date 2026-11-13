"""Tests for instance.example/hooks/slim_review_post.py."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def slim_review_module():
    """Import the hook module from instance.example/hooks/."""
    module_path = (
        Path(__file__).parent.parent.parent
        / "instance.example" / "hooks" / "slim_review_post.py"
    )
    spec = importlib.util.spec_from_file_location(
        "slim_review_post_test", module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def prompt_file(slim_review_module):
    """Ensure the prompt file exists next to the hook module."""
    prompt_path = Path(slim_review_module.__file__).parent / "slim_review_prompt.md"
    assert prompt_path.exists(), f"Prompt file missing: {prompt_path}"
    return prompt_path


def _make_ctx(instance_dir, **overrides):
    """Build a minimal post_mission context dict."""
    ctx = {
        "instance_dir": instance_dir,
        "project_name": "test-project",
        "project_path": "/tmp/test-project",
        "exit_code": 0,
        "mission_title": "implement feature X",
        "duration_minutes": 5,
        "result": {},
        "result_text": "Created PR https://github.com/owner/repo/pull/42",
    }
    ctx.update(overrides)
    return ctx


class TestConfigGating:
    def test_disabled_by_default(self, slim_review_module, instance_dir):
        """Hook is inert when config key is absent."""
        with patch.object(slim_review_module, "_run_slim_review") as mock_run:
            with patch("app.utils.load_config", return_value={}):
                slim_review_module.on_post_mission(
                    _make_ctx(instance_dir),
                )
        mock_run.assert_not_called()

    def test_disabled_explicitly(self, slim_review_module, instance_dir):
        """Hook is inert when enabled is false."""
        config = {"slim_review_hook": {"enabled": False}}
        with patch.object(slim_review_module, "_run_slim_review") as mock_run:
            with patch("app.utils.load_config", return_value=config):
                slim_review_module.on_post_mission(
                    _make_ctx(instance_dir),
                )
        mock_run.assert_not_called()

    def test_enabled(self, slim_review_module, instance_dir):
        """Hook dispatches when enabled is true."""
        config = {"slim_review_hook": {"enabled": True}}
        with patch.object(slim_review_module, "_run_slim_review"):
            with patch("app.utils.load_config", return_value=config):
                with patch.object(
                    slim_review_module.threading, "Thread",
                ) as mock_thread:
                    mock_thread.return_value = MagicMock()
                    slim_review_module.on_post_mission(
                        _make_ctx(instance_dir),
                    )
        mock_thread.assert_called_once()


class TestSkipConditions:
    def test_nonzero_exit_code(self, slim_review_module, instance_dir):
        """Skip on failed missions."""
        config = {"slim_review_hook": {"enabled": True}}
        with patch.object(slim_review_module, "_run_slim_review") as mock_run:
            with patch("app.utils.load_config", return_value=config):
                slim_review_module.on_post_mission(
                    _make_ctx(instance_dir, exit_code=1),
                )
        mock_run.assert_not_called()

    @pytest.mark.parametrize("title", [
        "/review https://github.com/o/r/pull/1",
        "/rebase https://github.com/o/r/pull/1",
        "/slim_review https://github.com/o/r/pull/1",
        "/review_rebase https://github.com/o/r/pull/1",
        "[project:foo] /review https://github.com/o/r/pull/1",
    ])
    def test_skip_review_missions(self, slim_review_module, instance_dir, title):
        """Skip review/rebase/slim_review missions to prevent loops."""
        config = {"slim_review_hook": {"enabled": True}}
        with patch.object(slim_review_module, "_run_slim_review") as mock_run:
            with patch("app.utils.load_config", return_value=config):
                slim_review_module.on_post_mission(
                    _make_ctx(instance_dir, mission_title=title),
                )
        mock_run.assert_not_called()

    def test_no_pr_url(self, slim_review_module, instance_dir):
        """Skip when result_text has no PR URL."""
        config = {"slim_review_hook": {"enabled": True}}
        with patch.object(slim_review_module, "_run_slim_review") as mock_run:
            with patch("app.utils.load_config", return_value=config):
                slim_review_module.on_post_mission(
                    _make_ctx(instance_dir, result_text="no pr here"),
                )
        mock_run.assert_not_called()


class TestDedup:
    def test_skip_same_diff_hash(self, slim_review_module, instance_dir, prompt_file):
        """Same diff hash skips Claude call."""
        import hashlib

        diff_text = "diff --git a/foo.py b/foo.py\n+hello"
        diff_hash = hashlib.sha256(diff_text.encode()).hexdigest()
        pr_url = "https://github.com/owner/repo/pull/42"

        tracker_path = Path(instance_dir) / ".slim-review-tracker.json"
        tracker_path.write_text(json.dumps({pr_url: diff_hash}))

        with patch("app.github.run_gh", return_value=diff_text):
            with patch("app.cli_exec.run_cli_with_retry") as mock_cli:
                slim_review_module._run_slim_review_inner(
                    instance_dir, "test-project", "/tmp/p", pr_url,
                )
        mock_cli.assert_not_called()

    def test_new_diff_triggers_review(self, slim_review_module, instance_dir, prompt_file):
        """Changed diff hash triggers Claude call and updates tracker."""
        pr_url = "https://github.com/owner/repo/pull/42"
        diff_text = "diff --git a/foo.py b/foo.py\n+new change"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "- **[warning]** `foo.py:1` — potential issue"
        mock_result.stderr = ""

        with patch("app.github.run_gh", return_value=diff_text):
            with patch("app.cli_exec.run_cli_with_retry", return_value=mock_result):
                with patch("app.cli_provider.build_full_command", return_value=["claude"]):
                    with patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"}):
                        with patch("app.journal.append_to_journal") as mock_journal:
                            slim_review_module._run_slim_review_inner(
                                instance_dir, "test-project", "/tmp/p", pr_url,
                            )

        mock_journal.assert_called_once()
        journal_entry = mock_journal.call_args[0][2]
        assert "Slim Review" in journal_entry
        assert pr_url in journal_entry
        assert "potential issue" in journal_entry

        tracker = json.loads(
            (Path(instance_dir) / ".slim-review-tracker.json").read_text(),
        )
        assert pr_url in tracker


class TestJournalOutput:
    def test_journal_entry_format(self, slim_review_module, instance_dir, prompt_file):
        """Journal entry contains PR URL and findings."""
        pr_url = "https://github.com/owner/repo/pull/99"
        findings = "- **[critical]** `auth.py:42` — SQL injection"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = findings
        mock_result.stderr = ""

        with patch("app.github.run_gh", return_value="diff content"):
            with patch("app.cli_exec.run_cli_with_retry", return_value=mock_result):
                with patch("app.cli_provider.build_full_command", return_value=["claude"]):
                    with patch("app.config.get_model_config", return_value={"lightweight": "haiku"}):
                        with patch("app.journal.append_to_journal") as mock_journal:
                            slim_review_module._run_slim_review_inner(
                                instance_dir, "test-project", "/tmp/p", pr_url,
                            )

        entry = mock_journal.call_args[0][2]
        assert "### Slim Review" in entry
        assert pr_url in entry
        assert "SQL injection" in entry

    def test_empty_findings_no_journal(self, slim_review_module, instance_dir, prompt_file):
        """Empty Claude output produces no journal entry."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("app.github.run_gh", return_value="diff content"):
            with patch("app.cli_exec.run_cli_with_retry", return_value=mock_result):
                with patch("app.cli_provider.build_full_command", return_value=["claude"]):
                    with patch("app.config.get_model_config", return_value={"lightweight": "haiku"}):
                        with patch("app.journal.append_to_journal") as mock_journal:
                            slim_review_module._run_slim_review_inner(
                                instance_dir, "test-project", "/tmp/p",
                                "https://github.com/o/r/pull/1",
                            )
        mock_journal.assert_not_called()


class TestErrorHandling:
    def test_gh_diff_failure(self, slim_review_module, instance_dir):
        """gh pr diff failure is caught and logged."""
        with patch("app.github.run_gh", side_effect=RuntimeError("404")):
            slim_review_module._run_slim_review_inner(
                instance_dir, "test-project", "/tmp/p",
                "https://github.com/o/r/pull/1",
            )
        # No exception raised — error is caught

    def test_cli_failure(self, slim_review_module, instance_dir, prompt_file):
        """Claude CLI failure is caught, no journal written."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch("app.github.run_gh", return_value="diff"):
            with patch("app.cli_exec.run_cli_with_retry", return_value=mock_result):
                with patch("app.cli_provider.build_full_command", return_value=["claude"]):
                    with patch("app.config.get_model_config", return_value={"lightweight": "haiku"}):
                        with patch("app.journal.append_to_journal") as mock_journal:
                            slim_review_module._run_slim_review_inner(
                                instance_dir, "test-project", "/tmp/p",
                                "https://github.com/o/r/pull/1",
                            )
        mock_journal.assert_not_called()
