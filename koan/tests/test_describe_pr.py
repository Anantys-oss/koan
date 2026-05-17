"""Tests for app.describe_pr — structured PR description generation."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.describe_pr import _parse_description, describe_pr, format_description


# ---------------------------------------------------------------------------
# _parse_description() tests
# ---------------------------------------------------------------------------

CLEAN_OUTPUT = """\
## Type

enhancement

## Summary

- Added describe_pr module for structured PR descriptions
- Integrated with implement and fix runners
- Wired into claude_step fallback path

## Walkthrough

- `koan/app/describe_pr.py` — new module with describe_pr and helpers
- `koan/skills/core/implement/implement_runner.py` — call describe_pr before submit
"""

LEADING_PROSE_OUTPUT = """\
Here is the structured PR description you requested:

## Type

bug fix

## Summary

- Fixed null pointer in mission parser

## Walkthrough

- `koan/app/missions.py` — guard against None section header
"""

MISSING_WALKTHROUGH_OUTPUT = """\
## Type

docs

## Summary

- Updated README with new installation steps
"""

EMPTY_OUTPUT = ""


class TestParseDescription:
    def test_clean_output(self):
        result = _parse_description(CLEAN_OUTPUT)
        assert result["type"] == "enhancement"
        assert len(result["summary"]) == 3
        assert "Added describe_pr module" in result["summary"][0]
        assert len(result["walkthrough"]) == 2
        assert result["walkthrough"][0]["file"] == "koan/app/describe_pr.py"
        assert "new module" in result["walkthrough"][0]["change"]

    def test_leading_prose_stripped(self):
        result = _parse_description(LEADING_PROSE_OUTPUT)
        assert result["type"] == "bug fix"
        assert result["summary"] == ["Fixed null pointer in mission parser"]
        assert result["walkthrough"][0]["file"] == "koan/app/missions.py"

    def test_missing_walkthrough_returns_empty_list(self):
        result = _parse_description(MISSING_WALKTHROUGH_OUTPUT)
        assert result["type"] == "docs"
        assert "Updated README" in result["summary"][0]
        assert result["walkthrough"] == []

    def test_empty_string_returns_empty_structure(self):
        result = _parse_description(EMPTY_OUTPUT)
        assert result["type"] == ""
        assert result["summary"] == []
        assert result["walkthrough"] == []

    def test_extra_whitespace_handled(self):
        raw = "\n## Type\n\n  enhancement  \n\n## Summary\n\n-  A bullet  \n"
        result = _parse_description(raw)
        assert result["type"] == "enhancement"
        assert result["summary"] == ["A bullet"]


# ---------------------------------------------------------------------------
# format_description() tests
# ---------------------------------------------------------------------------

class TestFormatDescription:
    def test_full_desc_renders_all_sections(self):
        desc = {
            "type": "enhancement",
            "summary": ["Added feature A", "Fixed edge case B"],
            "walkthrough": [
                {"file": "koan/app/foo.py", "change": "added helper"},
            ],
        }
        rendered = format_description(desc)
        assert "**Type:** enhancement" in rendered
        assert "## Summary" in rendered
        assert "- Added feature A" in rendered
        assert "## Changes" in rendered
        assert "`koan/app/foo.py` — added helper" in rendered

    def test_empty_walkthrough_skips_changes_section(self):
        desc = {"type": "docs", "summary": ["Updated readme"], "walkthrough": []}
        rendered = format_description(desc)
        assert "## Changes" not in rendered
        assert "Updated readme" in rendered

    def test_empty_desc_returns_empty_string(self):
        desc = {"type": "", "summary": [], "walkthrough": []}
        assert format_description(desc) == ""


# ---------------------------------------------------------------------------
# describe_pr() tests
# ---------------------------------------------------------------------------

FIXTURE_CLI_OUTPUT = """\
## Type

enhancement

## Summary

- Adds describe_pr for auto-generated PR descriptions

## Walkthrough

- `koan/app/describe_pr.py` — new module
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
        assert result["type"] == "enhancement"
        assert len(result["summary"]) == 1
        assert result["walkthrough"][0]["file"] == "koan/app/describe_pr.py"

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
