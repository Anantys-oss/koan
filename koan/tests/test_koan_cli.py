"""Tests for the interactive launcher (app.koan_cli) and Anantys theme."""

import io
from pathlib import Path

import pytest

from app import koan_cli
from app.banners import theme


# --- key decoding -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("\x1b[A", "up"),
    ("\x1b[B", "down"),
    ("\r", "enter"),
    ("\n", "enter"),
    ("q", "q"),
])
def test_read_key_decodes(monkeypatch, raw, expected):
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    assert koan_cli._read_key() == expected


def test_read_key_ctrl_c_raises(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("\x03"))
    with pytest.raises(KeyboardInterrupt):
        koan_cli._read_key()


# --- selector fallback ------------------------------------------------------

def test_arrow_select_falls_back_when_not_tty(monkeypatch):
    fake = io.StringIO("")
    fake.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", fake)
    calls = {}

    def fake_choice(prompt, labels, default=0):
        calls["labels"] = labels
        return 2

    monkeypatch.setattr("app.onboarding.ask_choice", fake_choice)
    idx = koan_cli._arrow_select([("A", "a"), ("B", "b"), ("C", "c")], default=0)
    assert idx == 2
    assert len(calls["labels"]) == 3


# --- mode dispatch (no real processes) --------------------------------------

def test_run_non_tty_delegates_headless(monkeypatch):
    fake = io.StringIO("")
    fake.isatty = lambda: False
    monkeypatch.setattr("sys.stdin", fake)
    started = {}
    monkeypatch.setattr("app.pid_manager.start_all",
                        lambda root, **kw: started.setdefault("root", root) or {})
    assert koan_cli.run(Path("/tmp/x")) == 0
    assert started["root"] == Path("/tmp/x")


def test_choose_mode_maps_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(koan_cli, "_arrow_select", lambda *a, **k: 1)  # terminal
    assert koan_cli._choose_mode(tmp_path) == "terminal"
    monkeypatch.setattr(koan_cli, "_arrow_select", lambda *a, **k: 2)  # headless
    assert koan_cli._choose_mode(tmp_path) == "headless"


def test_launch_headless_no_stop(tmp_path, monkeypatch):
    monkeypatch.setattr("app.pid_manager.start_all",
                        lambda root, **kw: {"run": (True, "ok"), "awake": (True, "ok")})
    stopped = {"called": False}
    monkeypatch.setattr("app.pid_manager.stop_processes",
                        lambda *a, **k: stopped.update(called=True))
    assert koan_cli._launch_headless(tmp_path) == 0
    assert stopped["called"] is False  # headless keeps running


def test_launch_terminal_stops_after_session(tmp_path, monkeypatch):
    monkeypatch.setattr("app.pid_manager.start_all", lambda root, **kw: {})
    monkeypatch.setattr("app.tui_dashboard.run", lambda root: 0)
    stopped = {"called": False}
    monkeypatch.setattr("app.pid_manager.stop_processes",
                        lambda *a, **k: stopped.update(called=True))
    assert koan_cli._launch_terminal(tmp_path) == 0
    assert stopped["called"] is True  # quitting the dashboard tears down


# --- theme ------------------------------------------------------------------

def test_pixel_gradient_line_count():
    assert len(theme.pixel_gradient_lines(width=40, height=1)) == 1
    assert len(theme.pixel_gradient_lines(width=40, height=4)) == 4


def test_paint_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert theme.mint("hello") == "hello"


def test_paint_emits_ansi_when_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(theme, "supports_color", lambda *a, **k: True)
    monkeypatch.setenv("COLORTERM", "truecolor")
    out = theme.mint("hi")
    assert "\033[" in out and "hi" in out
