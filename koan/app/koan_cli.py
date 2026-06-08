#!/usr/bin/env python3
"""Kōan — interactive launcher (``make koan``).

A TTY-gated front door for starting Kōan. In a terminal it clears the screen,
starts the stack (agent + bridge), and drops straight into the terminal
dashboard — no mode prompt. The dashboard's Status tab is the home screen
(hero banner + live status flags + toggles for the web dashboard and
caffeinate). Quitting the dashboard (``q``) tears the stack down cleanly.

``make start`` is intentionally left untouched for backward compatibility
(services, CI, scripts). When stdin is not a TTY this launcher delegates to
the existing headless ``start_all`` path with no prompt, so it is safe to
call from non-interactive contexts too.
"""

import argparse
import sys
from pathlib import Path

from app.banners.theme import amber, mint, muted, text


def _clear_screen() -> None:
    """Clear the terminal and scrollback so the UI starts on a clean slate."""
    # \033[3J clears scrollback, \033[2J clears the screen, \033[H homes cursor.
    sys.stdout.write("\033[3J\033[2J\033[H")
    sys.stdout.flush()


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


def run(koan_root: Path) -> int:
    """Start Kōan and open the terminal dashboard. Returns a process exit code."""
    interactive = hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
    if not interactive:
        # Non-TTY (service, CI, pipe): behave exactly like `make start`.
        from app.pid_manager import start_all

        start_all(koan_root)
        return 0

    _clear_screen()
    from app.pid_manager import start_all

    results = start_all(koan_root, show_banner=False)
    failed = [name for name, (ok, msg) in results.items()
              if not ok and "already running" not in msg.lower()]
    if failed:
        print(f"  {amber('some components did not start:')} {text(', '.join(failed))}")

    try:
        from app.tui_dashboard import run as run_tui
    except ImportError:
        # textual not installed — show the hero, keep Kōan running, point at logs.
        from app.banners import print_hero_banner

        print_hero_banner()
        print(f"  {amber('terminal dashboard unavailable')} "
              f"{muted('(install textual: pip install textual)')}")
        print(f"  {mint('Kōan is running.')} {muted('make logs / make stop')}")
        return 0

    import contextlib

    detached = False
    with contextlib.suppress(KeyboardInterrupt):
        detached = run_tui(koan_root)
    if detached:
        # User pressed `d`: keep Kōan running in the background.
        print(f"  {mint('Kōan still running.')} {muted('make logs / make stop')}")
    else:
        # Quitting (q) ends the session and stops Kōan.
        _stop_stack(koan_root)
    return 0


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
