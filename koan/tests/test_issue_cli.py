"""Tests for app.issue_cli — provider-neutral issue tracker CLI."""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.issue_cli import main  # noqa: E402
from app.issue_tracker.types import IssueContent, IssueRef  # noqa: E402


def _make_issue_content(title="Test Issue", body="Issue body", comments=None):
    ref = IssueRef(provider="github", url="https://github.com/o/r/issues/1", key="1")
    return IssueContent(ref=ref, title=title, body=body, comments=comments or [])


class TestIssueCLIFetch:
    def test_fetch_prints_title_and_body(self, capsys):
        content = _make_issue_content()
        with patch("app.issue_cli.fetch_issue", return_value=content):
            result = main(["fetch", "https://github.com/o/r/issues/1"])
        assert result == 0
        out = capsys.readouterr().out
        assert "# #1: Test Issue" in out
        assert "Issue body" in out

    def test_fetch_with_comments(self, capsys):
        content = _make_issue_content(
            comments=[{"author": "alice", "body": "looks good"}]
        )
        with patch("app.issue_cli.fetch_issue", return_value=content):
            result = main(["fetch", "https://github.com/o/r/issues/1"])
        assert result == 0
        out = capsys.readouterr().out
        assert "## Comments" in out
        assert "### alice" in out
        assert "looks good" in out

    def test_fetch_error_returns_1(self, capsys):
        with patch("app.issue_cli.fetch_issue", side_effect=RuntimeError("API down")):
            result = main(["fetch", "https://github.com/o/r/issues/1"])
        assert result == 1
        err = capsys.readouterr().err
        assert "API down" in err


class TestIssueCLIComment:
    def test_comment_success(self, tmp_path):
        body_file = tmp_path / "body.md"
        body_file.write_text("Nice work!")
        with patch("app.issue_cli.add_comment") as mock:
            result = main([
                "comment", "https://github.com/o/r/issues/1",
                "--body-file", str(body_file),
            ])
        assert result == 0
        mock.assert_called_once_with(
            "https://github.com/o/r/issues/1",
            "Nice work!",
            project_name="",
            project_path="",
        )

    def test_comment_missing_body_file(self, capsys):
        with pytest.raises(SystemExit):
            main([
                "comment", "https://github.com/o/r/issues/1",
                "--body-file", "/nonexistent/file.md",
            ])
        err = capsys.readouterr().err
        assert "body file not found" in err

    def test_comment_with_project(self, tmp_path):
        body_file = tmp_path / "body.md"
        body_file.write_text("Comment")
        with patch("app.issue_cli.add_comment") as mock:
            main([
                "comment", "https://github.com/o/r/issues/1",
                "--body-file", str(body_file),
                "--project", "myproj",
                "--project-path", "/path/to/proj",
            ])
        mock.assert_called_once_with(
            "https://github.com/o/r/issues/1",
            "Comment",
            project_name="myproj",
            project_path="/path/to/proj",
        )


class TestIssueCLICreate:
    def test_create_prints_url(self, tmp_path, capsys):
        body_file = tmp_path / "issue.md"
        body_file.write_text("Issue description")
        with patch("app.issue_cli.create_issue", return_value="https://github.com/o/r/issues/42"):
            result = main([
                "create",
                "--project", "myproj",
                "--title", "Fix bug",
                "--body-file", str(body_file),
            ])
        assert result == 0
        out = capsys.readouterr().out
        assert "https://github.com/o/r/issues/42" in out

    def test_create_missing_body_file(self, capsys):
        with pytest.raises(SystemExit):
            main([
                "create",
                "--project", "myproj",
                "--title", "Fix bug",
                "--body-file", "/nonexistent/body.md",
            ])
        err = capsys.readouterr().err
        assert "body file not found" in err

    def test_create_api_error(self, tmp_path, capsys):
        body_file = tmp_path / "issue.md"
        body_file.write_text("Body")
        with patch("app.issue_cli.create_issue", side_effect=RuntimeError("auth failed")):
            result = main([
                "create",
                "--project", "myproj",
                "--title", "Bug",
                "--body-file", str(body_file),
            ])
        assert result == 1
        err = capsys.readouterr().err
        assert "auth failed" in err
