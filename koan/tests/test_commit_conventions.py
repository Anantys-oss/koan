"""Tests for commit_conventions.py — project commit convention detection and parsing."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.commit_conventions import (
    get_project_commit_guidance,
    parse_commit_subject,
    strip_commit_subject_line,
    _extract_commit_sections_from_claude_md,
    _infer_commit_style_from_history,
)


# ---------------------------------------------------------------------------
# _extract_commit_sections_from_claude_md
# ---------------------------------------------------------------------------

class TestExtractCommitSectionsFromClaudeMd:
    def test_commit_section_found(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\nSome intro.\n\n"
            "## Commit Conventions\n\n"
            "Use `Case PROJECT-XXXXX` prefix for all commits.\n"
            "Always include a Changelog trailer.\n\n"
            "## Other Section\n\nNot about commits.\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert "Case PROJECT-XXXXX" in result
        assert "Changelog trailer" in result
        assert "Not about commits" not in result

    def test_heading_variants(self, tmp_path):
        """Various heading keywords should match."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Repo\n\n"
            "### Git Style and Message Format\n\n"
            "Use conventional commits (feat:, fix:, etc.).\n\n"
            "## Changelog\n\n"
            "Add a changelog entry for every PR.\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert "conventional commits" in result
        assert "changelog entry" in result

    def test_no_commit_section(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n## Setup\n\nRun make install.\n\n"
            "## Testing\n\nRun make test.\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert result == ""

    def test_file_missing(self, tmp_path):
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert result == ""

    def test_empty_file(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("")
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert result == ""

    def test_truncation(self, tmp_path):
        """Long commit sections are truncated."""
        claude_md = tmp_path / "CLAUDE.md"
        long_content = "x" * 5000
        claude_md.write_text(
            f"## Commit Format\n\n{long_content}\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert len(result) <= 4100  # 4000 + "(truncated)" overhead
        assert "(truncated)" in result

    def test_resolves_file_references(self, tmp_path):
        """File references in commit sections should be resolved and included."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Commit Conventions\n\n"
            "Follow `.github/instructions/commit-messages.instructions.md` for details.\n"
        )
        # Create the referenced file
        instructions_dir = tmp_path / ".github" / "instructions"
        instructions_dir.mkdir(parents=True)
        instructions_file = instructions_dir / "commit-messages.instructions.md"
        instructions_file.write_text(
            "# Commit Format\n\nUse `Case PROJECT-XXXXX:` on third line.\n"
            "Add `Changelog:` trailer.\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert "Case PROJECT-XXXXX" in result
        assert "Changelog:" in result
        assert "Referenced:" in result

    def test_missing_referenced_file_ignored(self, tmp_path):
        """Missing referenced files should not cause errors."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## Commit Conventions\n\n"
            "Follow `.github/nonexistent.md` for details.\n"
            "Use imperative mood.\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert "imperative mood" in result
        assert "Referenced:" not in result

    def test_case_insensitive_heading(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "## COMMIT MESSAGE FORMAT\n\nUse prefix.\n"
        )
        result = _extract_commit_sections_from_claude_md(str(tmp_path))
        assert "Use prefix" in result


# ---------------------------------------------------------------------------
# _infer_commit_style_from_history
# ---------------------------------------------------------------------------

class TestInferCommitStyleFromHistory:
    def _mock_git_log(self, lines):
        """Return a mock subprocess result with the given log lines."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = "\n".join(lines) + "\n"
        return result

    def test_conventional_commits_detected(self, tmp_path):
        log_lines = [
            "abc1234 feat: add login page",
            "def5678 fix(auth): handle expired tokens",
            "aaa9012 docs: update README",
            "bbb3456 refactor: clean up utils",
            "ccc7890 feat(api): add retry logic",
            "ddd1234 test: add coverage",
            "eee5678 chore: bump dependencies",
            "fff9012 fix: null pointer check",
            "aab3456 ci: fix pipeline",
            "bcd7890 feat: dark mode",
            "1234567 random commit not matching",
        ]
        with patch("app.commit_conventions.subprocess.run", return_value=self._mock_git_log(log_lines)):
            result = _infer_commit_style_from_history(str(tmp_path), "HEAD")
        assert "conventional commits" in result.lower()
        assert "feat:" in result or "fix" in result

    def test_ticket_references_detected(self, tmp_path):
        log_lines = [
            "abc1234 Case PROJECT-12345 Fix auth module",
            "def5678 Case PROJECT-12346 Update templates",
            "aaa9012 Case PROJECT-12347 Add logging",
            "bbb3456 Case PROJECT-12348 Refactor config",
            "ccc7890 Case PROJECT-12349 Fix CSS",
            "ddd1234 Case PROJECT-12350 Update tests",
            "eee5678 Case PROJECT-12351 Bump version",
            "fff9012 random commit",
            "aab3456 another random",
        ]
        with patch("app.commit_conventions.subprocess.run", return_value=self._mock_git_log(log_lines)):
            result = _infer_commit_style_from_history(str(tmp_path), "HEAD")
        assert "ticket" in result.lower() or "case" in result.lower()

    def test_no_pattern_detected(self, tmp_path):
        log_lines = [
            "abc1234 fixed the thing",
            "def5678 updated stuff",
            "aaa9012 more changes",
            "bbb3456 cleanup",
            "ccc7890 wip",
        ]
        with patch("app.commit_conventions.subprocess.run", return_value=self._mock_git_log(log_lines)):
            result = _infer_commit_style_from_history(str(tmp_path), "HEAD")
        assert result == ""

    def test_git_error_returns_empty(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("app.commit_conventions.subprocess.run", return_value=mock_result):
            result = _infer_commit_style_from_history(str(tmp_path), "HEAD")
        assert result == ""

    def test_git_timeout_returns_empty(self, tmp_path):
        with patch("app.commit_conventions.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            result = _infer_commit_style_from_history(str(tmp_path), "HEAD")
        assert result == ""

    def test_empty_log_returns_empty(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("app.commit_conventions.subprocess.run", return_value=mock_result):
            result = _infer_commit_style_from_history(str(tmp_path), "HEAD")
        assert result == ""


# ---------------------------------------------------------------------------
# get_project_commit_guidance
# ---------------------------------------------------------------------------

class TestGetProjectCommitGuidance:
    def test_prefers_claude_md_over_history(self, tmp_path):
        """CLAUDE.md guidance takes precedence over history inference."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("## Commit Conventions\n\nUse JIRA prefix.\n")
        result = get_project_commit_guidance(str(tmp_path), "HEAD")
        assert "JIRA prefix" in result
        assert "Project Commit Conventions" in result
        # Should NOT contain "inferred" since CLAUDE.md was found
        assert "inferred" not in result

    def test_falls_back_to_history(self, tmp_path):
        """Without CLAUDE.md, falls back to commit history."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "\n".join([
            f"abc{i:04d} feat: change {i}" for i in range(15)
        ]) + "\n"
        with patch("app.commit_conventions.subprocess.run", return_value=mock_result):
            result = get_project_commit_guidance(str(tmp_path), "HEAD")
        assert "inferred" in result.lower()
        assert "conventional commits" in result.lower()

    def test_no_conventions_returns_empty(self, tmp_path):
        """No CLAUDE.md and no clear history pattern returns empty."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234 random\ndef5678 stuff\n"
        with patch("app.commit_conventions.subprocess.run", return_value=mock_result):
            result = get_project_commit_guidance(str(tmp_path), "HEAD")
        assert result == ""


# ---------------------------------------------------------------------------
# parse_commit_subject
# ---------------------------------------------------------------------------

class TestParseCommitSubject:
    def test_found(self):
        output = (
            "I fixed the auth module.\n\n"
            "COMMIT_SUBJECT: Case PROJECT-52496 Fix auth token expiry\n"
        )
        result = parse_commit_subject(output)
        assert result == "Case PROJECT-52496 Fix auth token expiry"

    def test_not_found(self):
        output = "I fixed the auth module.\nDone."
        assert parse_commit_subject(output) is None

    def test_empty_subject(self):
        output = "COMMIT_SUBJECT: \n"
        assert parse_commit_subject(output) is None

    def test_too_long(self):
        long_subject = "x" * 200
        output = f"COMMIT_SUBJECT: {long_subject}\n"
        assert parse_commit_subject(output) is None

    def test_last_wins(self):
        """When multiple COMMIT_SUBJECT lines exist, last one is used."""
        output = (
            "COMMIT_SUBJECT: first attempt\n"
            "Actually wait...\n"
            "COMMIT_SUBJECT: Case PROJECT-123 correct subject\n"
        )
        result = parse_commit_subject(output)
        assert result == "Case PROJECT-123 correct subject"

    def test_whitespace_stripped(self):
        output = "COMMIT_SUBJECT:   fix: handle edge case   \n"
        result = parse_commit_subject(output)
        assert result == "fix: handle edge case"


# ---------------------------------------------------------------------------
# strip_commit_subject_line
# ---------------------------------------------------------------------------

class TestStripCommitSubjectLine:
    def test_removes_marker(self):
        text = (
            "Fixed the login bug.\n\n"
            "COMMIT_SUBJECT: fix(auth): resolve login failure\n\n"
            "Details of what changed."
        )
        result = strip_commit_subject_line(text)
        assert "COMMIT_SUBJECT" not in result
        assert "Fixed the login bug" in result
        assert "Details of what changed" in result

    def test_no_marker_unchanged(self):
        text = "Just a summary.\nNo marker here."
        result = strip_commit_subject_line(text)
        assert "Just a summary" in result

    def test_multiple_markers_all_removed(self):
        text = "COMMIT_SUBJECT: first\nstuff\nCOMMIT_SUBJECT: second\n"
        result = strip_commit_subject_line(text)
        assert "COMMIT_SUBJECT" not in result
