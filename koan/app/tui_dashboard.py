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

import logging
from collections import deque
from pathlib import Path

_log = logging.getLogger(__name__)

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
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
        ("q", "quit", "Quit"),
        ("1", "show('logs')", "Logs"),
        ("2", "show('config')", "Config"),
        ("3", "show('usage')", "Usage"),
        ("t", "toggle", "Toggle bool"),
        ("p", "pause", "Pause Kōan"),
        ("r", "refresh", "Refresh"),
    ]

    TITLE = "Kōan"

    def __init__(self, koan_root: Path):
        super().__init__()
        self.koan_root = Path(koan_root)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="logs"):
            with TabPane("Logs", id="logs"):
                yield Container(Static(id="logs-body", classes="pane"))
            with TabPane("Config", id="config"):
                yield Tree("config.yaml", id="config-tree")
                yield Static(id="config-status", classes="pane")
            with TabPane("Usage", id="usage"):
                yield Container(Static(id="usage-body", classes="pane"))
        yield Footer()

    def on_mount(self) -> None:
        self._build_config_tree()
        self.refresh_dynamic()
        self.set_interval(2.0, self.refresh_dynamic)

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
        self._render_logs()
        self._render_usage()
        self._render_config_status()
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        from app.pause_manager import is_paused

        state = "paused" if is_paused(str(self.koan_root)) else "live"
        self.sub_title = (f"{state} · 1/2/3 tabs · enter edits · t toggles bool"
                          f" · p pauses · q quits")

    def _render_logs(self) -> None:
        logs_dir = self.koan_root / "logs"
        lines = []
        for name in ("run.log", "awake.log"):
            tagged = _tail(logs_dir / name, _LOG_TAIL_LINES // 2)
            lines.extend(f"[{name[:-4]}] {ln.rstrip()}" for ln in tagged)
        body = "\n".join(lines[-_LOG_TAIL_LINES:]) or "no logs yet — is Kōan running?"
        self.query_one("#logs-body", Static).update(body)

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


def run(koan_root: Path) -> int:
    """Launch the dashboard. Returns a process exit code."""
    KoanDashboard(Path(koan_root)).run()
    return 0
