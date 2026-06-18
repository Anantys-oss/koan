"""Tests for fix_diagnose.py — the pre-fix diagnostic step."""

from pathlib import Path
from unittest.mock import patch, MagicMock

from skills.core.fix.fix_diagnose import (
    run_diagnostic,
    _parse_diagnostic,
    format_diagnostic_context,
)

_DIAG_MODULE = "skills.core.fix.fix_diagnose"


class TestParseDiagnostic:
    def test_high_confidence(self):
        raw = (
            "CONFIDENCE: HIGH\n\n"
            "HYPOTHESIS: The bug is in foo() on line 42\n\n"
            "CODE_PATHS:\n"
            "- src/foo.py:42 — handles bar\n\n"
            "ANALYSIS:\nThe function fails because..."
        )
        result = _parse_diagnostic(raw)
        assert result["confidence"] == "HIGH"
        assert "foo()" in result["hypothesis"]
        assert "src/foo.py:42" in result["code_paths"]
        assert "fails because" in result["analysis"]

    def test_low_confidence(self):
        raw = "CONFIDENCE: LOW\n\nHYPOTHESIS: Unclear\n\nCODE_PATHS:\n\nANALYSIS:\nNot enough info"
        result = _parse_diagnostic(raw)
        assert result["confidence"] == "LOW"

    def test_missing_confidence_defaults_low(self):
        raw = "Some unstructured output without markers"
        result = _parse_diagnostic(raw)
        assert result["confidence"] == "LOW"
        assert result["raw"] == raw

    def test_medium_confidence(self):
        raw = "CONFIDENCE: MEDIUM\n\nHYPOTHESIS: Likely in auth module\n\nCODE_PATHS:\n- auth.py:10\n\nANALYSIS:\nProbably here"
        result = _parse_diagnostic(raw)
        assert result["confidence"] == "MEDIUM"

    def test_empty_output(self):
        result = _parse_diagnostic("")
        assert result["confidence"] == "LOW"
        assert result["raw"] == ""


class TestFormatDiagnosticContext:
    def test_formats_high_confidence(self):
        diag = {
            "confidence": "HIGH",
            "hypothesis": "Bug in foo()",
            "code_paths": "- src/foo.py:42",
            "analysis": "Root cause is X",
        }
        text = format_diagnostic_context(diag)
        assert "Preliminary Diagnostic" in text
        assert "HIGH" in text
        assert "Bug in foo()" in text
        assert "src/foo.py:42" in text
        assert "hypothesis" in text.lower()

    def test_empty_hypothesis_returns_empty(self):
        diag = {"confidence": "LOW", "hypothesis": "", "code_paths": "", "analysis": ""}
        assert format_diagnostic_context(diag) == ""

    def test_missing_optional_sections(self):
        diag = {"confidence": "MEDIUM", "hypothesis": "Something", "code_paths": "", "analysis": ""}
        text = format_diagnostic_context(diag)
        assert "Something" in text
        assert "code paths" not in text.lower()


class TestRunDiagnostic:
    @patch("app.cli_provider.run_command_streaming", return_value=(
        "CONFIDENCE: HIGH\n\nHYPOTHESIS: Bug in X\n\n"
        "CODE_PATHS:\n- a.py:1\n\nANALYSIS:\nRoot cause is Y"
    ))
    @patch(f"{_DIAG_MODULE}.load_prompt_or_skill", return_value="prompt text")
    def test_returns_parsed_diagnostic(self, mock_prompt, mock_run):
        result = run_diagnostic(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Bug",
            issue_body="Body",
        )
        assert result["confidence"] == "HIGH"
        assert "Bug in X" in result["hypothesis"]
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["model_key"] == "chat"

    @patch("app.cli_provider.run_command_streaming", return_value="")
    @patch(f"{_DIAG_MODULE}.load_prompt_or_skill", return_value="prompt")
    def test_empty_output_returns_low_confidence(self, mock_prompt, mock_run):
        result = run_diagnostic(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Bug",
            issue_body="Body",
        )
        assert result["confidence"] == "LOW"

    @patch("app.cli_provider.run_command_streaming", side_effect=Exception("timeout"))
    @patch(f"{_DIAG_MODULE}.load_prompt_or_skill", return_value="prompt")
    def test_exception_returns_low_confidence(self, mock_prompt, mock_run):
        result = run_diagnostic(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Bug",
            issue_body="Body",
        )
        assert result["confidence"] == "LOW"
        assert "timeout" in result.get("error", "")

    @patch("app.cli_provider.run_command_streaming", return_value="CONFIDENCE: HIGH\n\nHYPOTHESIS: X\n\nCODE_PATHS:\n\nANALYSIS:\nY")
    @patch(f"{_DIAG_MODULE}.load_prompt_or_skill", return_value="prompt")
    def test_uses_read_only_tools(self, mock_prompt, mock_run):
        run_diagnostic(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Bug",
            issue_body="Body",
        )
        tools = mock_run.call_args.kwargs["allowed_tools"]
        assert "Edit" not in tools
        assert "Write" not in tools
        assert "Read" in tools
        assert "Grep" in tools

    @patch("app.cli_provider.run_command_streaming", return_value="CONFIDENCE: HIGH\n\nHYPOTHESIS: X\n\nCODE_PATHS:\n\nANALYSIS:\nY")
    @patch(f"{_DIAG_MODULE}.load_prompt_or_skill", return_value="prompt")
    def test_skill_dir_passed_to_prompt_loader(self, mock_prompt, mock_run):
        skill_dir = Path("/skills/core/fix")
        run_diagnostic(
            project_path="/path",
            issue_url="https://github.com/o/r/issues/42",
            issue_title="Bug",
            issue_body="Body",
            skill_dir=skill_dir,
        )
        assert mock_prompt.call_args[0][0] == skill_dir
