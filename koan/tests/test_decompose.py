"""Tests for app.decompose — mission decomposition classifier."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.decompose import DecomposeError, _parse_response, decompose_mission


# === _parse_response unit tests ===


class TestParseResponse:

    def test_returns_none_for_atomic(self):
        output = json.dumps({"type": "atomic", "subtasks": []})
        assert _parse_response(output) is None

    def test_returns_subtasks_for_composite(self):
        output = json.dumps({
            "type": "composite",
            "subtasks": ["Write tests", "Implement feature", "Update docs"],
        })
        result = _parse_response(output)
        assert result == ["Write tests", "Implement feature", "Update docs"]

    def test_raises_on_malformed_json(self):
        with pytest.raises(DecomposeError):
            _parse_response("not valid json {{")

    def test_raises_on_empty_output(self):
        with pytest.raises(DecomposeError):
            _parse_response("")

    def test_handles_empty_subtasks_composite(self):
        output = json.dumps({"type": "composite", "subtasks": []})
        assert _parse_response(output) is None

    def test_truncates_to_max_six(self):
        output = json.dumps({
            "type": "composite",
            "subtasks": [f"Task {i}" for i in range(10)],
        })
        result = _parse_response(output)
        assert result is not None
        assert len(result) == 6

    def test_strips_markdown_fences(self):
        output = "```json\n" + json.dumps({"type": "atomic", "subtasks": []}) + "\n```"
        assert _parse_response(output) is None

    def test_strips_fences_composite(self):
        payload = {"type": "composite", "subtasks": ["A", "B"]}
        output = "```json\n" + json.dumps(payload) + "\n```"
        result = _parse_response(output)
        assert result == ["A", "B"]

    def test_filters_blank_subtasks(self):
        output = json.dumps({"type": "composite", "subtasks": ["  ", "Real task", ""]})
        result = _parse_response(output)
        assert result == ["Real task"]

    def test_raises_on_non_dict(self):
        with pytest.raises(DecomposeError):
            _parse_response(json.dumps(["not", "a", "dict"]))


# === decompose_mission integration (subprocess mocked) ===


class TestDecomposeMission:

    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command", return_value=["mock-cmd"])
    @patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
    @patch("app.prompts.load_prompt", return_value="prompt text")
    def test_returns_none_for_atomic(self, mock_prompt, mock_models, mock_cmd, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"type": "atomic", "subtasks": []}),
            stderr="",
        )
        result = decompose_mission("Fix a typo", "/tmp/project")
        assert result is None

    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command", return_value=["mock-cmd"])
    @patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
    @patch("app.prompts.load_prompt", return_value="prompt text")
    def test_returns_subtasks_for_composite(self, mock_prompt, mock_models, mock_cmd, mock_run):
        subtasks = ["Refactor auth module", "Add new login endpoint", "Write tests"]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"type": "composite", "subtasks": subtasks}),
            stderr="",
        )
        result = decompose_mission("Refactor auth AND add login AND test", "/tmp/project")
        assert result == subtasks

    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command", return_value=["mock-cmd"])
    @patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
    @patch("app.prompts.load_prompt", return_value="prompt text")
    def test_raises_decompose_error_on_malformed_json(self, mock_prompt, mock_models, mock_cmd, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="this is not json at all",
            stderr="",
        )
        with pytest.raises(DecomposeError):
            decompose_mission("Some mission", "/tmp/project")

    @patch("app.cli_exec.run_cli_with_retry")
    @patch("app.cli_provider.build_full_command", return_value=["mock-cmd"])
    @patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
    @patch("app.prompts.load_prompt", return_value="prompt text")
    def test_raises_decompose_error_on_nonzero_returncode(self, mock_prompt, mock_models, mock_cmd, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        with pytest.raises(DecomposeError):
            decompose_mission("Some mission", "/tmp/project")

    @patch("app.cli_exec.run_cli_with_retry", side_effect=RuntimeError("CLI crashed"))
    @patch("app.cli_provider.build_full_command", return_value=["mock-cmd"])
    @patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
    @patch("app.prompts.load_prompt", return_value="prompt text")
    def test_raises_decompose_error_on_exception(self, mock_prompt, mock_models, mock_cmd, mock_run):
        with pytest.raises(DecomposeError):
            decompose_mission("Some mission", "/tmp/project")
