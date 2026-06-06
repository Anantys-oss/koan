"""Tests for app.version and the /version skill."""

import os
import subprocess
from unittest.mock import MagicMock, patch

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


class TestGetVersion:
    """Test app.version.get_version()."""

    def test_exact_tag(self):
        from app.version import get_version
        mock_result = MagicMock(returncode=0, stdout="v0.76\n")
        with patch("app.version.subprocess.run", return_value=mock_result):
            assert get_version() == "v0.76"

    def test_ahead_of_tag(self):
        from app.version import get_version
        mock_result = MagicMock(returncode=0, stdout="v0.76-178-ga456c1e8\n")
        with patch("app.version.subprocess.run", return_value=mock_result):
            assert get_version() == "v0.76@a456c1e8 +178"

    def test_git_failure(self):
        from app.version import get_version
        mock_result = MagicMock(returncode=128, stdout="")
        with patch("app.version.subprocess.run", return_value=mock_result):
            assert get_version() == ""

    def test_git_not_found(self):
        from app.version import get_version
        with patch("app.version.subprocess.run", side_effect=FileNotFoundError):
            assert get_version() == ""

    def test_timeout(self):
        from app.version import get_version
        with patch("app.version.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 5)):
            assert get_version() == ""


class TestVersionSkill:
    """Test the /version skill handler."""

    def test_returns_version_string(self):
        from skills.core.version.handler import handle
        ctx = MagicMock()
        with patch("app.version.get_version", return_value="v0.76@a456c1e8 +178"):
            assert handle(ctx) == "v0.76@a456c1e8 +178"

    def test_returns_exact_tag(self):
        from skills.core.version.handler import handle
        ctx = MagicMock()
        with patch("app.version.get_version", return_value="v0.76"):
            assert handle(ctx) == "v0.76"

    def test_returns_unknown_on_failure(self):
        from skills.core.version.handler import handle
        ctx = MagicMock()
        with patch("app.version.get_version", return_value=""):
            assert handle(ctx) == "unknown"
