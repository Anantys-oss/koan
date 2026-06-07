"""Tests for ASCII art banner module."""

import io
from unittest.mock import patch

from app.banners import (
    BLUE,
    BOLD,
    CYAN,
    MAGENTA,
    RESET,
    WHITE,
    YELLOW,
    _ANSI_RE,
    _format_info_lines,
    _read_art,
    _visible_len,
    colorize_agent,
    colorize_bridge,
    colorize_startup,
    print_agent_banner,
    print_bridge_banner,
    print_startup_banner,
)


class TestReadArt:
    def test_reads_agent_art(self):
        art = _read_art("agent.txt")
        assert art
        assert "K Ō A N" in art

    def test_reads_bridge_art(self):
        art = _read_art("bridge.txt")
        assert art
        assert "A W A K E" in art

    def test_missing_file_returns_empty(self):
        art = _read_art("nonexistent.txt")
        assert art == ""

    def test_agent_art_within_size_limit(self):
        art = _read_art("agent.txt")
        lines = [l for l in art.split("\n") if l.strip()]
        assert len(lines) <= 16, f"Agent art has {len(lines)} lines, max is 16"
        for line in lines:
            assert len(line) <= 16, f"Line too wide ({len(line)}): {line!r}"

    def test_bridge_art_within_size_limit(self):
        art = _read_art("bridge.txt")
        lines = [l for l in art.split("\n") if l.strip()]
        assert len(lines) <= 16, f"Bridge art has {len(lines)} lines, max is 16"
        for line in lines:
            assert len(line) <= 24, f"Line too wide ({len(line)}): {line!r}"


class TestColorize:
    def test_agent_colors_eyes(self):
        result = colorize_agent("◉ test ◉")
        assert CYAN in result
        assert RESET in result

    def test_agent_colors_radioactive(self):
        result = colorize_agent("──☢──")
        assert YELLOW in result

    def test_bridge_colors_diamonds(self):
        result = colorize_bridge("◇ signal ◇")
        assert CYAN in result

    def test_bridge_colors_arrows(self):
        result = colorize_bridge("→→ data ←←")
        assert "\033[32m" in result  # GREEN

    def test_plain_text_gets_base_color(self):
        result = colorize_agent("plain line")
        assert BLUE in result
        result = colorize_bridge("plain line")
        assert MAGENTA in result


class TestPrintBanners:
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_print_agent_banner(self, mock_stdout):
        print_agent_banner()
        output = mock_stdout.getvalue()
        assert "K" in output  # Part of "K Ō A N"

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_print_agent_banner_with_version(self, mock_stdout):
        print_agent_banner("v1.0 — test")
        output = mock_stdout.getvalue()
        assert "v1.0" in output

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_print_bridge_banner(self, mock_stdout):
        print_bridge_banner()
        output = mock_stdout.getvalue()
        assert "A W A K E" in output

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_print_bridge_banner_with_version(self, mock_stdout):
        print_bridge_banner("v1.0 — bridge")
        output = mock_stdout.getvalue()
        assert "v1.0" in output

    @patch("app.banners._read_art", return_value="")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_missing_art_prints_nothing(self, mock_stdout, mock_read):
        print_agent_banner("version")
        assert mock_stdout.getvalue() == ""

    @patch("app.banners._read_art", return_value="")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_missing_bridge_art_prints_nothing(self, mock_stdout, mock_read):
        print_bridge_banner("version")
        assert mock_stdout.getvalue() == ""


class TestStartupArt:
    def test_reads_startup_art(self):
        art = _read_art("startup.txt")
        assert art
        assert "K Ō A N" in art

    def test_startup_art_within_size_limit(self):
        art = _read_art("startup.txt")
        lines = [l for l in art.split("\n") if l.strip()]
        assert len(lines) <= 12, f"Startup art has {len(lines)} lines, max is 12"
        for line in lines:
            assert len(line) <= 50, f"Line too wide ({len(line)}): {line!r}"


class TestColorizeStartup:
    def test_colors_eyes(self):
        # Anantys mint theme: eyes use the mint accent, not cyan.
        from app.banners.theme import MINT, _seq
        result = colorize_startup("│ ◉  ◉ │")
        assert _seq(MINT) in result

    def test_colors_radioactive(self):
        result = colorize_startup("├──☢───┤")
        assert YELLOW in result

    def test_colors_title(self):
        from app.banners.theme import MINT, _seq
        result = colorize_startup("K Ō A N")
        assert BOLD in result
        assert _seq(MINT, bold=True) in result

    def test_colors_tagline(self):
        result = colorize_startup("cognitive sparring partner")
        assert WHITE in result

    def test_base_color_blue(self):
        result = colorize_startup("plain line")
        assert BLUE in result


class TestVisibleLen:
    def test_plain_text(self):
        assert _visible_len("hello") == 5

    def test_text_with_ansi(self):
        text = f"{CYAN}hello{RESET}"
        assert _visible_len(text) == 5

    def test_empty_string(self):
        assert _visible_len("") == 0

    def test_mixed_ansi(self):
        text = f"{BOLD}{CYAN}K{RESET} {WHITE}O{RESET}"
        assert _visible_len(text) == 3


class TestFormatInfoLines:
    def test_formats_all_known_keys(self):
        info = {"provider": "claude", "projects": "3", "skills": "29", "soul": "22k", "messaging": "telegram"}
        lines = _format_info_lines(info)
        assert len(lines) == 5
        assert "Provider" in lines[0]
        assert "claude" in lines[0]

    def test_skips_unknown_keys(self):
        info = {"provider": "claude", "extra_unknown": "value"}
        lines = _format_info_lines(info)
        assert len(lines) == 1

    def test_empty_dict(self):
        assert _format_info_lines({}) == []


class TestPrintStartupBanner:
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_prints_art_and_info(self, mock_stdout):
        info = {"provider": "claude", "skills": "29 core"}
        print_startup_banner(info)
        output = mock_stdout.getvalue()
        assert "K" in output  # From "K Ō A N"
        assert "Provider" in output
        assert "claude" in output
        assert "Skills" in output

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_prints_without_info(self, mock_stdout):
        print_startup_banner()
        output = mock_stdout.getvalue()
        assert "K" in output

    @patch("app.banners._read_art", return_value="")
    @patch("sys.stdout", new_callable=io.StringIO)
    def test_missing_art_prints_nothing(self, mock_stdout, mock_read):
        print_startup_banner({"provider": "claude"})
        assert mock_stdout.getvalue() == ""

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_two_column_alignment(self, mock_stdout):
        info = {"provider": "claude", "projects": "3 (koan, app, web)"}
        print_startup_banner(info)
        output = mock_stdout.getvalue()
        # Info lines should appear on the same line as art lines
        lines = output.split("\n")
        lines_with_provider = [l for l in lines if "Provider" in l]
        assert len(lines_with_provider) == 1
        # The provider info should be on a line that also has art content
        line = lines_with_provider[0]
        assert "┌" in line or "│" in line or "├" in line or "└" in line

    def test_long_values_truncated(self):
        info = {"provider": "x" * 100}
        lines = _format_info_lines(info)
        assert len(lines) == 1
        # Visible text should be truncated with ellipsis
        visible = _ANSI_RE.sub("", lines[0])
        assert "…" in visible
        assert len(visible.split(": ", 1)[1]) <= 50
