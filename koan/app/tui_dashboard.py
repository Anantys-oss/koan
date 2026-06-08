#!/usr/bin/env python3
"""Kōan — terminal dashboard (textual).

A themed TUI over Kōan's shared runtime files, launched by the "Terminal
view" choice in ``make koan`` (or ``make dashboard --tui``). Three tabs:

    - Logs    live tail of logs/run.log + logs/awake.log
    - Config  collapsible tree view of instance/config.yaml, with inline
              editing of scalar leaves (comment-preserving round-trip)
    - Usage   session/weekly progress bars, autonomous mode, burn rate

The only state-mutating actions are ``p`` (pause, via the same .koan-pause
signal the bridge uses) and editing a config value. ``textual`` is an
optional dependency; importing this module raises ImportError when it is
missing, and the launcher falls back to ``make logs``.

Anantys mint theme, no emojis.
"""

import contextlib
import logging
from collections import deque
from pathlib import Path

_log = logging.getLogger(__name__)

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
    Tree,
)

# Anantys palette (truecolor hex) for textual CSS + rich markup.
_MINT = "#3ECF8E"
_MINT_DIM = "#2E8A63"
_AMBER = "#DEAA5A"
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


def _load_config(koan_root: Path) -> dict:
    """Parse instance/config.yaml into a plain dict (best effort)."""
    cfg = koan_root / "instance" / "config.yaml"
    if not cfg.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(cfg.read_text()) or {}
    except Exception as exc:
        _log.debug("config load failed: %s", exc)
        return {}


def _coerce(raw: str):
    """Parse a user-entered string into the closest native YAML scalar."""
    try:
        import yaml

        value = yaml.safe_load(raw)
        # Keep multi-token plain strings as strings (yaml would too).
        return value
    except Exception as exc:
        _log.debug("coerce failed for %r: %s", raw, exc)
        return raw


def set_config_value(koan_root: Path, dotted_key: str, value) -> None:
    """Set a nested key in instance/config.yaml, preserving comments.

    Uses ruamel.yaml to round-trip the file so user comments and formatting
    survive the edit; falls back to pyyaml when ruamel is unavailable.
    """
    from app.utils import atomic_write

    path = Path(koan_root) / "instance" / "config.yaml"
    keys = dotted_key.split(".")

    try:
        import io

        from ruamel.yaml import YAML

        ry = YAML()
        ry.preserve_quotes = True
        data = ry.load(path.read_text()) if path.exists() else {}
        if data is None:
            data = {}
        node = data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        stream = io.StringIO()
        ry.dump(data, stream)
        atomic_write(path, stream.getvalue())
        return
    except ImportError:
        pass

    import yaml

    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    node = data
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value
    atomic_write(path, yaml.safe_dump(data, sort_keys=False))


class EditValueScreen(ModalScreen):
    """Modal prompt to edit one scalar config value."""

    CSS = f"""
    EditValueScreen {{ align: center middle; }}
    #box {{
        width: 70; height: auto; padding: 1 2;
        background: {_MIDNIGHT}; border: round {_MINT};
    }}
    #title {{ color: {_MINT}; text-style: bold; }}
    #hint {{ color: $text-muted; }}
    #buttons {{ height: auto; padding-top: 1; }}
    Button {{ margin-right: 2; }}
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, dotted_key: str, current):
        super().__init__()
        self.dotted_key = dotted_key
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(f"Edit  {self.dotted_key}", id="title")
            yield Label("enter to save · esc to cancel", id="hint")
            yield Input(value="" if self.current is None else str(self.current),
                        id="value")
            with Container(id="buttons"):
                yield Button("Save", variant="success", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        raw = self.query_one("#value", Input).value
        self.dismiss(_coerce(raw))


class ConfirmScreen(ModalScreen):
    """Yes/No confirmation modal. Dismisses with True (yes) or False."""

    CSS = f"""
    ConfirmScreen {{ align: center middle; }}
    #box {{ width: 64; height: auto; padding: 1 2;
            background: {_MIDNIGHT}; border: round {_AMBER}; }}
    #title {{ color: {_AMBER}; text-style: bold; }}
    #msg {{ color: $text; }}
    #buttons {{ height: auto; padding-top: 1; }}
    Button {{ margin-right: 2; }}
    """

    BINDINGS = [("escape", "no", "Cancel"), ("y", "yes", "Yes"), ("n", "no", "No")]

    def __init__(self, title: str, message: str):
        super().__init__()
        self._title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label(self._title, id="title")
            yield Label(self._message, id="msg")
            with Container(id="buttons"):
                yield Button("Yes (stop)", variant="error", id="yes")
                yield Button("Cancel", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class NewMissionScreen(ModalScreen):
    """Prompt for a new mission line; dismisses with the text or None."""

    CSS = f"""
    NewMissionScreen {{ align: center middle; }}
    #box {{ width: 84; height: auto; padding: 1 2;
            background: {_MIDNIGHT}; border: round {_MINT}; }}
    #title {{ color: {_MINT}; text-style: bold; }}
    #hint {{ color: $text-muted; }}
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label("New mission", id="title")
            yield Label("enter to queue · esc to cancel · tag with [project:name]",
                        id="hint")
            yield Input(placeholder="e.g. fix the flaky login test [project:my-app]",
                        id="mission")

    def on_mount(self) -> None:
        self.query_one("#mission", Input).focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self.query_one("#mission", Input).value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class KoanDashboard(App):
    """Terminal dashboard for a running Kōan instance."""

    CSS = f"""
    Screen {{ background: {_MIDNIGHT}; }}
    Header {{ background: {_MIDNIGHT}; color: {_MINT}; text-style: bold; }}
    Footer {{ background: {_MIDNIGHT}; }}
    TabbedContent {{ height: 1fr; }}
    Tabs {{ background: {_MIDNIGHT}; }}
    Tab {{ color: $text-muted; }}
    Tab.-active {{ color: {_MINT}; text-style: bold; }}
    .pane {{ padding: 0 1; color: $text; }}
    Tree {{ background: {_MIDNIGHT}; padding: 0 1; }}
    Tree > .tree--cursor {{ background: {_MINT_DIM}; color: {_MIDNIGHT}; }}
    """

    BINDINGS = [
        ("q", "request_quit", "Quit (stop)"),
        ("d", "detach", "Detach (keep running)"),
        ("1", "show('status')", "Status"),
        ("2", "show('logs')", "Logs"),
        ("3", "show('usage')", "Usage"),
        ("4", "show('config')", "Config"),
        # Letter aliases for the tabs (hidden from the footer to keep it tidy).
        Binding("s", "show('status')", "Status", show=False),
        Binding("l", "show('logs')", "Logs", show=False),
        Binding("u", "show('usage')", "Usage", show=False),
        Binding("c", "show('config')", "Config", show=False),
        ("m", "new_mission", "New mission"),
        ("w", "toggle_web", "Web dashboard"),
        ("k", "toggle_keepawake", "Keep awake"),
        ("t", "toggle", "Toggle bool"),
        ("p", "pause", "Pause Kōan"),
        ("r", "refresh", "Refresh"),
    ]

    TITLE = "Kōan"

    def __init__(self, koan_root: Path):
        super().__init__()
        self.koan_root = Path(koan_root)
        # Keep-awake subprocess handle (caffeinate / systemd-inhibit); on by default.
        self._keepawake = None
        self._keepawake_label = ""
        # Detach flag: True when the user closed the dashboard but left Kōan up.
        self._detached = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="status"):
            with TabPane("Status", id="status"):
                yield Container(Static(id="status-body", classes="pane"))
            with TabPane("Logs", id="logs"):
                yield RichLog(id="logs-body", classes="pane", markup=False, auto_scroll=True)
            with TabPane("Usage", id="usage"):
                yield Container(Static(id="usage-body", classes="pane"))
            with TabPane("Config", id="config"):
                yield Tree("config.yaml", id="config-tree")
                yield Static(id="config-status", classes="pane")
        yield Footer()

    def on_mount(self) -> None:
        self._build_config_tree()
        self._start_keepawake()  # keep the machine awake by default
        self.refresh_dynamic()
        self.set_interval(2.0, self.refresh_dynamic)

    def on_unmount(self) -> None:
        self._stop_keepawake()

    def on_tabbed_content_tab_activated(
        self, event: "TabbedContent.TabActivated"
    ) -> None:
        # Give keyboard focus to the config tree when its tab is shown so
        # arrow keys browse it (otherwise focus stays on the tab bar).
        if self.active_pane_id() == "config":
            self._focus_config_tree()

    def _focus_config_tree(self) -> None:
        try:
            self.query_one("#config-tree", Tree).focus()
        except Exception as exc:
            self.log(f"could not focus config tree: {exc}")

    # --- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        self._build_config_tree()
        self.refresh_dynamic()

    def action_show(self, pane: str) -> None:
        """Switch tabs via 1/2/3 — works even while the config tree has focus."""
        try:
            self.query_one(TabbedContent).active = pane
        except Exception as exc:
            self.log(f"tab switch failed: {exc}")
            return
        if pane == "config":
            self._focus_config_tree()
        else:
            # Move focus off the (now hidden) tree so it stops eating keys.
            try:
                self.query_one(Tabs).focus()
            except Exception as exc:
                self.log(f"tab focus failed: {exc}")

    def action_pause(self) -> None:
        try:
            from app.pause_manager import create_pause, is_paused, remove_pause

            if is_paused(str(self.koan_root)):
                remove_pause(str(self.koan_root))
                self.notify("Kōan resumed")
            else:
                create_pause(str(self.koan_root), "manual", display="paused from dashboard")
                self.notify("Kōan paused")
        except Exception as exc:  # pragma: no cover - defensive
            self.notify(f"pause failed: {exc}", severity="error")
        self.refresh_dynamic()

    # --- toggles (web dashboard, caffeinate) --------------------------------

    def _web_running(self) -> bool:
        try:
            from app.pid_manager import check_pidfile

            return check_pidfile(self.koan_root, "dashboard") is not None
        except Exception as exc:
            self.log(f"dashboard status check failed: {exc}")
            return False

    def action_toggle_web(self) -> None:
        """Start/stop the web dashboard with a single tap; open browser on start."""
        try:
            from app.pid_manager import start_dashboard, stop_process

            if self._web_running():
                stop_process(self.koan_root, "dashboard")
                self.notify("web dashboard stopped")
            else:
                ok, msg = start_dashboard(self.koan_root)
                if ok:
                    import webbrowser

                    with contextlib.suppress(Exception):
                        webbrowser.open("http://localhost:5001")
                    self.notify("web dashboard started — localhost:5001")
                else:
                    self.notify(f"dashboard: {msg}", severity="warning")
        except Exception as exc:
            self.notify(f"web toggle failed: {exc}", severity="error")
        self.refresh_dynamic()

    def _keepawake_command(self):
        """Return (argv, label) for a keep-awake command, or (None, "") if none."""
        import shutil

        if shutil.which("caffeinate"):  # macOS
            return ["caffeinate", "-s"], "caffeinate -s"
        if shutil.which("systemd-inhibit"):  # Linux
            return (["systemd-inhibit", "--what=sleep", "--why=Kōan",
                     "--mode=block", "sleep", "infinity"], "systemd-inhibit")
        return None, ""

    def _start_keepawake(self) -> None:
        """Keep the machine awake (caffeinate on macOS, systemd-inhibit on Linux)."""
        import subprocess

        if self._keepawake is not None:
            return
        argv, label = self._keepawake_command()
        if not argv:  # unsupported platform — quietly skip
            return
        try:
            self._keepawake = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._keepawake_label = label
        except Exception as exc:
            self.log(f"keep-awake start failed: {exc}")
            self._keepawake = None

    def _stop_keepawake(self) -> None:
        if self._keepawake is None:
            return
        with contextlib.suppress(Exception):
            self._keepawake.terminate()
            self._keepawake.wait(timeout=2)  # reap so it does not linger
        self._keepawake = None

    def _keepawake_on(self) -> bool:
        return self._keepawake is not None and self._keepawake.poll() is None

    def action_toggle_keepawake(self) -> None:
        if self._keepawake_on():
            self._stop_keepawake()
            self.notify("keep-awake off — machine may sleep")
        else:
            self._start_keepawake()
            if self._keepawake_on():
                self.notify(f"keep-awake on — {self._keepawake_label}")
            else:
                self.notify("keep-awake unavailable on this platform", severity="warning")
        self.refresh_dynamic()

    # --- detach / quit / new mission ---------------------------------------

    def action_detach(self) -> None:
        """Close the dashboard but leave Kōan running."""
        self._detached = True
        self.exit()

    def action_request_quit(self) -> None:
        """Confirm before stopping Kōan (q tears the stack down)."""
        def _confirmed(yes) -> None:
            if yes:
                self._detached = False
                self.exit()

        self.push_screen(ConfirmScreen(
            "Stop Kōan?",
            "This stops the agent + bridge. Use d to detach and keep it running."),
            _confirmed)

    def action_new_mission(self) -> None:
        """Queue a new mission into missions.md from a modal input."""
        def _submit(text_value) -> None:
            if not text_value:
                return
            try:
                from app.utils import insert_pending_mission

                md = self.koan_root / "instance" / "missions.md"
                ok = insert_pending_mission(md, text_value)
                self.notify("mission queued" if ok else "duplicate — already queued")
            except Exception as exc:
                self.notify(f"queue failed: {exc}", severity="error")
            self.refresh_dynamic()

        self.push_screen(NewMissionScreen(), _submit)

    def _selected_leaf(self):
        """Return (path, value) for the focused editable leaf, or None."""
        if self.active_pane_id() != "config":
            return None
        try:
            node = self.query_one("#config-tree", Tree).cursor_node
        except Exception as exc:
            self.log(f"tree lookup failed: {exc}")
            return None
        if not node or not isinstance(node.data, dict) or "path" not in node.data:
            return None
        return node.data["path"], node.data["value"]

    def _persist(self, path: str, value) -> None:
        try:
            set_config_value(self.koan_root, path, value)
            self.notify(f"set {path} = {self._format_scalar(value)}")
        except Exception as exc:
            self.notify(f"save failed: {exc}", severity="error")
        self._build_config_tree()

    def action_edit(self) -> None:
        leaf = self._selected_leaf()
        if leaf is None:
            return
        path, current = leaf
        # Booleans flip in place — no need to type true/false.
        if isinstance(current, bool):
            self._persist(path, not current)
            return

        def _apply(new_value) -> None:
            if new_value is None:
                return
            self._persist(path, new_value)

        self.push_screen(EditValueScreen(path, current), _apply)

    def action_toggle(self) -> None:
        """Flip the selected boolean leaf (space). No-op on non-booleans."""
        leaf = self._selected_leaf()
        if leaf is None:
            return
        path, current = leaf
        if isinstance(current, bool):
            self._persist(path, not current)

    def active_pane_id(self) -> str:
        try:
            return self.query_one(TabbedContent).active
        except Exception as exc:
            self.log(f"active pane lookup failed: {exc}")
            return ""

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, dict) and "path" in data:
            self.action_edit()

    # --- rendering ----------------------------------------------------------

    def refresh_dynamic(self) -> None:
        self._render_status()
        self._render_logs()
        self._render_usage()
        self._render_config_status()
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        from app.pause_manager import is_paused

        state = "paused" if is_paused(str(self.koan_root)) else "live"
        self.sub_title = (f"{state} · 1-4 tabs · m mission · w web · k awake"
                          f" · p pause · d detach · q stop")

    # --- status (home) ------------------------------------------------------

    def _dot(self, on: bool) -> str:
        """Anantys accent dot: filled mint when ON, empty muted when OFF."""
        return f"[{_MINT}]◉[/]" if on else "[dim]○[/]"

    def _in_progress_missions(self) -> list:
        """Return short titles of in-progress missions (best effort)."""
        try:
            from app.missions import parse_sections, strip_all_lifecycle_markers

            md = self.koan_root / "instance" / "missions.md"
            if not md.exists():
                return []
            items = parse_sections(md.read_text()).get("in_progress", [])
            titles = []
            for raw in items:
                line = strip_all_lifecycle_markers(raw).strip().lstrip("-").strip()
                line = line.splitlines()[0] if line else ""
                if line:
                    titles.append(line[:60] + ("…" if len(line) > 60 else ""))
            return titles
        except Exception as exc:
            self.log(f"mission list failed: {exc}")
            return []

    def _telegram_status(self):
        """Return (bridge_alive, configured) for the Telegram indicator."""
        import os

        configured = bool(os.environ.get("KOAN_TELEGRAM_TOKEN")
                          and os.environ.get("KOAN_TELEGRAM_CHAT_ID"))
        bridge = False
        try:
            from app.pid_manager import check_pidfile

            bridge = check_pidfile(self.koan_root, "awake") is not None
        except Exception as exc:
            self.log(f"bridge status failed: {exc}")
        return bridge, configured

    def _render_status(self) -> None:
        try:
            body = self.query_one("#status-body", Static)
        except Exception as exc:
            self.log(f"status widget missing: {exc}")
            return

        from rich.text import Text

        from app.banners import _read_art, colorize_hero
        from app.banners.theme import RESET
        from app.pause_manager import is_paused

        hero_art = ""
        try:
            hero_art = colorize_hero(_read_art("koan_hero.txt").rstrip("\n"))
        except Exception as exc:
            self.log(f"hero render failed: {exc}")

        out = Text.from_ansi(hero_art + RESET) if hero_art else Text("Kōan")
        out.append("\n\n")

        # Live status flags + single-tap toggles, rendered as markup.
        paused = is_paused(str(self.koan_root))
        titles = self._in_progress_missions()
        web_on = self._web_running()
        bridge, tg_configured = self._telegram_status()

        web_hint = "localhost:5001" if web_on else "start + open browser"
        awake_on = self._keepawake_on()
        awake_hint = self._keepawake_label if awake_on else "off"
        if bridge and tg_configured:
            tg = f"{self._dot(True)}  [dim]bridge live[/]"
        elif tg_configured:
            tg = f"{self._dot(False)}  [dim]configured · bridge down[/]"
        else:
            tg = f"{self._dot(False)}  [dim]not configured[/]"

        lines = [
            f"  state        {'[yellow]paused[/]' if paused else f'[{_MINT}]running[/]'}",
            f"  missions     [{_MINT}]{len(titles)}[/] in progress",
        ]
        lines.extend(f"                 [dim]·[/] {t}" for t in titles[:3])
        if len(titles) > 3:
            lines.append(f"                 [dim]… +{len(titles) - 3} more[/]")
        lines += [
            f"  telegram     {tg}",
            f"  web board    {self._dot(web_on)}  [dim](w · {web_hint})[/]",
            f"  keep awake   {self._dot(awake_on)}  [dim](k · {awake_hint})[/]",
        ]
        # Usage bars reuse the same renderer as the Usage tab.
        try:
            from app.usage_tracker import UsageTracker

            t = UsageTracker(self.koan_root / "instance" / "usage.md")
            lines.append("")
            lines.append("  " + self._bar("session", t.session_pct, t.session_reset))
            lines.append("  " + self._bar("weekly", t.weekly_pct, t.weekly_reset))
        except Exception as exc:
            self.log(f"status usage failed: {exc}")

        lines.append("")
        lines.append("  [dim]m new mission · w web · k awake · p pause · "
                     "d detach · q stop[/]")

        out.append_text(Text.from_markup("\n".join(lines)))
        body.update(out)

    def _render_logs(self) -> None:
        logs_dir = self.koan_root / "logs"
        lines = []
        for name in ("run.log", "awake.log"):
            tagged = _tail(logs_dir / name, _LOG_TAIL_LINES // 2)
            lines.extend(f"[{name[:-4]}] {ln.rstrip()}" for ln in tagged)
        body = "\n".join(lines[-_LOG_TAIL_LINES:]) or "no logs yet — is Kōan running?"
        # Logs carry raw ANSI and brackets ("[run]", "=== Run ===") that would
        # be mis-parsed as rich markup. Render via Text.from_ansi: it treats the
        # content as literal text and converts ANSI escapes into real styling.
        from rich.text import Text

        log_widget = self.query_one("#logs-body", RichLog)
        log_widget.clear()
        log_widget.write(Text.from_ansi(body))

    # --- config tree --------------------------------------------------------

    def _build_config_tree(self) -> None:
        try:
            tree = self.query_one("#config-tree", Tree)
        except Exception as exc:
            self.log(f"config tree build skipped: {exc}")
            return
        config = _load_config(self.koan_root)
        tree.clear()
        tree.root.expand()
        self._add_config_nodes(tree.root, config, prefix="")

    def _add_config_nodes(self, parent, mapping: dict, prefix: str) -> None:
        for key, value in mapping.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                branch = parent.add(f"[b]{key}[/b]", expand=False)
                self._add_config_nodes(branch, value, path)
            elif isinstance(value, list):
                branch = parent.add(f"[b]{key}[/b]  [dim]({len(value)} items)[/dim]",
                                    expand=False)
                for i, item in enumerate(value):
                    branch.add_leaf(f"[dim]- {item}[/dim]")
            elif isinstance(value, bool):
                # Show the current state only; enter/t flips it in place.
                shown = "on" if value else "off"
                color = _MINT if value else _MINT_DIM
                leaf = parent.add_leaf(f"{key}: [{color}][b]{shown}[/b][/]")
                leaf.data = {"path": path, "value": value}
            else:
                shown = self._format_scalar(value)
                leaf = parent.add_leaf(f"{key}: [{_MINT}]{shown}[/]")
                leaf.data = {"path": path, "value": value}

    @staticmethod
    def _format_scalar(value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        return str(value)

    def _render_config_status(self) -> None:
        try:
            status = self.query_one("#config-status", Static)
        except Exception as exc:
            self.log(f"config status widget missing: {exc}")
            return
        parts = ["[dim]enter / click a value to edit · r to reload[/dim]"]
        try:
            from app.config_validator import detect_config_drift, find_extra_config_keys

            missing = detect_config_drift(str(self.koan_root))
            extra = find_extra_config_keys(str(self.koan_root))
            if missing:
                parts.append(f"[{_MINT}]+ {len(missing)} new template keys[/] "
                             f"[dim]({', '.join(missing[:4])}…)[/dim]"
                             if len(missing) > 4
                             else f"[{_MINT}]+ {', '.join(missing)}[/]")
            if extra:
                parts.append(f"[{_AMBER}]~ {len(extra)} extra keys[/]")
            if not missing and not extra:
                parts.append("[dim]in sync with template[/dim]")
        except Exception as exc:
            parts.append(f"[dim](drift check unavailable: {exc})[/dim]")
        status.update("   ".join(parts))

    # --- usage --------------------------------------------------------------

    def _bar(self, label: str, pct: float, reset: str) -> str:
        pct = max(0.0, min(100.0, float(pct)))
        width = 30
        filled = int(round(pct / 100 * width))
        color = _MINT if pct < 70 else (_AMBER if pct < 90 else "red")
        bar = f"[{color}]{'█' * filled}[/][dim]{'░' * (width - filled)}[/]"
        return f"{label:<9} {bar}  [{color}]{pct:>3.0f}%[/]  [dim]reset in {reset}[/dim]"

    def _render_usage(self) -> None:
        usage_md = self.koan_root / "instance" / "usage.md"
        lines = []
        try:
            from app.usage_tracker import UsageTracker

            t = UsageTracker(usage_md)
            lines.append(self._bar("Session", t.session_pct, t.session_reset))
            lines.append(self._bar("Weekly", t.weekly_pct, t.weekly_reset))
            lines.append("")
            try:
                mode = t.decide_mode()
                lines.append(f"Mode      [{_MINT}]{mode}[/]")
            except Exception as exc:
                self.log(f"mode decision unavailable: {exc}")
            try:
                from app.burn_rate import burn_rate_pct_per_minute

                burn = burn_rate_pct_per_minute(usage_md.parent)
                if burn is not None:
                    lines.append(f"Burn      [{_MINT}]{burn:.2f}%/min[/]")
            except Exception as exc:
                self.log(f"burn rate unavailable: {exc}")
        except Exception as exc:
            lines.append(f"[dim](usage unavailable: {exc})[/dim]")
        if not (usage_md.exists()):
            lines.append("[dim]no usage.md yet — Kōan writes it after the first run[/dim]")
        self.query_one("#usage-body", Static).update("\n".join(lines))


def run(koan_root: Path) -> bool:
    """Launch the dashboard.

    Returns True if the user *detached* (closed the dashboard but left Kōan
    running), False if they quit and Kōan should be stopped.
    """
    app = KoanDashboard(Path(koan_root))
    app.run()
    return app._detached
