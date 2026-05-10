"""Tests for the /rtk skill handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


def _make_ctx(args: str, koan_root: Path):
    instance = koan_root / "instance"
    instance.mkdir(parents=True, exist_ok=True)
    ctx = MagicMock(spec=SkillContext)
    ctx.command_name = "rtk"
    ctx.args = args
    ctx.koan_root = koan_root
    ctx.instance_dir = instance
    return ctx


@pytest.fixture(autouse=True)
def _reset_detector():
    from app.rtk_detector import reset_cache
    reset_cache()
    yield
    reset_cache()


def _fake_status(**kwargs):
    """Build a real RtkStatus so .summary_line()/etc. work."""
    from app.rtk_detector import RtkStatus
    defaults = dict(
        installed=False, version=None, hook_active=None,
        jq_available=False, config_path=None, binary_path=None,
    )
    defaults.update(kwargs)
    return RtkStatus(**defaults)


# ---------------------------------------------------------------------------
# /rtk (status)
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_when_not_installed(self, tmp_path):
        from skills.core.rtk.handler import handle

        with patch("app.rtk_detector.detect_rtk", return_value=_fake_status()):
            result = handle(_make_ctx("", tmp_path))

        assert "not installed" in result
        assert "brew install rtk" in result

    def test_status_when_installed_with_active_hook(self, tmp_path):
        from skills.core.rtk.handler import handle

        status = _fake_status(
            installed=True, version="0.28.2", hook_active=True,
            jq_available=True, binary_path=Path("/opt/homebrew/bin/rtk"),
        )
        with patch("app.rtk_detector.detect_rtk", return_value=status), \
             patch("app.config.is_rtk_awareness_enabled", return_value=True):
            result = handle(_make_ctx("", tmp_path))

        assert "0.28.2" in result
        assert "active" in result
        assert "✅" in result

    def test_status_warns_when_hook_inactive(self, tmp_path):
        from skills.core.rtk.handler import handle

        status = _fake_status(
            installed=True, version="0.28.2", hook_active=False, jq_available=True,
            binary_path=Path("/usr/bin/rtk"),
        )
        with patch("app.rtk_detector.detect_rtk", return_value=status), \
             patch("app.config.is_rtk_awareness_enabled", return_value=True):
            result = handle(_make_ctx("", tmp_path))

        assert "/rtk setup" in result


# ---------------------------------------------------------------------------
# /rtk help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_lists_subcommands(self, tmp_path):
        from skills.core.rtk.handler import handle

        result = handle(_make_ctx("help", tmp_path))
        for sub in ("setup", "uninstall", "gain", "discover"):
            assert sub in result


# ---------------------------------------------------------------------------
# /rtk setup
# ---------------------------------------------------------------------------


class TestSetup:
    def test_setup_blocks_when_rtk_missing(self, tmp_path):
        from skills.core.rtk.handler import handle

        with patch("skills.core.rtk.handler.shutil.which", return_value=None):
            result = handle(_make_ctx("setup", tmp_path))

        assert "not installed" in result
        assert "brew install rtk" in result

    def test_setup_preview_without_confirm(self, tmp_path):
        from skills.core.rtk.handler import handle

        with patch("skills.core.rtk.handler.shutil.which", return_value="/usr/bin/rtk"), \
             patch("app.rtk_detector.detect_rtk", return_value=_fake_status(
                 installed=True, version="0.28.2", hook_active=False, jq_available=True,
             )):
            result = handle(_make_ctx("setup", tmp_path))

        assert "preview" in result.lower()
        assert "/rtk setup confirm" in result

    def test_setup_already_installed(self, tmp_path):
        from skills.core.rtk.handler import handle

        with patch("skills.core.rtk.handler.shutil.which", return_value="/usr/bin/rtk"), \
             patch("app.rtk_detector.detect_rtk", return_value=_fake_status(
                 installed=True, version="0.28.2", hook_active=True, jq_available=True,
             )):
            result = handle(_make_ctx("setup", tmp_path))

        assert "already installed" in result.lower()

    def test_setup_confirm_runs_init(self, tmp_path):
        from skills.core.rtk.handler import handle

        completed = type("R", (), {"returncode": 0, "stdout": "Hook installed\n", "stderr": ""})()
        with patch("skills.core.rtk.handler.shutil.which", return_value="/usr/bin/rtk"), \
             patch("skills.core.rtk.handler.subprocess.run", return_value=completed) as run_mock, \
             patch("app.rtk_detector.detect_rtk", return_value=_fake_status(
                 installed=True, version="0.28.2", hook_active=True, jq_available=True,
             )):
            result = handle(_make_ctx("setup confirm", tmp_path))

        assert "Hook installed" in result
        # Verify rtk init -g was actually invoked.
        called_args = run_mock.call_args[0][0]
        assert called_args[:3] == ["rtk", "init", "-g"]


# ---------------------------------------------------------------------------
# /rtk on / off — runtime override
# ---------------------------------------------------------------------------


class TestOnOff:
    def test_on_writes_override(self, tmp_path):
        from skills.core.rtk.handler import handle

        result = handle(_make_ctx("on", tmp_path))

        override = tmp_path / "instance" / ".koan-rtk-override"
        assert override.read_text().strip() == "on"
        assert "ON" in result

    def test_off_writes_override(self, tmp_path):
        from skills.core.rtk.handler import handle

        result = handle(_make_ctx("off", tmp_path))

        override = tmp_path / "instance" / ".koan-rtk-override"
        assert override.read_text().strip() == "off"
        assert "OFF" in result

    def test_on_uses_atomic_write(self, tmp_path):
        """Override must be written via app.utils.atomic_write per koan convention.

        Regression: a direct ``Path.write_text`` truncates+writes in two
        syscalls and exposes a window where a concurrent reader sees an
        empty file.
        """
        from unittest.mock import patch
        from skills.core.rtk.handler import handle

        with patch("app.utils.atomic_write") as mock_atomic:
            handle(_make_ctx("on", tmp_path))

        mock_atomic.assert_called_once()
        path_arg, content_arg = mock_atomic.call_args[0]
        assert path_arg.name == ".koan-rtk-override"
        assert content_arg == "on\n"


# ---------------------------------------------------------------------------
# SKILL.md frontmatter contract
# ---------------------------------------------------------------------------


class TestSkillManifest:
    def test_worker_true_is_set(self):
        """The /rtk skill shells out to subprocesses with timeouts up to 30s.

        Without ``worker: true`` the handler runs on the bridge thread and
        freezes Telegram polling for the duration of the subprocess.
        """
        from pathlib import Path
        from app.skills import parse_skill_md

        skill_md = Path(__file__).resolve().parents[1] / "skills" / "core" / "rtk" / "SKILL.md"
        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert getattr(skill, "worker", False) is True


# ---------------------------------------------------------------------------
# /rtk gain — passthrough
# ---------------------------------------------------------------------------


class TestGain:
    def test_gain_when_not_installed(self, tmp_path):
        from skills.core.rtk.handler import handle

        with patch("skills.core.rtk.handler.shutil.which", return_value=None):
            result = handle(_make_ctx("gain", tmp_path))
        assert "not found" in result.lower() or "❌" in result

    def test_gain_forwards_args(self, tmp_path):
        from skills.core.rtk.handler import handle

        completed = type("R", (), {
            "returncode": 0, "stdout": "Total saved: 12345 tokens\n", "stderr": ""
        })()
        with patch("skills.core.rtk.handler.shutil.which", return_value="/usr/bin/rtk"), \
             patch("skills.core.rtk.handler.subprocess.run", return_value=completed) as run_mock:
            handle(_make_ctx("gain --history", tmp_path))

        called = run_mock.call_args[0][0]
        assert called == ["rtk", "gain", "--history"]


# ---------------------------------------------------------------------------
# Unknown subcommand
# ---------------------------------------------------------------------------


class TestUnknown:
    def test_unknown_returns_help(self, tmp_path):
        from skills.core.rtk.handler import handle

        result = handle(_make_ctx("nonsense", tmp_path))
        assert "Unknown" in result
        assert "/rtk" in result
