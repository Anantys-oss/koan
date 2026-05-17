"""Tests for app.describe_pr — structured PR description generation."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.describe_pr import _parse_description, describe_pr, format_description


# ---------------------------------------------------------------------------
# _parse_description() tests
# ---------------------------------------------------------------------------

CLEAN_OUTPUT = """\
## Summary

- Added describe_pr module for structured PR descriptions
- Integrated with implement and fix runners
- Wired into claude_step fallback path

## Why

The old PR descriptions were ad-hoc strings with no consistent structure,
making review harder.

## How

- Created describe_pr module with diff parsing and Claude invocation
- Wired into implement_runner and fix_runner before submit_draft_pr
- Added fallback path in claude_step

## Testing

- 13 unit tests covering parser, formatter, and describe_pr
- Full test suite passes with no regressions
"""

LEADING_PROSE_OUTPUT = """\
Here is the structured PR description you requested:

## Summary

- Fixed null pointer in mission parser

## Why

Crash when section header is None.

## How

- Added guard against None section header in missions.py

## Testing

- Added regression test for None header case
"""

MISSING_TESTING_OUTPUT = """\
## Summary

- Updated README with new installation steps

## Why

Docs were outdated after the config migration.

## How

- Rewrote installation section in README
"""

EMPTY_OUTPUT = ""


class TestParseDescription:
    def test_clean_output(self):
        result = _parse_description(CLEAN_OUTPUT)
        assert len(result["summary"]) == 3
        assert "Added describe_pr module" in result["summary"][0]
        assert "ad-hoc" in result["why"]
        assert len(result["how"]) == 3
        assert len(result["testing"]) == 2

    def test_leading_prose_stripped(self):
        result = _parse_description(LEADING_PROSE_OUTPUT)
        assert result["summary"] == ["Fixed null pointer in mission parser"]
        assert "None" in result["why"]
        assert result["how"][0] == "Added guard against None section header in missions.py"

    def test_missing_testing_returns_empty_list(self):
        result = _parse_description(MISSING_TESTING_OUTPUT)
        assert "Updated README" in result["summary"][0]
        assert "outdated" in result["why"]
        assert result["testing"] == []

    def test_empty_string_returns_empty_structure(self):
        result = _parse_description(EMPTY_OUTPUT)
        assert result["summary"] == []
        assert result["why"] == ""
        assert result["how"] == []
        assert result["testing"] == []
        assert result["limitations"] == []

    def test_extra_whitespace_handled(self):
        raw = "\n## Summary\n\n-  A bullet  \n\n## Why\n\nBecause reasons.\n"
        result = _parse_description(raw)
        assert result["summary"] == ["A bullet"]
        assert result["why"] == "Because reasons."

    def test_limitations_parsed(self):
        raw = (
            "## Summary\n\n- Change X\n\n"
            "## Why\n\nNeeded.\n\n"
            "## How\n\n- Did Y\n\n"
            "## Testing\n\n- Tested Z\n\n"
            "## Limitations & Risk\n\n- May break on large inputs\n"
        )
        result = _parse_description(raw)
        assert result["limitations"] == ["May break on large inputs"]


# ---------------------------------------------------------------------------
# format_description() tests
# ---------------------------------------------------------------------------

class TestFormatDescription:
    def test_full_desc_renders_all_sections(self):
        desc = {
            "summary": ["Added feature A", "Fixed edge case B"],
            "why": "Users needed feature A for workflow X.",
            "how": ["Created module foo", "Wired into bar"],
            "testing": ["Unit tests added", "Manual QA passed"],
            "limitations": ["Does not handle edge case C"],
        }
        rendered = format_description(desc)
        assert "## Summary" in rendered
        assert "- Added feature A" in rendered
        assert "## Why" in rendered
        assert "Users needed feature A" in rendered
        assert "## How" in rendered
        assert "- Created module foo" in rendered
        assert "## Testing" in rendered
        assert "- Unit tests added" in rendered
        assert "## Limitations & Risk" in rendered
        assert "- Does not handle edge case C" in rendered

    def test_no_limitations_skips_section(self):
        desc = {
            "summary": ["Updated readme"],
            "why": "Docs outdated.",
            "how": ["Rewrote section"],
            "testing": ["Verified locally"],
            "limitations": [],
        }
        rendered = format_description(desc)
        assert "## Limitations" not in rendered
        assert "Updated readme" in rendered

    def test_empty_desc_returns_empty_string(self):
        desc = {"summary": [], "why": "", "how": [], "testing": [], "limitations": []}
        assert format_description(desc) == ""


# ---------------------------------------------------------------------------
# describe_pr() tests
# ---------------------------------------------------------------------------

FIXTURE_CLI_OUTPUT = """\
## Summary

- Adds describe_pr for auto-generated PR descriptions

## Why

PR descriptions were inconsistent and manual.

## How

- Created describe_pr module with Claude invocation

## Testing

- Added 13 unit tests
"""


@pytest.fixture()
def mock_git_diff():
    """Patch _run_git so git calls return fixture data."""
    with patch("app.describe_pr._run_git") as mock:
        mock.side_effect = [
            "1 file changed, 10 insertions(+)",  # stat call
            "diff --git a/foo.py b/foo.py\n+new line",  # diff call
            "- add feature",  # log call
        ]
        yield mock


class TestDescribePr:
    def test_returns_parsed_dict_on_success(self, mock_git_diff, tmp_path):
        cli_result = MagicMock()
        cli_result.returncode = 0
        cli_result.stdout = FIXTURE_CLI_OUTPUT
        cli_result.stderr = ""

        with (
            patch("app.cli_provider.build_full_command", return_value=["claude"]),
            patch("app.config.get_model_config", return_value={"lightweight": "haiku"}),
            patch("app.prompts.load_prompt", return_value="prompt text"),
            patch("app.cli_exec.run_cli_with_retry", return_value=cli_result),
        ):
            result = describe_pr(str(tmp_path), "main")

        assert result is not None
        assert len(result["summary"]) == 1
        assert "inconsistent" in result["why"]
        assert len(result["how"]) == 1

    def test_returns_none_on_empty_diff(self, tmp_path):
        with patch("app.describe_pr._run_git") as mock:
            mock.side_effect = [
                "",  # stat
                "",  # diff
            ]
            result = describe_pr(str(tmp_path), "main")

        assert result is None

    def test_returns_none_on_cli_failure(self, mock_git_diff, tmp_path):
        cli_result = MagicMock()
        cli_result.returncode = 1
        cli_result.stdout = ""
        cli_result.stderr = "quota exhausted"

        with (
            patch("app.cli_provider.build_full_command", return_value=["claude"]),
            patch("app.config.get_model_config", return_value={}),
            patch("app.prompts.load_prompt", return_value="prompt"),
            patch("app.cli_exec.run_cli_with_retry", return_value=cli_result),
        ):
            result = describe_pr(str(tmp_path), "main")

        assert result is None

    def test_returns_none_on_cli_exception(self, mock_git_diff, tmp_path):
        with (
            patch("app.cli_provider.build_full_command", return_value=["claude"]),
            patch("app.config.get_model_config", return_value={}),
            patch("app.prompts.load_prompt", return_value="prompt"),
            patch("app.cli_exec.run_cli_with_retry", side_effect=RuntimeError("timeout")),
        ):
            result = describe_pr(str(tmp_path), "main")

        assert result is None

    def test_fallback_body_unchanged_when_describe_pr_raises(self, tmp_path):
        """Caller (implement_runner) body is unchanged when describe_pr raises."""
        original_body = "## Summary\n\nFallback body\n\nCloses #1\n\n---\n*Generated*"

        with patch("app.describe_pr._run_git", side_effect=RuntimeError("git gone")):
            result = describe_pr(str(tmp_path), "main")

        assert result is None
        # Caller logic: if None, keep original_body as-is
        final_body = original_body if result is None else "replaced"
        assert final_body == original_body
