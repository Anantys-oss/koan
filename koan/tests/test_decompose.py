"""Tests for app.decompose — LLM-driven mission decomposition."""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.decompose import DecomposeError, _parse_response, decompose_mission


# --- _parse_response -------------------------------------------------------

def test_parse_atomic_returns_none():
    assert _parse_response('{"type": "atomic", "subtasks": []}') is None


def test_parse_composite_returns_list():
    out = '{"type": "composite", "subtasks": ["Do A", "Do B"]}'
    assert _parse_response(out) == ["Do A", "Do B"]


def test_parse_composite_case_insensitive_type():
    out = '{"type": "COMPOSITE", "subtasks": ["Do A"]}'
    assert _parse_response(out) == ["Do A"]


def test_parse_strips_code_fence():
    out = '```json\n{"type": "composite", "subtasks": ["Do A"]}\n```'
    assert _parse_response(out) == ["Do A"]


def test_parse_narrows_surrounding_prose():
    out = 'Here is my answer: {"type": "composite", "subtasks": ["Do A"]} done'
    assert _parse_response(out) == ["Do A"]


def test_parse_empty_output_raises():
    with pytest.raises(DecomposeError):
        _parse_response("")


def test_parse_malformed_json_raises():
    with pytest.raises(DecomposeError):
        _parse_response("not json at all {")


def test_parse_non_dict_raises():
    with pytest.raises(DecomposeError):
        _parse_response('["a", "b"]')


def test_parse_composite_non_list_subtasks_raises():
    # Composite verdict with a malformed subtasks field is a classifier
    # failure, not a real atomic verdict.
    with pytest.raises(DecomposeError):
        _parse_response('{"type": "composite", "subtasks": "oops"}')


def test_parse_composite_all_blank_subtasks_raises():
    # Degenerate output must surface, not silently downgrade to atomic.
    with pytest.raises(DecomposeError):
        _parse_response('{"type": "composite", "subtasks": ["", "  "]}')


def test_parse_filters_blank_subtasks():
    out = '{"type": "composite", "subtasks": ["Do A", "", "Do B"]}'
    assert _parse_response(out) == ["Do A", "Do B"]


def test_parse_truncates_to_six():
    tasks = [f"Task {i}" for i in range(10)]
    import json
    out = json.dumps({"type": "composite", "subtasks": tasks})
    result = _parse_response(out)
    assert len(result) == 6
    assert result == tasks[:6]


def test_parse_missing_type_returns_none():
    # No composite verdict → treat as atomic.
    assert _parse_response('{"subtasks": ["Do A"]}') is None


# --- decompose_mission -----------------------------------------------------

def _mock_cli(returncode=0, stdout="", stderr=""):
    res = MagicMock()
    res.returncode = returncode
    res.stdout = stdout
    res.stderr = stderr
    return res


@patch("app.cli_exec.run_cli_with_retry")
@patch("app.cli_provider.build_full_command", return_value=["claude"])
@patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
@patch("app.prompts.load_prompt", return_value="prompt")
def test_decompose_mission_composite(_lp, _mc, _bc, mock_cli):
    mock_cli.return_value = _mock_cli(
        stdout='{"type": "composite", "subtasks": ["A", "B"]}')
    assert decompose_mission("m", "/tmp") == ["A", "B"]


@patch("app.cli_exec.run_cli_with_retry")
@patch("app.cli_provider.build_full_command", return_value=["claude"])
@patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
@patch("app.prompts.load_prompt", return_value="prompt")
def test_decompose_mission_atomic(_lp, _mc, _bc, mock_cli):
    mock_cli.return_value = _mock_cli(stdout='{"type": "atomic", "subtasks": []}')
    assert decompose_mission("m", "/tmp") is None


@patch("app.cli_exec.run_cli_with_retry")
@patch("app.cli_provider.build_full_command", return_value=["claude"])
@patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
@patch("app.prompts.load_prompt", return_value="prompt")
def test_decompose_mission_nonzero_exit_raises(_lp, _mc, _bc, mock_cli):
    mock_cli.return_value = _mock_cli(returncode=1, stderr="boom")
    with pytest.raises(DecomposeError):
        decompose_mission("m", "/tmp")


@patch("app.cli_exec.run_cli_with_retry", side_effect=RuntimeError("network"))
@patch("app.cli_provider.build_full_command", return_value=["claude"])
@patch("app.config.get_model_config", return_value={"lightweight": "haiku", "fallback": "sonnet"})
@patch("app.prompts.load_prompt", return_value="prompt")
def test_decompose_mission_exception_raises(_lp, _mc, _bc, _cli):
    with pytest.raises(DecomposeError):
        decompose_mission("m", "/tmp")
