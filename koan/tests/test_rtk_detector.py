"""Tests for app.rtk_detector — optional rtk binary detection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with a clean detector cache."""
    from app.rtk_detector import reset_cache
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# detect_rtk — binary not on PATH
# ---------------------------------------------------------------------------


class TestRtkNotInstalled:
    def test_returns_not_installed(self):
        from app.rtk_detector import detect_rtk

        with patch("app.rtk_detector.shutil.which", return_value=None):
            status = detect_rtk()

        assert status.installed is False
        assert status.version is None
        assert status.binary_path is None

    def test_summary_line_when_missing(self):
        from app.rtk_detector import detect_rtk

        with patch("app.rtk_detector.shutil.which", return_value=None):
            assert detect_rtk().summary_line() == "rtk: not installed"

    def test_jq_probed_independently(self):
        """Even with no rtk binary, the jq probe still runs so /rtk can warn."""
        from app.rtk_detector import detect_rtk

        def fake_which(name):
            return "/usr/bin/jq" if name == "jq" else None

        with patch("app.rtk_detector.shutil.which", side_effect=fake_which):
            status = detect_rtk()

        assert status.installed is False
        assert status.jq_available is True


# ---------------------------------------------------------------------------
# detect_rtk — binary present
# ---------------------------------------------------------------------------


class TestRtkInstalled:
    def _patch_which(self, mock):
        """Make shutil.which find both rtk and jq."""
        def _which(name):
            if name == "rtk":
                return "/opt/homebrew/bin/rtk"
            if name == "jq":
                return "/opt/homebrew/bin/jq"
            return None
        mock.side_effect = _which

    def test_parses_version(self):
        from app.rtk_detector import detect_rtk

        completed = type("R", (), {"stdout": "rtk 0.28.2\n", "stderr": ""})()
        with patch("app.rtk_detector.shutil.which") as which, \
             patch("app.rtk_detector.subprocess.run", return_value=completed), \
             patch("app.rtk_detector._probe_hook", return_value=None), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            self._patch_which(which)
            status = detect_rtk()

        assert status.installed is True
        assert status.version == "0.28.2"
        assert status.jq_available is True
        assert status.binary_path == Path("/opt/homebrew/bin/rtk")

    def test_summary_line_with_active_hook(self):
        from app.rtk_detector import detect_rtk

        completed = type("R", (), {"stdout": "rtk 0.30.0\n", "stderr": ""})()
        with patch("app.rtk_detector.shutil.which") as which, \
             patch("app.rtk_detector.subprocess.run", return_value=completed), \
             patch("app.rtk_detector._probe_hook", return_value=True), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            self._patch_which(which)
            assert detect_rtk().summary_line() == "rtk 0.30.0 detected, hook: active"

    def test_summary_line_jq_missing(self):
        from app.rtk_detector import detect_rtk

        completed = type("R", (), {"stdout": "rtk 0.28.2\n", "stderr": ""})()

        def _which(name):
            return "/opt/homebrew/bin/rtk" if name == "rtk" else None

        with patch("app.rtk_detector.shutil.which", side_effect=_which), \
             patch("app.rtk_detector.subprocess.run", return_value=completed), \
             patch("app.rtk_detector._probe_hook", return_value=False), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            line = detect_rtk().summary_line()
        assert "rtk 0.28.2 detected, hook: inactive (jq missing)" == line

    def test_version_probe_timeout_returns_none(self):
        """Hung binary → version is None but installed remains True."""
        from app.rtk_detector import detect_rtk
        import subprocess as sp

        with patch("app.rtk_detector.shutil.which") as which, \
             patch(
                 "app.rtk_detector.subprocess.run",
                 side_effect=sp.TimeoutExpired(cmd="rtk", timeout=2.0),
             ), \
             patch("app.rtk_detector._probe_hook", return_value=None), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            self._patch_which(which)
            status = detect_rtk()
        assert status.installed is True
        assert status.version is None


# ---------------------------------------------------------------------------
# Hook probe
# ---------------------------------------------------------------------------


class TestHookProbe:
    def test_missing_settings_file_returns_none(self, tmp_path):
        from app.rtk_detector import _probe_hook

        assert _probe_hook(tmp_path / "settings.json") is None

    def test_no_marker_returns_false(self, tmp_path):
        from app.rtk_detector import _probe_hook

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"hooks": {}}))
        assert _probe_hook(settings) is False

    def test_marker_present_returns_true(self, tmp_path):
        from app.rtk_detector import _probe_hook

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "~/.claude/hooks/rtk-rewrite.sh"}
                    ]}
                ]
            }
        }))
        assert _probe_hook(settings) is True

    def test_invalid_json_returns_none(self, tmp_path):
        from app.rtk_detector import _probe_hook

        settings = tmp_path / "settings.json"
        settings.write_text("{not json")
        assert _probe_hook(settings) is None

    def test_marker_in_invalid_json_returns_none(self, tmp_path):
        """A JSON-broken settings file with the marker should not falsely report active."""
        from app.rtk_detector import _probe_hook

        settings = tmp_path / "settings.json"
        # Marker present, but file is not valid JSON.
        settings.write_text('{"hooks": "rtk-rewrite.sh"')  # missing closing brace
        assert _probe_hook(settings) is None

    def test_invalid_utf8_returns_none(self, tmp_path):
        """Settings.json saved with the wrong encoding must not blow up the probe.

        Regression for #1295: ``UnicodeDecodeError`` is a ``ValueError``, not
        an ``OSError`` — without an explicit catch it would escape and
        clobber the binary/version probes in :func:`detect_rtk`.
        """
        from app.rtk_detector import _probe_hook

        settings = tmp_path / "settings.json"
        # 0xff is invalid as a leading byte in UTF-8.
        settings.write_bytes(b'\xff\xfe{"hooks":{}}')
        assert _probe_hook(settings) is None

    def test_invalid_utf8_does_not_clobber_install_status(self, tmp_path):
        """Even with a broken settings.json, ``installed`` must remain True."""
        from app.rtk_detector import detect_rtk

        completed = type("R", (), {"stdout": "rtk 0.28.2\n", "stderr": ""})()
        settings = tmp_path / "settings.json"
        settings.write_bytes(b"\xff\xfe{")  # invalid UTF-8

        with patch("app.rtk_detector.shutil.which", return_value="/usr/bin/rtk"), \
             patch("app.rtk_detector.subprocess.run", return_value=completed), \
             patch("app.rtk_detector._claude_settings_path", return_value=settings), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            status = detect_rtk()

        assert status.installed is True
        assert status.version == "0.28.2"
        assert status.hook_active is None


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------


class TestCache:
    def test_second_call_does_not_re_probe(self):
        from app.rtk_detector import detect_rtk

        completed = type("R", (), {"stdout": "rtk 0.28.2\n", "stderr": ""})()
        with patch("app.rtk_detector.shutil.which", return_value="/usr/bin/rtk") as which, \
             patch("app.rtk_detector.subprocess.run", return_value=completed) as run, \
             patch("app.rtk_detector._probe_hook", return_value=None), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            detect_rtk()
            detect_rtk()  # second call
            detect_rtk()  # third call

        # which() is called twice on the first probe (rtk + jq) and not again.
        assert which.call_count == 2
        assert run.call_count == 1

    def test_force_reprobes(self):
        from app.rtk_detector import detect_rtk

        completed = type("R", (), {"stdout": "rtk 0.28.2\n", "stderr": ""})()
        with patch("app.rtk_detector.shutil.which", return_value="/usr/bin/rtk"), \
             patch("app.rtk_detector.subprocess.run", return_value=completed) as run, \
             patch("app.rtk_detector._probe_hook", return_value=None), \
             patch("app.rtk_detector._probe_config_path", return_value=None):
            detect_rtk()
            detect_rtk(force=True)

        assert run.call_count == 2
