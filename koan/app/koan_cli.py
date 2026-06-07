#!/usr/bin/env python3
"""Kōan — interactive launcher (``make koan``).

A TTY-gated, themed front door for starting Kōan. It renders the Anantys
banner, surfaces any config drift (read-only — never modifies the user's
config), then lets the human choose how to supervise the agent:

    - web       start the stack + web dashboard, open the browser
    - terminal  start the stack + the terminal dashboard (textual)
    - headless  start the stack and hand control back (same as ``make start``)

``make start`` is intentionally left untouched for backward compatibility
(services, CI, scripts). When stdin is not a TTY this launcher delegates to
the existing headless ``start_all`` path with no prompt, so it is safe to
call from non-interactive contexts too.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from app.banners.theme import amber, mint, mint_dim, muted, pixel_gradient_lines, text

MODES = ("web", "terminal", "headless")
DASHBOARD_URL = "http://localhost:5001"


def _render_banner(koan_root: Path) -> None:
    from app.banners import print_startup_banner
    from app.pid_manager import _detect_provider
    from app.startup_info import gather_startup_info

    print()
    for line in pixel_gradient_lines(width=64, height=1):
        print(f"  {line}")
    try:
        info = gather_startup_info(koan_root)
        info["provider"] = _detect_provider(koan_root)
    except Exception as exc:
        print(f"  (banner info unavailable: {exc})", file=sys.stderr)
        info = None
    print_startup_banner(info)


def _render_drift(koan_root: Path) -> None:
    """Surface config drift before the menu — display only, never apply."""
    try:
        from app.config_validator import detect_config_drift, find_extra_config_keys

        missing = detect_config_drift(str(koan_root))
        extra = find_extra_config_keys(str(koan_root))
    except Exception as exc:
        print(f"  (drift check skipped: {exc})", file=sys.stderr)
        return

    if not missing and not extra:
        return

    print(f"  {mint_dim('config drift')} {muted('— instance.example/config.yaml has changed:')}")
    for key in missing:
        print(f"    {mint('+')}  {text(key)}")
    for key in extra:
        print(f"    {amber('~')}  {text(key)} {muted('(extra — maybe deprecated)')}")
    print(f"    {muted('->')}  {muted('run /config_check for details')}")
    print()


def _render_options(options, idx: int, first: bool) -> None:
    """(Re)draw the option list in place, highlighting the active row."""
    if not first:
        # Move the cursor back up over the previously drawn rows.
        sys.stdout.write(f"\033[{len(options)}A")
    for i, (label, desc) in enumerate(options):
        sys.stdout.write("\033[2K")  # clear the whole line
        if i == idx:
            pointer = mint("›", bold=True)
            row = f"  {pointer} {mint(label, bold=True)}   {text(desc)}"
        else:
            row = f"    {muted(label)}   {muted(desc)}"
        sys.stdout.write(row + "\n")
    sys.stdout.flush()


def _read_key() -> str:
    """Read one logical keypress, decoding arrow escape sequences."""
    ch = sys.stdin.read(1)
    if ch == "\x1b":  # ESC — possibly an arrow sequence
        seq = sys.stdin.read(2)
        if seq == "[A":
            return "up"
        if seq == "[B":
            return "down"
        return "esc"
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03":  # Ctrl-C
        raise KeyboardInterrupt
    return ch


def _arrow_select(options, default: int = 0) -> int:
    """Arrow-key driven selector (↑/↓ + Enter, digits as shortcuts).

    Falls back to a numbered prompt when stdin is not a TTY or the platform
    lacks termios (e.g. Windows). Returns the selected index, or the default
    on cancel.
    """
    try:
        import termios
        import tty
    except ImportError:
        return _numbered_select(options, default)

    if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
        return _numbered_select(options, default)

    idx = default
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    sys.stdout.write("\033[?25l")  # hide cursor
    try:
        _render_options(options, idx, first=True)
        tty.setraw(fd)
        while True:
            key = _read_key()
            if key == "up":
                idx = (idx - 1) % len(options)
            elif key == "down":
                idx = (idx + 1) % len(options)
            elif key.isdigit() and 1 <= int(key) <= len(options):
                idx = int(key) - 1
                break
            elif key == "enter":
                break
            elif key in ("q", "esc"):
                raise KeyboardInterrupt
            else:
                continue
            # Restore cooked mode just for the redraw, then back to raw.
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            _render_options(options, idx, first=False)
            tty.setraw(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h")  # show cursor
        # Final repaint in cooked mode so the chosen row stays highlighted.
        _render_options(options, idx, first=False)
    return idx


def _numbered_select(options, default: int = 0) -> int:
    """Plain numbered fallback for non-TTY / no-termios environments."""
    from app import onboarding

    labels = [f"{label}   {desc}" for label, desc in options]
    return onboarding.ask_choice("Choice", labels, default=default)


def _choose_mode(koan_root: Path) -> str:
    options = [
        ("Web dashboard", "opens the browser (localhost:5001)"),
        ("Terminal view", "tabs: logs / config / usage"),
        ("Headless", "starts and hands control back"),
    ]
    print(f"  {text('How do you want to follow Kōan?', bold=True)}")
    print(f"  {muted('↑/↓ to move · enter to confirm · q to quit')}")
    print()
    idx = _arrow_select(options, default=0)
    print()
    return MODES[idx]


def _start_stack(koan_root: Path) -> dict:
    from app.pid_manager import start_all

    # Banner already rendered by _render_banner — suppress the duplicate.
    return start_all(koan_root, show_banner=False)


def _stop_stack(koan_root: Path) -> None:
    """Tear down the whole stack cleanly (equivalent to `make stop`)."""
    print()
    print(f"  {muted('stopping Kōan…')}")
    try:
        from app.pid_manager import stop_processes

        stop_processes(koan_root)
        print(f"  {mint('Kōan stopped.')}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  {amber('stop failed:')} {text(str(exc))} {muted('— try `make stop`')}")


def _wait_until_interrupt(koan_root: Path) -> None:
    """Block in the foreground until the user hits Ctrl-C."""
    import contextlib
    import time

    print(f"  {muted('Kōan is running — Ctrl-C to stop the session')}")
    with contextlib.suppress(KeyboardInterrupt):
        while True:
            time.sleep(1)


def _launch_web(koan_root: Path) -> int:
    from app.pid_manager import start_dashboard

    results = _start_stack(koan_root)
    failed = [name for name, (ok, _) in results.items() if not ok]
    if failed:
        print(f"  {amber('some components did not start:')} {text(', '.join(failed))}")
    ok, msg = start_dashboard(koan_root)
    print(f"  {mint('dashboard') if ok else amber('dashboard')} {muted(msg)}")
    try:
        webbrowser.open(DASHBOARD_URL)
        print(f"  {muted('opened')} {text(DASHBOARD_URL)}")
    except Exception:
        print(f"  {muted('open')} {text(DASHBOARD_URL)} {muted('in your browser')}")
    # Foreground session: Ctrl-C tears the stack down cleanly.
    _wait_until_interrupt(koan_root)
    _stop_stack(koan_root)
    return 0


def _launch_terminal(koan_root: Path) -> int:
    results = _start_stack(koan_root)
    failed = [name for name, (ok, _) in results.items() if not ok]
    if failed:
        print(f"  {amber('some components did not start:')} {text(', '.join(failed))}")
    try:
        from app.tui_dashboard import run as run_tui
    except (ImportError, ModuleNotFoundError):
        print(f"  {amber('terminal dashboard unavailable')} "
              f"{muted('(install textual: pip install textual) — falling back to logs')}")
        print(f"  {muted('run')} {text('make logs')} {muted('to follow output')}")
        _wait_until_interrupt(koan_root)
        _stop_stack(koan_root)
        return 0
    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        run_tui(koan_root)
    # Quitting the dashboard (q) ends the session and stops Kōan.
    _stop_stack(koan_root)
    return 0


def _launch_headless(koan_root: Path) -> int:
    results = _start_stack(koan_root)
    failed = [name for name, (ok, _) in results.items() if not ok]
    if failed:
        print(f"  {amber('some components did not start:')} {text(', '.join(failed))}")
    else:
        print(f"  {mint('Kōan is running.')} {muted('make status / make logs / make stop')}")
    return 0


def run(koan_root: Path) -> int:
    """Interactive launch flow. Returns a process exit code."""
    interactive = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if not interactive:
        # Non-TTY (service, CI, pipe): behave exactly like `make start`,
        # banner included.
        from app.pid_manager import start_all

        start_all(koan_root)
        return 0

    _render_banner(koan_root)
    _render_drift(koan_root)
    try:
        mode = _choose_mode(koan_root)
    except KeyboardInterrupt:
        print()
        print(f"  {muted('cancelled — nothing started')}")
        return 0

    if mode == "web":
        return _launch_web(koan_root)
    if mode == "terminal":
        return _launch_terminal(koan_root)
    return _launch_headless(koan_root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="koan", description="Kōan interactive launcher")
    parser.add_argument("koan_root", nargs="?", default=None,
                        help="Path to the Kōan root (defaults to KOAN_ROOT or cwd)")
    args = parser.parse_args(argv)

    import os
    root = args.koan_root or os.environ.get("KOAN_ROOT") or os.getcwd()
    return run(Path(root))


if __name__ == "__main__":
    sys.exit(main())
