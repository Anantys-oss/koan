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
import time
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
    """Return the last ``limit`` lines of a file, or [] if absent.

    For files larger than 64 KiB we seek near the end and read only the
    trailing chunk, avoiding reading the whole file into memory.
    """
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        if size < 65_536:
            with path.open("r", errors="replace") as fh:
                return list(deque(fh, maxlen=limit))
        # Large file: seek back in expanding blocks until enough lines found.
        chunk = min(limit * 128, size)
        with path.open("r", errors="replace") as fh:
            while True:
                fh.seek(max(0, size - chunk))
                if size > chunk:
                    fh.readline()
                lines = list(deque(fh, maxlen=limit))
                if len(lines) >= limit or chunk >= size:
                    return lines
                chunk = min(chunk * 2, size)
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


def _provider_name() -> str:
    """Return the configured CLI provider name, or 'unknown'."""
    try:
        from app.cli_provider import get_provider_name
        return get_provider_name()
    except Exception as exc:
        _log.debug("provider_name failed: %s", exc)
        return "unknown"


def _provider_has_api_quota() -> bool:
    """Return True when the active provider consumes metered API quota."""
    try:
        from app.cli_provider import get_provider
        return get_provider().has_api_quota()
    except Exception as exc:
        _log.warning("provider_has_api_quota failed: %s", exc)
        return True


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


def _set_nested_key(data: dict, dotted_key: str, value) -> None:
    """Set a nested key in a dict, creating intermediate dicts as needed."""
    keys = dotted_key.split(".")
    node = data
    for k in keys[:-1]:
        node = node.setdefault(k, {})
    node[keys[-1]] = value


def set_config_value(koan_root: Path, dotted_key: str, value) -> None:
    """Set a nested key in instance/config.yaml, preserving comments.

    Uses ruamel.yaml to round-trip the file so user comments and formatting
    survive the edit; falls back to pyyaml when ruamel is unavailable.
    """
    from app.utils import atomic_write

    path = Path(koan_root) / "instance" / "config.yaml"

    try:
        import io

        from ruamel.yaml import YAML

        ry = YAML()
        ry.preserve_quotes = True
        data = ry.load(path.read_text()) if path.exists() else {}
        if data is None:
            data = {}
        _set_nested_key(data, dotted_key, value)
        stream = io.StringIO()
        ry.dump(data, stream)
        atomic_write(path, stream.getvalue())
        return
    except ImportError:
        pass

    import yaml

    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    _set_nested_key(data, dotted_key, value)
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


class ResetQuotaScreen(ModalScreen):
    """Modal to override or fully reset quota estimates."""

    CSS = f"""
    ResetQuotaScreen {{ align: center middle; }}
    #box {{ width: 72; height: auto; padding: 1 2;
            background: {_MIDNIGHT}; border: round {_AMBER}; }}
    #title {{ color: {_AMBER}; text-style: bold; }}
    #current {{ color: $text; }}
    #hint {{ color: $text-muted; }}
    #buttons {{ height: auto; padding-top: 1; }}
    Button {{ margin-right: 2; }}
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, session_pct: float, weekly_pct: float):
        super().__init__()
        self.session_pct = session_pct
        self.weekly_pct = weekly_pct

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Label("Reset / override quota", id="title")
            yield Label(
                f"Session: {self.session_pct:.0f}%  ·  Weekly: {self.weekly_pct:.0f}%",
                id="current",
            )
            yield Label(
                "Enter % used (0-100) to override, or choose Full Reset",
                id="hint",
            )
            yield Input(placeholder="e.g. 5 (= 5% used, 95% remaining)", id="value")
            with Container(id="buttons"):
                yield Button("Override", variant="primary", id="override")
                yield Button("Full Reset", variant="warning", id="reset")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "override":
            self._submit()
        elif event.button.id == "reset":
            self.dismiss("reset")
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        raw = self.query_one("#value", Input).value.strip()
        if not raw:
            self.dismiss(None)
            return
        try:
            pct = int(raw)
        except ValueError:
            self.dismiss("invalid")
            return
        if pct < 0 or pct > 100:
            self.dismiss("invalid")
            return
        self.dismiss(pct)


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

    # Tabs are switched by their underlined first letter (BIOS-style) or 1-4;
    # those bindings are hidden from the footer to keep it focused on actions.
    BINDINGS = [
        ("q", "request_quit", "Quit (stop)"),
        ("d", "detach", "Detach (keep running)"),
        ("m", "new_mission", "New mission"),
        ("w", "toggle_web", "Web dashboard"),
        ("k", "toggle_keepawake", "Keep awake"),
        ("p", "pause", "Pause"),
        Binding("1", "show('status')", "Status", show=False),
        Binding("2", "show('logs')", "Logs", show=False),
        Binding("3", "show('usage')", "Usage", show=False),
        Binding("4", "show('config')", "Config", show=False),
        Binding("s", "show('status')", "Status", show=False, priority=True),
        Binding("l", "show('logs')", "Logs", show=False, priority=True),
        Binding("u", "show('usage')", "Usage", show=False, priority=True),
        Binding("c", "show('config')", "Config", show=False, priority=True),
        Binding("up", "focus_up", "Focus up", show=False, priority=True),
        Binding("down", "focus_pane", "Focus pane", show=False),
        Binding("escape", "focus_tabs", "Focus tabs", show=False),
        Binding("t", "toggle", "Toggle bool", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    # Disable the built-in command palette (^p) — wasted real estate here.
    ENABLE_COMMAND_PALETTE = False

    TITLE = "Kōan"

    def __init__(self, koan_root: Path):
        super().__init__()
        self.koan_root = Path(koan_root)
        # Keep-awake subprocess handle (caffeinate / systemd-inhibit); on by default.
        self._keepawake = None
        self._keepawake_label = ""
        # Detach flag: True when the user closed the dashboard but left Kōan up.
        self._detached = False
        # Monotonic timestamp of last CTRL-C interrupt for double-tap quit.
        self._last_interrupt_at = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="status"):
            with TabPane("[u]S[/]tatus", id="status"):
                yield Container(Static(id="status-body", classes="pane"))
            with TabPane("[u]L[/]ogs", id="logs"):
                yield RichLog(id="logs-body", classes="pane", markup=False, auto_scroll=True)
            with TabPane("[u]U[/]sage", id="usage"):
                yield Container(Static(id="usage-body", classes="pane"))
            with TabPane("[u]C[/]onfig", id="config"):
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
        # Keep focus on the tab bar after any switch so Left/Right navigate
        # tabs and letter shortcuts are never trapped by pane widgets.
        try:
            self.query_one(Tabs).focus()
        except Exception as exc:
            self.log(f"tab focus failed: {exc}")

    def _focus_config_tree(self) -> None:
        try:
            self.query_one("#config-tree", Tree).focus()
        except Exception as exc:
            self.log(f"could not focus config tree: {exc}")

    # --- actions ------------------------------------------------------------

    def action_refresh(self) -> None:
        if self.active_pane_id() == "usage":
            self.action_reset_quota()
            return
        self._build_config_tree()
        self.refresh_dynamic()

    def action_focus_up(self) -> None:
        """Up arrow: navigate the config tree upward, or return to tabs at root."""
        try:
            tree = self.query_one("#config-tree", Tree)
            if tree.has_focus:
                cursor = getattr(tree, "cursor_node", None)
                if cursor is not None and cursor != tree.root:
                    tree.action_cursor_up()
                    return
        except Exception as exc:
            self.log(f"focus up tree check failed: {exc}")
        self.action_focus_tabs()

    def action_focus_tabs(self) -> None:
        """Move keyboard focus to the tab bar (Escape)."""
        try:
            self.query_one(Tabs).focus()
        except Exception as exc:
            self.log(f"focus tabs failed: {exc}")

    def action_focus_pane(self) -> None:
        """Move focus from the tab bar into the active pane (Down)."""
        if self.active_pane_id() == "config":
            self._focus_config_tree()

    def action_show(self, pane: str) -> None:
        """Switch tabs via 1/2/3/4 or s/l/u/c."""
        try:
            self.query_one(TabbedContent).active = pane
        except Exception as exc:
            self.log(f"tab switch failed: {exc}")
            return
        # Always leave focus on the tab bar so Left/Right navigate tabs
        # and letter shortcuts are never trapped by pane widgets.
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

    def action_reset_quota(self) -> None:
        """Open quota reset modal when on the Usage tab."""
        try:
            from app.usage_tracker import UsageTracker

            usage_md = self.koan_root / "instance" / "usage.md"
            t = UsageTracker(usage_md)
        except Exception as exc:
            self.notify(f"quota read failed: {exc}", severity="error")
            return

        def _apply(result) -> None:
            if result is None:
                return
            if result == "invalid":
                self.notify("Invalid input — enter a number between 0 and 100")
                return
            instance_dir = self.koan_root / "instance"
            state_file = instance_dir / "usage_state.json"
            usage_md = instance_dir / "usage.md"
            try:
                if result == "reset":
                    from app.usage_estimator import cmd_reset_session
                    cmd_reset_session(state_file, usage_md)
                    # Clear burn-rate history
                    burn_rate_file = instance_dir / ".burn-rate.json"
                    if burn_rate_file.exists():
                        burn_rate_file.unlink(missing_ok=True)
                    msg = "Quota reset — session tokens cleared, burn rate reset."
                else:
                    from app.usage_estimator import cmd_set_used
                    cmd_set_used(result, state_file, usage_md)
                    msg = f"Quota override — set to {result}% used ({100 - result}% remaining)."

                # Clear quota pause if active
                from app.pause_manager import is_paused, get_pause_state, remove_pause
                if is_paused(str(self.koan_root)):
                    state = get_pause_state(str(self.koan_root))
                    if state and state.is_quota:
                        remove_pause(str(self.koan_root))
                        msg += "  Quota pause cleared — agent will resume."

                self.notify(msg)
            except Exception as exc:
                self.notify(f"quota update failed: {exc}", severity="error")
            self.refresh_dynamic()

        self.push_screen(
            ResetQuotaScreen(t.session_pct, t.weekly_pct), _apply
        )

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

    _INTERRUPT_WINDOW = 3.0  # seconds to confirm a double CTRL-C

    def action_help_quit(self) -> None:
        """Double CTRL-C to stop: first press notifies, second confirms."""
        now = time.monotonic()
        if now - self._last_interrupt_at < self._INTERRUPT_WINDOW:
            self._detached = False
            self.exit()
            return
        self._last_interrupt_at = now
        self.notify(
            "Press [b]Ctrl-C[/b] again to stop",
            title="Stop Kōan?",
            timeout=self._INTERRUPT_WINDOW,
        )

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

        parts = ["This stops the agent + bridge. Use d to detach and keep it running."]
        active = self._active_processes()
        if active:
            parts.append(f"\nActive processes: {', '.join(active)}")
        titles = self._in_progress_missions()
        if titles:
            parts.append(f"\nIn progress ({len(titles)}):")
            parts.extend(f"  · {t}" for t in titles[:5])
            if len(titles) > 5:
                parts.append(f"  … +{len(titles) - 5} more")

        self.push_screen(ConfirmScreen("Stop Kōan?", "\n".join(parts)), _confirmed)

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

        # Keep the subtitle minimal — the footer already lists the shortcuts.
        self.sub_title = "paused" if is_paused(str(self.koan_root)) else "live"

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

    def _run_status(self) -> bool:
        """Return whether the agent run loop is alive."""
        try:
            from app.pid_manager import check_pidfile

            return check_pidfile(self.koan_root, "run") is not None
        except Exception as exc:
            self.log(f"run status failed: {exc}")
            return False

    def _api_status(self) -> bool:
        """Return whether the REST API server is alive."""
        try:
            from app.pid_manager import check_pidfile

            return check_pidfile(self.koan_root, "api") is not None
        except Exception as exc:
            self.log(f"api status failed: {exc}")
            return False

    def _active_processes(self) -> list:
        """Return names of active Kōan processes (excluding the dashboard itself)."""
        try:
            from app.pid_manager import PROCESS_NAMES, check_pidfile

            return [
                name
                for name in PROCESS_NAMES
                if name != "dashboard" and check_pidfile(self.koan_root, name) is not None
            ]
        except Exception as exc:
            self.log(f"process list failed: {exc}")
            return []

    def _render_status(self) -> None:
        try:
            body = self.query_one("#status-body", Static)
        except Exception as exc:
            self.log(f"status widget missing: {exc}")
            return

        from rich.markup import escape
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
        try:
            from app.missions import count_pending

            md = self.koan_root / "instance" / "missions.md"
            pending_count = count_pending(md.read_text()) if md.exists() else 0
        except Exception as exc:
            self.log(f"pending count failed: {exc}")
            pending_count = 0
        web_on = self._web_running()
        bridge, tg_configured = self._telegram_status()

        web_hint = "localhost:5001" if web_on else "start + open browser"
        awake_on = self._keepawake_on()
        awake_hint = self._keepawake_label if awake_on else "off"
        run_on = self._run_status()
        api_on = self._api_status()
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
        # Escape titles — mission text like "[project:koan]" would otherwise
        # be parsed as rich markup tags and crash the renderer.
        lines.extend(f"                 [dim]·[/] {escape(t)}" for t in titles[:3])
        if len(titles) > 3:
            lines.append(f"                 [dim]… +{len(titles) - 3} more[/]")
        if pending_count:
            lines.append(f"  pending      [{_MINT}]{pending_count}[/] in queue")
        else:
            lines.append("  pending      [dim]empty queue[/]")
        lines += [
            f"  telegram     {tg}",
            f"  web board    {self._dot(web_on)}  [dim](w · {web_hint})[/]",
            f"  keep awake   {self._dot(awake_on)}  [dim](k · {awake_hint})[/]",
            f"  run loop     {self._dot(run_on)}  [dim]{'running' if run_on else 'stopped'}[/]",
            f"  api          {self._dot(api_on)}  [dim]{'live' if api_on else 'off'}[/]",
        ]
        # Provider + models (placed before usage so the operator sees *what* is
        # driving consumption before the quota bars).
        try:
            from app.config import get_cli_provider_name, get_model_config

            provider = get_cli_provider_name()
            models = get_model_config()
            if provider:
                lines.append("")
                lines.append(f"  provider     [{_MINT}]{provider}[/]")
                model_parts = []
                for role in ("mission", "chat", "lightweight", "fallback", "review_mode", "reflect"):
                    val = models.get(role, "")
                    if val:
                        label = role.replace("_", " ")
                        model_parts.append(f"{label}: {val}")
                if model_parts:
                    lines.append(f"  models       [dim]{' · '.join(model_parts)}[/dim]")
        except Exception as exc:
            self.log(f"provider/models display failed: {exc}")
        # Usage bars reuse the same renderer as the Usage tab.
        try:
            from app.usage_tracker import UsageTracker

            usage_md = self.koan_root / "instance" / "usage.md"
            t = UsageTracker(usage_md)
            lines.append("")
            if _provider_has_api_quota():
                lines.append("  " + self._bar("session", t.session_pct, t.session_reset))
                lines.append("  " + self._bar("weekly", t.weekly_pct, t.weekly_reset))
            else:
                lines.append("  [dim]session    no API quota (provider: " + _provider_name() + ")[/]")
                lines.append("  [dim]weekly     no API quota[/]")
        except Exception as exc:
            self.log(f"status usage failed: {exc}")

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
        has_data = False
        try:
            from app.usage_tracker import UsageTracker

            t = UsageTracker(usage_md)
            if _provider_has_api_quota():
                lines.append(self._bar("Session", t.session_pct, t.session_reset))
                lines.append(self._bar("Weekly", t.weekly_pct, t.weekly_reset))
                lines.append("")
                has_data = True
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
                try:
                    from app.session_tracker import load_outcomes

                    outcomes_path = self.koan_root / "instance" / "session_outcomes.json"
                    outcomes = load_outcomes(outcomes_path)
                    if outcomes:
                        last = outcomes[-1]
                        la = last.get("last_action", "")
                        dur = last.get("duration_minutes")
                        if la:
                            lines.append(f"Last      [{_MINT}]{la}[/]")
                        if dur is not None:
                            lines.append(f"Duration  [{_MINT}]{dur} min[/]")
                except ImportError as exc:
                    self.log(f"session_tracker unavailable: {exc}")
                except Exception as exc:
                    logging.exception("last session info failed")
                    self.log(f"last session info unavailable: {exc}")
            else:
                lines.append("[dim]Session    no API quota[/]")
                lines.append("[dim]Weekly     no API quota[/]")
                lines.append("")
                try:
                    mode = t.decide_mode()
                    lines.append(f"Mode      [{_MINT}]{mode}[/]  [dim](budget disabled for {_provider_name()})[/]")
                except Exception as exc:
                    self.log(f"mode decision unavailable: {exc}")
                    lines.append(f"Mode      [{_MINT}]deep[/]  [dim](budget disabled for {_provider_name()})[/]")
        except Exception as exc:
            logging.exception("usage rendering failed")
            lines.append(f"[dim](usage unavailable: {exc})[/dim]")
        if not (usage_md.exists()):
            lines.append("[dim]no usage.md yet — Kōan writes it after the first run[/dim]")
        if has_data and usage_md.exists():
            lines.append("")
            lines.append("[dim]r = reset / override quota[/dim]")
        self.query_one("#usage-body", Static).update("\n".join(lines))


def run(koan_root: Path) -> bool:
    """Launch the dashboard.

    Returns True if the user *detached* (closed the dashboard but left Kōan
    running), False if they quit and Kōan should be stopped.
    """
    app = KoanDashboard(Path(koan_root))
    app.run()
    return app._detached
