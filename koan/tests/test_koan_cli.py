"""Tests for the interactive launcher (app.koan_cli) and Anantys theme."""

import io
from pathlib import Path

from app import koan_cli
from app.banners import theme


def _tty(monkeypatch, is_tty: bool):
    fake = io.StringIO("")
    fake.isatty = lambda: is_tty
    monkeypatch.setattr("sys.stdin", fake)


# --- run() flow -------------------------------------------------------------

def test_run_non_tty_delegates_headless(monkeypatch):
    _tty(monkeypatch, False)
    started = {}
    monkeypatch.setattr("app.pid_manager.start_all",
                        lambda root, **kw: started.setdefault("root", root) or {})
    assert koan_cli.run(Path("/tmp/x")) == 0
    assert started["root"] == Path("/tmp/x")


def test_run_tty_starts_stack_runs_tui_then_stops(monkeypatch):
    _tty(monkeypatch, True)
    calls = []
    monkeypatch.setattr("app.koan_cli._clear_screen", lambda: None)
    monkeypatch.setattr("app.onboarding_helpers.onboarding_needed", lambda root: False)
    monkeypatch.setattr("app.pid_manager.start_all",
                        lambda root, **kw: calls.append("start") or {})
    monkeypatch.setattr("app.tui_dashboard.run",
                        lambda root: calls.append("tui") or 0)
    monkeypatch.setattr("app.pid_manager.stop_processes",
                        lambda *a, **k: calls.append("stop"))
    assert koan_cli.run(Path("/tmp/x")) == 0
    # Stack starts in a background thread, so start/tui order is unspecified;
    # what matters is both ran and the stack was torn down last.
    assert "start" in calls and "tui" in calls
    assert calls[-1] == "stop"


def test_run_tty_detach_keeps_running(monkeypatch):
    _tty(monkeypatch, True)
    calls = []
    monkeypatch.setattr("app.koan_cli._clear_screen", lambda: None)
    monkeypatch.setattr("app.onboarding_helpers.onboarding_needed", lambda root: False)
    monkeypatch.setattr("app.pid_manager.start_all",
                        lambda root, **kw: calls.append("start") or {})
    monkeypatch.setattr("app.tui_dashboard.run",
                        lambda root: calls.append("tui") or True)  # detached
    monkeypatch.setattr("app.pid_manager.stop_processes",
                        lambda *a, **k: calls.append("stop"))
    assert koan_cli.run(Path("/tmp/x")) == 0
    assert "tui" in calls
    assert "stop" not in calls  # detach → no tear-down


def test_run_tty_without_textual_keeps_running(monkeypatch):
    _tty(monkeypatch, True)
    monkeypatch.setattr("app.koan_cli._clear_screen", lambda: None)
    monkeypatch.setattr("app.onboarding_helpers.onboarding_needed", lambda root: False)
    monkeypatch.setattr("app.pid_manager.start_all", lambda root, **kw: {})
    stopped = {"called": False}
    monkeypatch.setattr("app.pid_manager.stop_processes",
                        lambda *a, **k: stopped.update(called=True))

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "app.tui_dashboard":
            raise ImportError("No module named 'textual'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert koan_cli.run(Path("/tmp/x")) == 0
    # textual missing → Kōan stays up, stack not torn down.
    assert stopped["called"] is False


def test_run_tty_first_run_onboards_before_stack(monkeypatch):
    _tty(monkeypatch, True)
    calls = []
    monkeypatch.setattr("app.koan_cli._clear_screen", lambda: None)
    monkeypatch.setattr("app.onboarding_helpers.onboarding_needed", lambda root: True)
    monkeypatch.setattr("app.onboarding.run_onboarding",
                        lambda: calls.append("onboard"))
    monkeypatch.setattr("app.pid_manager.start_all",
                        lambda root, **kw: calls.append("start") or {})
    monkeypatch.setattr("app.tui_dashboard.run",
                        lambda root: calls.append("tui") or True)

    assert koan_cli.run(Path("/tmp/x")) == 0
    assert calls[0] == "onboard"
    assert "start" in calls


def test_clear_screen_emits_escape(monkeypatch, capsys):
    monkeypatch.setattr(theme, "supports_color", lambda *a, **k: True)
    koan_cli._clear_screen()
    out = capsys.readouterr().out
    assert "\033[2J" in out


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


# --- railway attach / permission-aware wizard -------------------------------

def test_run_attaches_when_daemon_running(monkeypatch):
    _tty(monkeypatch, True)
    monkeypatch.setenv("KOAN_DEPLOY", "railway")
    monkeypatch.setattr(koan_cli, "_clear_screen", lambda: None)
    monkeypatch.setattr("app.railway.daemon_running", lambda root: True)
    called = {"onboard": False, "attach": False}
    monkeypatch.setattr("app.onboarding.run_onboarding",
                        lambda: called.__setitem__("onboard", True))
    monkeypatch.setattr(koan_cli, "_attach",
                        lambda root: called.__setitem__("attach", True) or 0)
    koan_cli.run(Path("/tmp/test-koan"))
    assert called["attach"] is True
    assert called["onboard"] is False


def test_run_surfaces_permission_error(monkeypatch, capsys):
    _tty(monkeypatch, True)
    monkeypatch.setenv("KOAN_DEPLOY", "railway")
    monkeypatch.setattr(koan_cli, "_clear_screen", lambda: None)
    monkeypatch.setattr("app.railway.daemon_running", lambda root: False)
    monkeypatch.setattr("app.onboarding_helpers.onboarding_needed",
                        lambda root: True)
    monkeypatch.setattr("app.railway.ensure_volume_writable",
                        lambda d: (False, "Permission denied writing to /app/instance"))
    onboard = {"called": False}
    monkeypatch.setattr("app.onboarding.run_onboarding",
                        lambda: onboard.__setitem__("called", True))
    rc = koan_cli.run(Path("/tmp/test-koan"))
    out = capsys.readouterr().out
    assert "Permission denied" in out
    assert onboard["called"] is False
    assert rc != 0
