"""Tests for the bridge_log module — colored log output for awake process."""

import re
from unittest.mock import patch

from app.bridge_log import log, _COLORS, _RESET, _DEFAULT_COLOR, _use_color

# Matches the [HH:MM:SS] timestamp prefix bridge_log prepends in non-color mode.
_TS = r"\[\d{2}:\d{2}:\d{2}\] "


class TestUseColor:
    """Test color detection logic."""

    def test_returns_true_for_real_tty(self):
        """When stderr has isatty() returning True, _use_color() returns True."""
        with patch("app.bridge_log.sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            with patch.dict("os.environ", {}, clear=False):
                assert _use_color() is True

    def test_returns_false_for_pipe(self):
        """When stderr has isatty() returning False, _use_color() returns False."""
        with patch("app.bridge_log.sys.stderr") as mock_stderr, \
             patch.dict("os.environ", {"KOAN_FORCE_COLOR": ""}, clear=False):
            mock_stderr.isatty.return_value = False
            assert _use_color() is False

    def test_returns_false_when_no_isatty(self):
        """When stderr has no isatty attribute, returns False."""
        with patch("app.bridge_log.sys.stderr", new=object()):
            with patch.dict("os.environ", {}, clear=True):
                assert _use_color() is False

    def test_force_color_overrides_pipe(self):
        """KOAN_FORCE_COLOR env var forces color output."""
        with patch("app.bridge_log.sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = False
            with patch.dict("os.environ", {"KOAN_FORCE_COLOR": "1"}):
                assert _use_color() is True


class TestLogColorCategories:
    """Test that each category gets the right color."""

    def test_init_category_has_blue(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("init", "Starting up")
        output = capsys.readouterr().err
        assert "[init]" in output
        assert "Starting up" in output
        assert _COLORS["init"] in output

    def test_chat_category_has_cyan(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("chat", "Received message")
        output = capsys.readouterr().err
        assert "[chat]" in output
        assert _COLORS["chat"] in output

    def test_mission_category_has_green(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("mission", "Mission queued")
        output = capsys.readouterr().err
        assert "[mission]" in output
        assert _COLORS["mission"] in output

    def test_outbox_category_has_magenta(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("outbox", "Flushed")
        output = capsys.readouterr().err
        assert "[outbox]" in output
        assert _COLORS["outbox"] in output

    def test_error_category_has_bold_red(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("error", "Something failed")
        output = capsys.readouterr().err
        assert "[error]" in output
        assert _COLORS["error"] in output

    def test_health_category_has_yellow(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("health", "Compacted")
        output = capsys.readouterr().err
        assert "[health]" in output
        assert _COLORS["health"] in output

    def test_skill_category_has_dim_green(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("skill", "Dispatching /status")
        output = capsys.readouterr().err
        assert "[skill]" in output
        assert _COLORS["skill"] in output

    def test_unknown_category_uses_default(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("foobar", "Unknown category")
        output = capsys.readouterr().err
        assert "[foobar]" in output
        assert _DEFAULT_COLOR in output


class TestLogNoColor:
    """Test that colors are stripped in non-TTY mode (pipes, CI)."""

    def test_no_ansi_codes_when_not_tty(self, capsys):
        with patch("app.bridge_log._use_color", return_value=False):
            log("error", "Something failed")
        output = capsys.readouterr().err
        assert re.fullmatch(_TS + r"\[error\] Something failed\n", output)
        assert "\033[" not in output

    def test_plain_prefix_format(self, capsys):
        with patch("app.bridge_log._use_color", return_value=False):
            log("init", "Token loaded")
        output = capsys.readouterr().err
        assert re.fullmatch(_TS + r"\[init\] Token loaded\n", output)

    def test_all_categories_no_escape_codes(self, capsys):
        """Every category should produce clean output in non-TTY mode."""
        for category in _COLORS:
            with patch("app.bridge_log._use_color", return_value=False):
                log(category, "test message")
            output = capsys.readouterr().err
            assert "\033[" not in output, f"ANSI codes found for category '{category}'"
            assert f"[{category}] test message" in output


class TestLogOutput:
    """Test log output formatting."""

    def test_color_output_includes_reset(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("init", "hello")
        output = capsys.readouterr().err
        assert _RESET in output

    def test_message_after_prefix(self, capsys):
        with patch("app.bridge_log._use_color", return_value=True):
            log("chat", "user said hello")
        output = capsys.readouterr().err
        assert "user said hello" in output

    def test_special_characters_in_message(self, capsys):
        with patch("app.bridge_log._use_color", return_value=False):
            log("chat", "Received: /help")
        output = capsys.readouterr().err
        assert "Received: /help" in output

    def test_empty_message(self, capsys):
        with patch("app.bridge_log._use_color", return_value=False):
            log("init", "")
        output = capsys.readouterr().err
        assert re.fullmatch(_TS + r"\[init\] \n", output)

    def test_timestamp_prefix_present(self, capsys):
        """Every line starts with an [HH:MM:SS] timestamp for debugging."""
        with patch("app.bridge_log._use_color", return_value=False):
            log("mission", "queued")
        output = capsys.readouterr().err
        assert re.match(r"^\[\d{2}:\d{2}:\d{2}\] \[mission\] queued\n$", output)

    def test_timestamp_prefix_present_with_color(self, capsys):
        """Timestamp is emitted (dim-wrapped) even in color mode."""
        with patch("app.bridge_log._use_color", return_value=True):
            log("mission", "queued")
        output = capsys.readouterr().err
        assert re.search(r"\[\d{2}:\d{2}:\d{2}\]", output)

    def test_writes_to_stderr_not_stdout(self, capsys):
        """Log output must go to stderr, not stdout."""
        with patch("app.bridge_log._use_color", return_value=False):
            log("init", "stderr check")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "stderr check" in captured.err


class TestColorConstants:
    """Test that the color mapping is consistent with run.py."""

    def test_all_expected_categories_defined(self):
        expected = {"init", "chat", "mission", "outbox", "error", "health", "skill"}
        assert set(_COLORS.keys()) == expected

    def test_error_is_bold_red(self):
        assert "\033[1m" in _COLORS["error"]  # bold
        assert "\033[31m" in _COLORS["error"]  # red

    def test_skill_is_dim_green(self):
        assert "\033[2m" in _COLORS["skill"]   # dim
        assert "\033[32m" in _COLORS["skill"]  # green
