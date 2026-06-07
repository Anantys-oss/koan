#!/usr/bin/env python3
"""Kōan — terminal dashboard (textual).

A small, read-only TUI over Kōan's shared runtime files, launched by the
"Terminal view" choice in ``make koan`` (or ``make dashboard --tui``). Three
tabs:

    - Logs    live tail of logs/run.log + logs/awake.log
    - Config  instance/config.yaml view + drift status (read-only)
    - Usage   session/weekly %, mode, and the raw usage.md snapshot

The dashboard never mutates runtime state except the one explicit action
``p`` (pause), which writes ``.koan-pause`` through the same helper the
bridge uses. ``textual`` is an optional dependency; importing this module
raises ImportError when it is missing, and the launcher falls back to
``make logs``.

Anantys mint theme, no emojis.
"""

from collections import deque
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

# Mint accent for textual CSS (truecolor hex from the Anantys palette).
_MINT = "#3ECF8E"
_MIDNIGHT = "#0D1117"

_LOG_TAIL_LINES = 400


def _tail(path: Path, limit: int = _LOG_TAIL_LINES) -> list:
    """Return the last ``limit`` lines of a file, or [] if absent."""
    if not path.exists():
        return []
    try:
        with path.open("r", errors="replace") as fh:
            return list(deque(fh, maxlen=limit))
    except OSError:
        return []


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


class KoanDashboard(App):
    """Read-only terminal dashboard for a running Kōan instance."""

    CSS = f"""
    Screen {{ background: {_MIDNIGHT}; }}
    Header {{ background: {_MIDNIGHT}; color: {_MINT}; text-style: bold; }}
    Footer {{ background: {_MIDNIGHT}; }}
    TabbedContent {{ height: 1fr; }}
    Tabs {{ background: {_MIDNIGHT}; }}
    Tab {{ color: $text-muted; }}
    Tab.-active {{ color: {_MINT}; text-style: bold; }}
    .pane {{ padding: 0 1; color: $text; }}
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "pause", "Pause Kōan"),
        ("r", "refresh", "Refresh"),
    ]

    TITLE = "Kōan"

    def __init__(self, koan_root: Path):
        super().__init__()
        self.koan_root = Path(koan_root)
        self._paused = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="logs"):
            with TabPane("Logs", id="logs"):
                yield Container(Static(id="logs-body", classes="pane"))
            with TabPane("Config", id="config"):
                yield Container(Static(id="config-body", classes="pane"))
            with TabPane("Usage", id="usage"):
                yield Container(Static(id="usage-body", classes="pane"))
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_all()
        self.set_interval(2.0, self.refresh_all)

    # --- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_all()

    def action_pause(self) -> None:
        try:
            from app.pause_manager import create_pause, is_paused, remove_pause

            if is_paused(str(self.koan_root)):
                remove_pause(str(self.koan_root))
                self._paused = False
                self.notify("Kōan resumed")
            else:
                create_pause(str(self.koan_root), "manual", display="paused from dashboard")
                self._paused = True
                self.notify("Kōan paused")
        except Exception as exc:  # pragma: no cover - defensive
            self.notify(f"pause failed: {exc}", severity="error")
        self.refresh_all()

    # --- rendering ----------------------------------------------------------

    def refresh_all(self) -> None:
        self._render_logs()
        self._render_config()
        self._render_usage()
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        from app.pause_manager import is_paused

        state = "paused" if is_paused(str(self.koan_root)) else "live"
        self.sub_title = f"{state} · run + awake"

    def _render_logs(self) -> None:
        logs_dir = self.koan_root / "logs"
        lines = []
        for name in ("run.log", "awake.log"):
            tagged = _tail(logs_dir / name, _LOG_TAIL_LINES // 2)
            lines.extend(f"[{name[:-4]}] {ln.rstrip()}" for ln in tagged)
        body = "\n".join(lines[-_LOG_TAIL_LINES:]) or "no logs yet — is Kōan running?"
        self.query_one("#logs-body", Static).update(body)

    def _render_config(self) -> None:
        cfg = self.koan_root / "instance" / "config.yaml"
        out = [_read(cfg) or "instance/config.yaml not found", ""]
        try:
            from app.config_validator import detect_config_drift, find_extra_config_keys

            missing = detect_config_drift(str(self.koan_root))
            extra = find_extra_config_keys(str(self.koan_root))
            if missing or extra:
                out.append("── drift (read-only — run /config_check) ──")
                out.extend(f"  + {k}" for k in missing)
                out.extend(f"  ~ {k} (extra)" for k in extra)
            else:
                out.append("config is in sync with the template")
        except Exception as exc:
            out.append(f"(drift check unavailable: {exc})")
        self.query_one("#config-body", Static).update("\n".join(out))

    def _render_usage(self) -> None:
        usage_md = self.koan_root / "instance" / "usage.md"
        lines = []
        try:
            from app.usage_tracker import UsageTracker

            t = UsageTracker(usage_md)
            lines.append(f"Session   {t.session_pct:.0f}%   reset in {t.session_reset}")
            lines.append(f"Weekly    {t.weekly_pct:.0f}%   reset in {t.weekly_reset}")
            lines.append("")
        except Exception as exc:
            lines.append(f"(usage parse unavailable: {exc})")
        lines.append(_read(usage_md) or "no usage.md yet")
        self.query_one("#usage-body", Static).update("\n".join(lines))


def run(koan_root: Path) -> int:
    """Launch the dashboard. Returns a process exit code."""
    KoanDashboard(Path(koan_root)).run()
    return 0
