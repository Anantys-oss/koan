"""Tests for app.ai_runner — AI exploration CLI runner."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.ai_runner import (
    run_exploration,
    _clean_response,
    _extract_missions,
    _has_similar_mission,
    _is_similar_mission,
    _mission_words,
    _normalize_mission_text,
    _strip_mission_lines,
    _queue_missions,
    main,
)


# ---------------------------------------------------------------------------
# _clean_response (delegates to text_utils.clean_cli_response)
# ---------------------------------------------------------------------------

class TestCleanResponse:
    def test_strips_markdown_decorators(self):
        text = "### Header\n**bold** and __underline__"
        cleaned = _clean_response(text)
        assert "###" not in cleaned
        assert "**" not in cleaned
        assert "__" not in cleaned

    def test_strips_code_fences(self):
        text = "```python\nprint('hello')\n```"
        cleaned = _clean_response(text)
        assert "```" not in cleaned

    def test_strips_max_turns_error(self):
        text = "Error: max turns reached\nGood content here"
        cleaned = _clean_response(text)
        assert "max turns" not in cleaned
        assert "Good content" in cleaned

    def test_truncates_long_output(self):
        text = "x" * 3000
        cleaned = _clean_response(text)
        assert len(cleaned) <= 2000
        assert cleaned.endswith("...")

    def test_preserves_short_output(self):
        text = "Short and sweet"
        cleaned = _clean_response(text)
        assert cleaned == "Short and sweet"


# ---------------------------------------------------------------------------
# run_command (provider-level helper, tested via ai_runner integration)
# ---------------------------------------------------------------------------

class TestRunCommand:
    """Tests for the shared run_command helper in app.provider."""

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Exploration results", stderr=""
        )
        result = run_command("test prompt", "/tmp", allowed_tools=["Read"])
        assert result == "Exploration results"

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_raises_on_failure(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="quota exceeded"
        )
        with pytest.raises(RuntimeError, match="CLI invocation failed"):
            run_command("test prompt", "/tmp", allowed_tools=["Read"])

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_passes_allowed_tools(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_command("test", "/tmp", allowed_tools=["Read", "Glob", "Grep", "Bash"])
        call_kwargs = mock_cmd.call_args[1]
        assert "Read" in call_kwargs["allowed_tools"]
        assert "Bash" in call_kwargs["allowed_tools"]

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_passes_max_turns(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_command("test", "/tmp", allowed_tools=["Read"], max_turns=5)
        call_kwargs = mock_cmd.call_args[1]
        assert call_kwargs["max_turns"] == 5

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_sets_cwd_to_project_path(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        run_command("test", "/my/project", allowed_tools=["Read"])
        assert mock_run.call_args[1]["cwd"] == "/my/project"

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_strips_max_turns_error_from_output(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Error: Reached max turns (1)",
            stderr="",
        )
        result = run_command("test", "/tmp", allowed_tools=[])
        assert result == ""

    @patch("app.config.get_model_config", return_value={"chat": "sonnet", "fallback": ""})
    @patch("app.provider.build_full_command", return_value=["claude", "-p", "test"])
    @patch("app.provider.subprocess.run")
    def test_strips_max_turns_preserves_real_content(self, mock_run, mock_cmd, mock_model):
        from app.cli_provider import run_command
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Real output here\nError: Reached max turns (5)\n",
            stderr="",
        )
        result = run_command("test", "/tmp", allowed_tools=[])
        assert result == "Real output here"


# ---------------------------------------------------------------------------
# run_exploration
# ---------------------------------------------------------------------------

class TestRunExploration:
    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_success_returns_true(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        assert "completed" in summary.lower()

    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_notifies_start_and_result(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert notify.call_count == 2
        # First call: "Exploring myapp..."
        assert "Exploring" in notify.call_args_list[0][0][0]
        # Second call: exploration result
        assert "myapp" in notify.call_args_list[1][0][0]

    @patch("app.cli_provider.run_command_streaming", side_effect=RuntimeError("quota exceeded"))
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_failure_returns_false(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is False
        assert "failed" in summary.lower()

    @patch("app.cli_provider.run_command_streaming", return_value="")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_empty_result_returns_false(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is False
        assert "empty" in summary.lower()

    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_loads_prompt_from_skill_dir(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify, skill_dir=custom_dir,
        )
        assert mock_prompt.call_args[0][0] == custom_dir
        assert mock_prompt.call_args[0][1] == "ai-explore"

    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_prompt_substitutions(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        """Prompt should receive PROJECT_NAME, GIT_ACTIVITY, etc."""
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        kwargs = mock_prompt.call_args[1]
        assert kwargs["PROJECT_NAME"] == "myapp"
        assert "GIT_ACTIVITY" in kwargs
        assert "PROJECT_STRUCTURE" in kwargs
        assert "MISSIONS_CONTEXT" in kwargs

    @patch("app.cli_provider.run_command_streaming", return_value="x" * 3000)
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_truncates_telegram_output(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        result_msg = notify.call_args_list[1][0][0]
        assert len(result_msg) <= 2100  # header + 2000 content

    @patch("app.config.get_skill_timeout", return_value=999)
    @patch("app.config.get_skill_max_turns", return_value=42)
    @patch("app.cli_provider.run_command_streaming", return_value="Found issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_max_turns_uses_skill_config(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        mock_max_turns, mock_timeout, tmp_path
    ):
        """ai_runner must read skill_max_turns/skill_timeout from app.config.

        Previously hardcoded max_turns=10, timeout=600 — too low for real
        exploration of large projects, and not adjustable via instance
        config. Now defers to get_skill_max_turns()/get_skill_timeout()
        like /implement, /fix, /incident, etc.
        """
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        call_kwargs = mock_claude.call_args[1]
        assert call_kwargs["max_turns"] == 42
        assert call_kwargs["timeout"] == 999


# ---------------------------------------------------------------------------
# _extract_missions
# ---------------------------------------------------------------------------

class TestExtractMissions:
    def test_extracts_mission_lines(self):
        text = (
            "Found some issues:\n"
            "MISSION: Fix the retry logic in fetch_data()\n"
            "MISSION: Add input validation for user email\n"
            "Some other text\n"
        )
        missions = _extract_missions(text, "myapp")
        assert len(missions) == 2
        assert missions[0] == "- [project:myapp] Fix the retry logic in fetch_data()"
        assert missions[1] == "- [project:myapp] Add input validation for user email"

    def test_no_mission_lines(self):
        text = "No issues found. Everything looks good."
        missions = _extract_missions(text, "myapp")
        assert missions == []

    def test_ignores_empty_mission_lines(self):
        text = "MISSION: \nMISSION:   \nMISSION: Real task"
        missions = _extract_missions(text, "myapp")
        assert len(missions) == 1
        assert "Real task" in missions[0]

    def test_strips_whitespace(self):
        text = "  MISSION:   Fix whitespace issue  \n"
        missions = _extract_missions(text, "myapp")
        assert len(missions) == 1
        assert missions[0] == "- [project:myapp] Fix whitespace issue"

    def test_uses_project_name_in_tag(self):
        text = "MISSION: Do something"
        missions = _extract_missions(text, "backend")
        assert missions[0].startswith("- [project:backend]")

    def test_ignores_non_mission_lines_with_mission_word(self):
        text = "The MISSION: is clear\nMISSION: Actual task"
        missions = _extract_missions(text, "myapp")
        assert len(missions) == 1
        assert "Actual task" in missions[0]

    def test_strips_duplicate_project_tag(self):
        text = "MISSION: [project:myapp] Fix the bug"
        missions = _extract_missions(text, "myapp")
        assert len(missions) == 1
        assert missions[0] == "- [project:myapp] Fix the bug"

    def test_strips_different_project_tag(self):
        """Claude might hallucinate a different project tag — replace it."""
        text = "MISSION: [project:wrong] Fix the bug"
        missions = _extract_missions(text, "myapp")
        assert missions[0] == "- [project:myapp] Fix the bug"

    def test_strips_leading_bullet(self):
        text = "MISSION: - Fix the bug"
        missions = _extract_missions(text, "myapp")
        assert missions[0] == "- [project:myapp] Fix the bug"

    def test_strips_bullet_and_tag_combined(self):
        text = "MISSION: - [project:myapp] Fix the bug"
        missions = _extract_missions(text, "myapp")
        assert missions[0] == "- [project:myapp] Fix the bug"


# ---------------------------------------------------------------------------
# _strip_mission_lines
# ---------------------------------------------------------------------------

class TestStripMissionLines:
    def test_removes_mission_lines(self):
        text = "Report here\nMISSION: Fix something\nMore report"
        result = _strip_mission_lines(text)
        assert "MISSION:" not in result
        assert "Report here" in result
        assert "More report" in result

    def test_no_mission_lines(self):
        text = "Just a normal report"
        result = _strip_mission_lines(text)
        assert result == "Just a normal report"

    def test_strips_trailing_whitespace(self):
        text = "Report\nMISSION: Task\n\n\n"
        result = _strip_mission_lines(text)
        assert result == "Report"


# ---------------------------------------------------------------------------
# _queue_missions
# ---------------------------------------------------------------------------

class TestQueueMissions:
    def test_inserts_each_mission(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")
        missions = [
            "- [project:myapp] Fix bug A",
            "- [project:myapp] Fix bug B",
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 2
        content = missions_path.read_text()
        assert "Fix bug A" in content
        assert "Fix bug B" in content

    def test_no_missions_returns_zero(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        queued = _queue_missions(missions_path, [])
        assert queued == 0

    def test_skips_duplicate_in_pending(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "## Pending\n"
            "- [project:myapp] Refactor the authentication module to use dependency injection\n\n"
            "# In Progress\n\n# Done\n"
        )
        missions = [
            "- [project:myapp] Refactor the authentication module to use dependency injection",
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 0

    def test_skips_similar_in_pending(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "## Pending\n"
            "- [project:myapp] Refactor the auth module to use dependency injection\n\n"
            "# In Progress\n\n# Done\n"
        )
        missions = [
            # Similar but rephrased
            "- [project:myapp] Refactor auth module for dependency injection pattern",
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 0

    def test_allows_distinct_missions(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "## Pending\n"
            "- [project:myapp] Fix the login page CSS alignment\n\n"
            "# In Progress\n\n# Done\n"
        )
        missions = [
            "- [project:myapp] Add unit tests for the payment processing module",
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 1

    def test_intra_batch_dedup(self, tmp_path):
        """Identical missions within the same batch should not all be queued."""
        missions_path = tmp_path / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")
        missions = [
            "- [project:myapp] Fix the retry logic in fetch_data()",
            "- [project:myapp] Fix the retry logic in fetch_data()",
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 1

    def test_skips_similar_in_progress(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "# Pending\n\n"
            "## In Progress\n"
            "- [project:myapp] Add input validation for the user email field\n\n"
            "## Done\n"
        )
        missions = [
            "- [project:myapp] Add input validation for user email field",
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 0

    def test_returns_correct_count_mixed(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "## Pending\n"
            "- [project:myapp] Fix the retry logic in fetch_data()\n\n"
            "# In Progress\n\n# Done\n"
        )
        missions = [
            "- [project:myapp] Fix the retry logic in fetch_data()",  # dup
            "- [project:myapp] Add caching to the search endpoint",  # new
        ]
        queued = _queue_missions(missions_path, missions)
        assert queued == 1

    def test_creates_file_if_missing(self, tmp_path):
        missions_path = tmp_path / "missions.md"
        missions = ["- [project:myapp] Fix bug A"]
        queued = _queue_missions(missions_path, missions)
        assert queued == 1
        assert missions_path.exists()


# ---------------------------------------------------------------------------
# _normalize_mission_text / _mission_words / _is_similar_mission
# ---------------------------------------------------------------------------

class TestNormalizeMissionText:
    def test_strips_project_tag(self):
        result = _normalize_mission_text("- [project:myapp] Fix the bug")
        assert result == "fix the bug"

    def test_strips_timestamp(self):
        result = _normalize_mission_text("Fix the bug ⏳(2026-05-23T04:24)")
        assert result == "fix the bug"

    def test_strips_slash_command(self):
        result = _normalize_mission_text("/fix https://github.com/o/r/issues/1")
        assert result == "https://github.com/o/r/issues/1"

    def test_normalizes_whitespace(self):
        result = _normalize_mission_text("  Fix   the    bug  ")
        assert result == "fix the bug"

    def test_combined_metadata(self):
        result = _normalize_mission_text(
            "- [project:myapp] Refactor auth module ⏳(2026-05-23T04:24)"
        )
        assert result == "refactor auth module"


class TestMissionWords:
    def test_extracts_significant_words(self):
        words = _mission_words("- [project:myapp] Fix the retry logic")
        assert "retry" in words
        assert "logic" in words
        # Short words (< 4 chars) filtered out
        assert "fix" not in words
        assert "the" not in words

    def test_includes_underscored_identifiers(self):
        words = _mission_words("Refactor fetch_data function")
        assert "fetch_data" in words
        assert "refactor" in words

    def test_empty_returns_empty(self):
        assert _mission_words("") == set()


class TestIsSimilarMission:
    def test_identical_missions(self):
        a = "- [project:myapp] Fix the retry logic in fetch_data()"
        b = "- [project:myapp] Fix the retry logic in fetch_data()"
        assert _is_similar_mission(a, b) is True

    def test_different_project_tag_same_text(self):
        a = "- [project:myapp] Refactor the authentication module for better testing"
        b = "- [project:other] Refactor the authentication module for better testing"
        assert _is_similar_mission(a, b) is True

    def test_completely_different(self):
        a = "- [project:myapp] Fix CSS alignment on login page"
        b = "- [project:myapp] Add unit tests for payment processing"
        assert _is_similar_mission(a, b) is False

    def test_minor_rephrasing(self):
        a = "- [project:myapp] Refactor the auth module to use dependency injection"
        b = "- [project:myapp] Refactor auth module for dependency injection pattern"
        assert _is_similar_mission(a, b) is True

    def test_short_missions_not_falsely_matched(self):
        """Short missions with few overlapping words should not match."""
        a = "- [project:myapp] Fix the bug"
        b = "- [project:myapp] Fix the test"
        assert _is_similar_mission(a, b) is False

    def test_empty_text_returns_false(self):
        assert _is_similar_mission("", "something") is False
        assert _is_similar_mission("something", "") is False


class TestHasSimilarMission:
    def test_finds_match(self):
        existing = [
            "- [project:myapp] Add caching to the search endpoint",
            "- [project:myapp] Fix the retry logic in fetch_data()",
        ]
        assert _has_similar_mission(
            "- [project:myapp] Fix the retry logic in fetch_data()", existing
        ) is True

    def test_no_match(self):
        existing = [
            "- [project:myapp] Add caching to the search endpoint",
        ]
        assert _has_similar_mission(
            "- [project:myapp] Fix the retry logic in fetch_data()", existing
        ) is False

    def test_empty_existing(self):
        assert _has_similar_mission(
            "- [project:myapp] Fix the bug", []
        ) is False


# ---------------------------------------------------------------------------
# run_exploration with missions
# ---------------------------------------------------------------------------

class TestRunExplorationWithMissions:
    @patch("app.cli_provider.run_command_streaming",
           return_value="Found issues\nMISSION: Fix bug A\nMISSION: Fix bug B")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_queues_missions_from_output(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, tmp_path
    ):
        # Create missions.md so _queue_missions can read it
        missions_path = tmp_path / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        assert "2 missions queued" in summary

    @patch("app.cli_provider.run_command_streaming",
           return_value="Found issues\nMISSION: Fix bug A\nMISSION: Fix bug B")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_telegram_shows_mission_count(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, tmp_path
    ):
        missions_path = tmp_path / "missions.md"
        missions_path.write_text("# Pending\n\n# In Progress\n\n# Done\n")

        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        result_msg = notify.call_args_list[1][0][0]
        assert "2 mission(s) queued" in result_msg
        assert "MISSION:" not in result_msg

    @patch("app.cli_provider.run_command_streaming",
           return_value=(
               "Found issues\n"
               "MISSION: Refactor authentication module for better error handling\n"
               "MISSION: Add caching layer to search endpoint\n"
           ))
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_telegram_shows_skipped_count(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, tmp_path
    ):
        # Pre-populate with one existing mission that matches (same words)
        missions_path = tmp_path / "missions.md"
        missions_path.write_text(
            "## Pending\n"
            "- [project:myapp] Refactor authentication module for better error handling\n\n"
            "## In Progress\n\n## Done\n"
        )

        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        result_msg = notify.call_args_list[1][0][0]
        assert "1 mission(s) queued" in result_msg
        assert "1 duplicate(s) skipped" in result_msg

    @patch("app.cli_provider.run_command_streaming", return_value="No issues found")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_no_missions_no_suffix(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        assert "0 missions queued" in summary
        result_msg = notify.call_args_list[1][0][0]
        assert "mission(s) queued" not in result_msg


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

class TestCLI:
    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_success_returns_0(self, mock_run):
        exit_code = main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        assert exit_code == 0
        mock_run.assert_called_once()

    @patch("app.ai_runner.run_exploration", return_value=(False, "Failed"))
    def test_main_failure_returns_1(self, mock_run):
        exit_code = main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        assert exit_code == 1

    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_passes_correct_args(self, mock_run):
        main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        kwargs = mock_run.call_args[1]
        assert kwargs["project_path"] == "/tmp/myapp"
        assert kwargs["project_name"] == "myapp"
        assert kwargs["instance_dir"] == "/tmp/instance"

    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_sets_skill_dir(self, mock_run):
        main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        kwargs = mock_run.call_args[1]
        skill_dir = kwargs["skill_dir"]
        assert skill_dir.name == "ai"
        assert "skills/core/ai" in str(skill_dir)

    def test_main_requires_project_path(self):
        with pytest.raises(SystemExit):
            main(["--project-name", "myapp", "--instance-dir", "/tmp"])

    def test_main_requires_project_name(self):
        with pytest.raises(SystemExit):
            main(["--project-path", "/tmp", "--instance-dir", "/tmp"])

    def test_main_requires_instance_dir(self):
        with pytest.raises(SystemExit):
            main(["--project-path", "/tmp", "--project-name", "myapp"])
