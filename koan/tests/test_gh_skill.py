"""Tests for the /gh skill handler."""

import os
import subprocess
from unittest.mock import MagicMock, patch

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


class TestGhSkill:
    """Test the /gh skill handler."""

    def test_returns_auth_status_in_code_block(self):
        from skills.core.gh.handler import handle

        ctx = MagicMock()
        mock_result = MagicMock(
            stdout="github.com\n  ✓ Logged in as testuser\n",
            stderr="",
        )
        with patch("skills.core.gh.handler.subprocess.run", return_value=mock_result):
            result = handle(ctx)
        assert result.startswith("```\n")
        assert result.endswith("\n```")
        assert "testuser" in result

    def test_uses_stderr_when_stdout_empty(self):
        from skills.core.gh.handler import handle

        ctx = MagicMock()
        mock_result = MagicMock(
            stdout="",
            stderr="github.com\n  ✓ Logged in as otheruser\n",
        )
        with patch("skills.core.gh.handler.subprocess.run", return_value=mock_result):
            result = handle(ctx)
        assert "otheruser" in result
        assert result.startswith("```\n")

    def test_gh_not_found(self):
        from skills.core.gh.handler import handle

        ctx = MagicMock()
        with patch("skills.core.gh.handler.subprocess.run", side_effect=FileNotFoundError):
            result = handle(ctx)
        assert "gh CLI not found" in result
        assert result.startswith("```\n")

    def test_timeout(self):
        from skills.core.gh.handler import handle

        ctx = MagicMock()
        with patch(
            "skills.core.gh.handler.subprocess.run",
            side_effect=subprocess.TimeoutExpired("gh", 10),
        ):
            result = handle(ctx)
        assert "timed out" in result
        assert result.startswith("```\n")

    def test_empty_output(self):
        from skills.core.gh.handler import handle

        ctx = MagicMock()
        mock_result = MagicMock(stdout="", stderr="")
        with patch("skills.core.gh.handler.subprocess.run", return_value=mock_result):
            result = handle(ctx)
        assert "No output" in result
        assert result.startswith("```\n")
