"""Tests for app.ai_runner — AI exploration CLI runner."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.ai_runner import (
    AIFinding,
    parse_findings,
    prioritize_findings,
    run_exploration,
    _build_project_health_block,
    _clean_response,
    _extract_missions,
    _extract_missions_legacy,
    _findings_to_missions,
    _strip_mission_lines,
    _strip_structured_output,
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
# AIFinding data class
# ---------------------------------------------------------------------------

class TestAIFinding:
    def test_defaults(self):
        f = AIFinding()
        assert f.title == ""
        assert f.impact == "medium"
        assert f.effort == "medium"
        assert f.category == ""
        assert f.location == ""
        assert f.description == ""

    def test_is_valid_requires_title_and_description(self):
        assert AIFinding(title="Fix bug", description="It breaks").is_valid()
        assert not AIFinding(title="Fix bug").is_valid()
        assert not AIFinding(description="It breaks").is_valid()
        assert not AIFinding().is_valid()


# ---------------------------------------------------------------------------
# parse_findings
# ---------------------------------------------------------------------------

class TestParseFindings:
    def test_parses_single_block(self):
        text = (
            "---IDEA---\n"
            "TITLE: Fix retry logic\n"
            "IMPACT: high\n"
            "EFFORT: quick_win\n"
            "CATEGORY: quality\n"
            "LOCATION: src/client.py:42\n"
            "DESCRIPTION: The retry wrapper swallows errors silently.\n"
        )
        findings = parse_findings(text)
        assert len(findings) == 1
        f = findings[0]
        assert f.title == "Fix retry logic"
        assert f.impact == "high"
        assert f.effort == "quick_win"
        assert f.category == "quality"
        assert f.location == "src/client.py:42"
        assert "retry wrapper" in f.description

    def test_parses_multiple_blocks(self):
        text = (
            "Some preamble text\n"
            "---IDEA---\n"
            "TITLE: First idea\n"
            "IMPACT: high\n"
            "EFFORT: medium\n"
            "CATEGORY: perf\n"
            "LOCATION: src/a.py:1\n"
            "DESCRIPTION: First description.\n"
            "---IDEA---\n"
            "TITLE: Second idea\n"
            "IMPACT: low\n"
            "EFFORT: significant\n"
            "CATEGORY: feature\n"
            "LOCATION: src/b.py:2\n"
            "DESCRIPTION: Second description.\n"
        )
        findings = parse_findings(text)
        assert len(findings) == 2
        assert findings[0].title == "First idea"
        assert findings[1].title == "Second idea"

    def test_skips_invalid_blocks(self):
        text = (
            "---IDEA---\n"
            "TITLE: Valid idea\n"
            "DESCRIPTION: Has both fields.\n"
            "---IDEA---\n"
            "TITLE: Missing description\n"
            "---IDEA---\n"
            "DESCRIPTION: Missing title.\n"
        )
        findings = parse_findings(text)
        assert len(findings) == 1
        assert findings[0].title == "Valid idea"

    def test_multiline_description(self):
        text = (
            "---IDEA---\n"
            "TITLE: Complex issue\n"
            "IMPACT: medium\n"
            "EFFORT: medium\n"
            "CATEGORY: quality\n"
            "LOCATION: src/x.py:10\n"
            "DESCRIPTION: First line of description.\n"
            "Second line continues here.\n"
        )
        findings = parse_findings(text)
        assert len(findings) == 1
        assert "First line" in findings[0].description
        assert "Second line" in findings[0].description

    def test_no_idea_blocks_returns_empty(self):
        text = "Just a regular report with no structured blocks."
        findings = parse_findings(text)
        assert findings == []

    def test_defaults_for_missing_optional_fields(self):
        text = (
            "---IDEA---\n"
            "TITLE: Minimal idea\n"
            "DESCRIPTION: Just title and description.\n"
        )
        findings = parse_findings(text)
        assert len(findings) == 1
        assert findings[0].impact == "medium"
        assert findings[0].effort == "medium"
        assert findings[0].category == ""
        assert findings[0].location == ""


# ---------------------------------------------------------------------------
# prioritize_findings
# ---------------------------------------------------------------------------

class TestPrioritizeFindings:
    def test_sorts_by_impact(self):
        findings = [
            AIFinding(title="low", impact="low", description="d"),
            AIFinding(title="high", impact="high", description="d"),
            AIFinding(title="medium", impact="medium", description="d"),
        ]
        result = prioritize_findings(findings)
        assert [f.title for f in result] == ["high", "medium", "low"]

    def test_preserves_order_for_same_impact(self):
        findings = [
            AIFinding(title="first", impact="medium", description="d"),
            AIFinding(title="second", impact="medium", description="d"),
        ]
        result = prioritize_findings(findings)
        assert [f.title for f in result] == ["first", "second"]

    def test_unknown_impact_sorts_last(self):
        findings = [
            AIFinding(title="unknown", impact="critical", description="d"),
            AIFinding(title="low", impact="low", description="d"),
        ]
        result = prioritize_findings(findings)
        assert result[0].title == "low"
        assert result[1].title == "unknown"


# ---------------------------------------------------------------------------
# _findings_to_missions
# ---------------------------------------------------------------------------

class TestFindingsToMissions:
    def test_converts_findings_to_mission_entries(self):
        findings = [
            AIFinding(title="Fix bug A", location="src/a.py:10", description="d"),
            AIFinding(title="Add feature B", description="d"),
        ]
        missions = _findings_to_missions(findings, "myapp")
        assert len(missions) == 2
        assert missions[0] == "- [project:myapp] Fix bug A (src/a.py:10)"
        assert missions[1] == "- [project:myapp] Add feature B"

    def test_omits_location_when_empty(self):
        findings = [AIFinding(title="Simple fix", description="d")]
        missions = _findings_to_missions(findings, "myapp")
        assert missions[0] == "- [project:myapp] Simple fix"


# ---------------------------------------------------------------------------
# _strip_structured_output
# ---------------------------------------------------------------------------

class TestStripStructuredOutput:
    def test_removes_idea_blocks(self):
        text = (
            "Report here\n"
            "---IDEA---\n"
            "TITLE: Something\n"
            "DESCRIPTION: Details\n"
            "---IDEA---\n"
            "TITLE: Another\n"
            "DESCRIPTION: More details\n"
        )
        result = _strip_structured_output(text)
        assert "---IDEA---" not in result
        assert "Report here" in result

    def test_removes_legacy_mission_lines(self):
        text = "Report\nMISSION: Fix something\nMore report"
        result = _strip_structured_output(text)
        assert "MISSION:" not in result
        assert "Report" in result
        assert "More report" in result

    def test_handles_mixed_format(self):
        text = (
            "Report\n"
            "MISSION: Legacy line\n"
            "---IDEA---\n"
            "TITLE: New format\n"
            "DESCRIPTION: Details\n"
        )
        result = _strip_structured_output(text)
        assert "MISSION:" not in result
        assert "---IDEA---" not in result
        assert "Report" in result

    def test_backward_compat_alias(self):
        """_strip_mission_lines should be the same function."""
        assert _strip_mission_lines is _strip_structured_output


# ---------------------------------------------------------------------------
# _build_project_health_block
# ---------------------------------------------------------------------------

class TestBuildProjectHealthBlock:
    @patch("app.mission_summary.get_failure_context", return_value="")
    @patch("app.mission_metrics.get_project_success_rates", return_value={"myapp": 0.85})
    def test_includes_success_rate(self, mock_rates, mock_fail, tmp_path):
        result = _build_project_health_block(str(tmp_path), "myapp")
        assert "85%" in result
        assert "Success rate" in result

    @patch("app.mission_summary.get_failure_context", return_value="fatal: bad ref\nError: build failed")
    @patch("app.mission_metrics.get_project_success_rates", return_value={"myapp": 0.5})
    def test_includes_failure_context(self, mock_rates, mock_fail, tmp_path):
        result = _build_project_health_block(str(tmp_path), "myapp")
        assert "fatal: bad ref" in result
        assert "Recent failure patterns" in result

    @patch("app.mission_summary.get_failure_context", return_value="")
    @patch("app.mission_metrics.get_project_success_rates", return_value={"myapp": 0.5})
    def test_neutral_rate_still_shown(self, mock_rates, mock_fail, tmp_path):
        """0.5 (neutral/no data) is still rendered — the explorer should know."""
        result = _build_project_health_block(str(tmp_path), "myapp")
        assert "50%" in result

    @patch("app.mission_summary.get_failure_context", side_effect=Exception("broken"))
    @patch("app.mission_metrics.get_project_success_rates", side_effect=Exception("broken"))
    def test_resilient_to_errors(self, mock_rates, mock_fail, tmp_path):
        """Errors in metrics/summary must not crash the runner."""
        result = _build_project_health_block(str(tmp_path), "myapp")
        assert result == ""

    @patch("app.mission_summary.get_failure_context", return_value="")
    @patch("app.mission_metrics.get_project_success_rates", return_value={})
    def test_empty_when_no_data(self, mock_rates, mock_fail, tmp_path):
        result = _build_project_health_block(str(tmp_path), "myapp")
        assert result == ""

    @patch("app.mission_summary.get_failure_context", return_value="stack trace here")
    @patch("app.mission_metrics.get_project_success_rates", return_value={"proj": 0.3})
    def test_both_sections_combined(self, mock_rates, mock_fail, tmp_path):
        result = _build_project_health_block(str(tmp_path), "proj")
        assert "Project Health" in result
        assert "30%" in result
        assert "stack trace here" in result


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
    def test_uses_mission_model_key(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        """AI exploration is mission-level reasoning (like /plan) and must use
        the configured mission model, not silently fall back to 'chat'."""
        run_exploration(str(tmp_path), "myapp", str(tmp_path), notify_fn=MagicMock())
        assert mock_claude.call_args.kwargs["model_key"] == "mission"

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

    @patch("app.skill_memory.build_memory_block_for_skill", return_value="<memory>learnings</memory>")
    @patch("app.ai_runner._build_project_health_block", return_value="## Project Health\n- rate: 85%\n")
    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_prompt_substitutions(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        mock_health, mock_memory, tmp_path
    ):
        """Prompt should receive PROJECT_NAME, GIT_ACTIVITY, memory, and health."""
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
        assert kwargs["FOCUS_CONTEXT"] == ""
        assert kwargs["PROJECT_MEMORY"] == "<memory>learnings</memory>"
        assert "85%" in kwargs["PROJECT_HEALTH"]

    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_focus_context_injected_into_prompt(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        """When focus_context is provided, FOCUS_CONTEXT should contain the block."""
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
            focus_context="explore the notification pipeline",
        )
        kwargs = mock_prompt.call_args[1]
        assert "Exploration Focus" in kwargs["FOCUS_CONTEXT"]
        assert "explore the notification pipeline" in kwargs["FOCUS_CONTEXT"]

    @patch("app.cli_provider.run_command_streaming", return_value="Found 3 issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_focus_context_in_notify_message(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        tmp_path
    ):
        """Start notification should include focus hint."""
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
            focus_context="error handling",
        )
        start_msg = notify.call_args_list[0][0][0]
        assert "focus: error handling" in start_msg

    @patch("app.skill_memory.build_memory_block_for_skill", side_effect=Exception("boom"))
    @patch("app.cli_provider.run_command_streaming", return_value="Found issues")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_memory_failure_does_not_crash(
        self, mock_prompt, mock_git, mock_struct, mock_missions, mock_claude,
        mock_memory, tmp_path
    ):
        """Memory injection failure must not crash the exploration."""
        notify = MagicMock()
        success, _ = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        # Memory should be empty string on failure
        kwargs = mock_prompt.call_args[1]
        assert kwargs["PROJECT_MEMORY"] == ""

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
    """Tests for legacy MISSION: line extraction (backward compat)."""

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
    @patch("app.utils.insert_pending_mission")
    def test_inserts_each_mission(self, mock_insert):
        missions = [
            "- [project:myapp] Fix bug A",
            "- [project:myapp] Fix bug B",
        ]
        _queue_missions(None, missions)
        assert mock_insert.call_count == 2
        mock_insert.assert_any_call("Fix bug A", "myapp", urgent=False)
        mock_insert.assert_any_call("Fix bug B", "myapp", urgent=False)

    @patch("app.utils.insert_pending_mission")
    def test_no_missions_no_calls(self, mock_insert):
        _queue_missions(Path("/tmp/missions.md"), [])
        mock_insert.assert_not_called()

    @patch("app.utils.insert_pending_mission")
    def test_high_impact_gets_urgent(self, mock_insert):
        missions_path = Path("/tmp/missions.md")
        findings = [
            AIFinding(title="High impact", impact="high", description="d"),
            AIFinding(title="Low impact", impact="low", description="d"),
        ]
        missions = [
            "- [project:myapp] High impact",
            "- [project:myapp] Low impact",
        ]
        _queue_missions(missions_path, missions, findings)
        calls = mock_insert.call_args_list
        assert calls[0][1]["urgent"] is True
        assert calls[1][1]["urgent"] is False

    @patch("app.utils.insert_pending_mission")
    def test_no_findings_all_non_urgent(self, mock_insert):
        """Legacy path without findings — all non-urgent."""
        missions = ["- [project:myapp] Fix something"]
        _queue_missions(None, missions, findings=None)
        mock_insert.assert_called_once_with("Fix something", "myapp", urgent=False)


# ---------------------------------------------------------------------------
# run_exploration with missions (legacy MISSION: format)
# ---------------------------------------------------------------------------

class TestRunExplorationWithMissions:
    @patch("app.utils.insert_pending_mission")
    @patch("app.cli_provider.run_command_streaming",
           return_value="Found issues\nMISSION: Fix bug A\nMISSION: Fix bug B")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_queues_missions_from_output(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, mock_insert, tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        assert "2 missions queued" in summary
        assert mock_insert.call_count == 2

    @patch("app.utils.insert_pending_mission")
    @patch("app.cli_provider.run_command_streaming",
           return_value="Found issues\nMISSION: Fix bug A\nMISSION: Fix bug B")
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_telegram_shows_mission_count(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, mock_insert, tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        result_msg = notify.call_args_list[1][0][0]
        assert "2 mission(s) queued" in result_msg
        assert "MISSION:" not in result_msg

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
# run_exploration with structured ---IDEA--- blocks
# ---------------------------------------------------------------------------

_STRUCTURED_OUTPUT = (
    "Here's what I found:\n\n"
    "---IDEA---\n"
    "TITLE: Fix retry logic in fetch_data\n"
    "IMPACT: high\n"
    "EFFORT: quick_win\n"
    "CATEGORY: quality\n"
    "LOCATION: src/client.py:42\n"
    "DESCRIPTION: Retry wrapper swallows errors silently.\n"
    "---IDEA---\n"
    "TITLE: Add input validation\n"
    "IMPACT: low\n"
    "EFFORT: medium\n"
    "CATEGORY: security\n"
    "LOCATION: src/auth.py:115\n"
    "DESCRIPTION: Email not validated before DB query.\n"
)


class TestRunExplorationStructured:
    @patch("app.utils.insert_pending_mission")
    @patch("app.cli_provider.run_command_streaming",
           return_value=_STRUCTURED_OUTPUT)
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_structured_output_queues_missions(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, mock_insert, tmp_path
    ):
        notify = MagicMock()
        success, summary = run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        assert success is True
        assert "2 missions queued" in summary
        assert mock_insert.call_count == 2

    @patch("app.utils.insert_pending_mission")
    @patch("app.cli_provider.run_command_streaming",
           return_value=_STRUCTURED_OUTPUT)
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_high_impact_queued_urgent(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, mock_insert, tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        calls = mock_insert.call_args_list
        # High impact finding queued first (sorted), urgent=True
        assert calls[0][1]["urgent"] is True
        assert "Fix retry logic" in calls[0][0][0]
        # Low impact finding queued second, urgent=False
        assert calls[1][1]["urgent"] is False
        assert "Add input validation" in calls[1][0][0]

    @patch("app.utils.insert_pending_mission")
    @patch("app.cli_provider.run_command_streaming",
           return_value=_STRUCTURED_OUTPUT)
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_mission_entries_include_location(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, mock_insert, tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        first_mission = mock_insert.call_args_list[0][0][0]
        assert "(src/client.py:42)" in first_mission

    @patch("app.utils.insert_pending_mission")
    @patch("app.cli_provider.run_command_streaming",
           return_value=_STRUCTURED_OUTPUT)
    @patch("app.ai_runner.get_missions_context", return_value="No active missions.")
    @patch("app.ai_runner.gather_project_structure", return_value="Directories: src/")
    @patch("app.ai_runner.gather_git_activity", return_value="Recent commits: abc")
    @patch("app.ai_runner.load_skill_prompt", return_value="Explore myapp")
    def test_telegram_strips_idea_blocks(
        self, mock_prompt, mock_git, mock_struct, mock_missions,
        mock_claude, mock_insert, tmp_path
    ):
        notify = MagicMock()
        run_exploration(
            str(tmp_path), "myapp", str(tmp_path),
            notify_fn=notify,
        )
        result_msg = notify.call_args_list[1][0][0]
        assert "---IDEA---" not in result_msg
        assert "2 mission(s) queued" in result_msg


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

    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_passes_focus_context(self, mock_run):
        main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
            "--focus-context", "explore the notification pipeline",
        ])
        kwargs = mock_run.call_args[1]
        assert kwargs["focus_context"] == "explore the notification pipeline"

    @patch("app.ai_runner.run_exploration", return_value=(True, "Done"))
    def test_main_default_focus_context_empty(self, mock_run):
        main([
            "--project-path", "/tmp/myapp",
            "--project-name", "myapp",
            "--instance-dir", "/tmp/instance",
        ])
        kwargs = mock_run.call_args[1]
        assert kwargs["focus_context"] == ""
