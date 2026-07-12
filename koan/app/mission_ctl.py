"""Kōan mission-queue break-glass CLI — inspect and edit the mission store from
the terminal, out-of-band from the Telegram bridge.

Motivation: when the agent loop is stuck on a mission the bridge may stop
answering ``/list`` and ``/cancel``, so an operator needs a way to interrogate
and unstick the queue that does not depend on either long-running process. This
talks straight to the authoritative store (``instance/missions.db``) and applies
edits through the *same* flock-protected write chokepoint the daemons use
(``utils.modify_missions_file``), so the store and the ``missions.md`` export
stay consistent and it is safe to run while the daemons are alive.

Usage (run with ``KOAN_ROOT`` set — the ``make`` targets and the daemons do this)::

    python -m app.mission_ctl list [active|pending|in_progress|done|failed|all]
    python -m app.mission_ctl delete <selector>      # alias: rm

Selectors for ``delete``: ``i<N>`` (in-progress #N), ``p<N>`` (pending #N), or a
keyword substring of the mission text. Deleting a *pending* mission removes it
from the queue; deleting an *in-progress* mission aborts it (moves it to Failed)
so crash-recovery will not re-run it — the agent loop itself may still need a
restart if it is hung on that mission (this only edits the queue).

See ``docs/operations/mission-cli.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_LIVE = ("in_progress", "pending")
_LABELS = {
    "in_progress": "IN PROGRESS",
    "pending": "PENDING",
    "done": "DONE",
    "failed": "FAILED",
}
_PREFIX = {"in_progress": "i", "pending": "p", "done": "d", "failed": "f"}


def _instance_dir() -> Path:
    from app.utils import KOAN_ROOT

    return Path(KOAN_ROOT) / "instance"


def _store():
    """Return the authoritative mission store, syncing it once if needed."""
    from app.mission_store.resolver import get_mission_store
    from app.mission_store.transition import ensure_store_synced

    inst = str(_instance_dir())
    ensure_store_synced(inst)
    return get_mission_store(inst)


def _display(m) -> str:
    from app.missions import clean_mission_display

    return clean_mission_display(m.text, 100)


def _states_for(which: str) -> List[str]:
    if which == "active":
        return ["in_progress", "pending"]
    if which == "all":
        return ["in_progress", "pending", "done", "failed"]
    return [which]


def cmd_list(which: str) -> int:
    store = _store()
    blocks = []
    for st in _states_for(which):
        missions = store.list_by_state(st)
        lines = [f"{_LABELS[st]} ({len(missions)}):"]
        if not missions:
            lines.append("  (none)")
        for i, m in enumerate(missions, 1):
            lines.append(f"  {_PREFIX[st]}{i}\t{_display(m)}")
        blocks.append("\n".join(lines))
    print("\n\n".join(blocks))
    if which in ("active", "all"):
        print("\nDelete one with:  make mission-rm sel=i1   (sel=p2, or a keyword)")
    return 0


def _resolve(store, selector: str) -> Optional[Tuple[str, int, object]]:
    """Resolve a selector to (state, index, Mission), or None if no match.

    Positional selectors are ``p<N>``/``i<N>`` (1-indexed within the section);
    anything else is a case-insensitive keyword matched against mission text,
    searching in-progress first (the usual break-glass target), then pending.
    """
    selector = selector.strip()
    if len(selector) >= 2 and selector[0] in ("p", "i") and selector[1:].isdigit():
        state = "pending" if selector[0] == "p" else "in_progress"
        idx = int(selector[1:]) - 1
        missions = store.list_by_state(state)
        if 0 <= idx < len(missions):
            return state, idx, missions[idx]
        return None

    kw = selector.lower()
    for state in _LIVE:
        for idx, m in enumerate(store.list_by_state(state)):
            if kw in m.text.lower():
                return state, idx, m
    return None


def cmd_delete(selector: str) -> int:
    store = _store()
    target = _resolve(store, selector)
    if target is None:
        print(
            f"✗ No active mission matches {selector!r}. "
            f"Run 'make missions' to see the current queue.",
            file=sys.stderr,
        )
        return 1

    state, idx, m = target
    from app import missions as _m
    from app.utils import modify_missions_file

    missions_path = _instance_dir() / "missions.md"
    result = {"ok": False}

    if state == "pending":
        def _transform(content):
            try:
                updated, _cancelled = _m.cancel_pending_mission(content, str(idx + 1))
                result["ok"] = True
                return updated
            except ValueError as e:
                # Capture the cause (bad selector, empty section, …) so the failure
                # message below explains WHY the break-glass delete failed, instead
                # of swallowing it behind a generic error during an incident.
                result["error"] = str(e)
                return content

        done_verb = "Removed pending mission"
    else:  # in_progress
        def _transform(content):
            updated = _m.fail_mission(content, m.text, cause_tag="aborted")
            result["ok"] = updated != content
            return updated

        done_verb = "Aborted in-progress mission (moved to Failed)"

    modify_missions_file(missions_path, _transform)

    if not result["ok"]:
        if m.text.lstrip().startswith("### "):
            hint = " — complex ### missions cannot be removed this way"
        elif result.get("error"):
            hint = f" — {result['error']}"
        else:
            hint = ""
        print(f"✗ Could not remove mission {selector!r}{hint}.", file=sys.stderr)
        return 1

    print(f"✓ {done_verb}: {_display(m)}")
    if state == "in_progress":
        print(
            "  If the agent loop is hung on this mission, also restart it "
            "(`make stop && make start`) — this only edits the queue."
        )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="koan-missions",
        description=(
            "Break-glass CLI to inspect and edit the Kōan mission queue (SQLite "
            "store) directly, for when the Telegram bridge is unresponsive."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List missions by state (default: active)")
    p_list.add_argument(
        "state",
        nargs="?",
        default="active",
        choices=["active", "pending", "in_progress", "done", "failed", "all"],
        help="Which section(s) to show (active = in-progress + pending).",
    )

    p_del = sub.add_parser(
        "delete",
        aliases=["rm"],
        help="Remove a mission by selector i<N> / p<N> or a keyword.",
    )
    p_del.add_argument("selector", help="i<N> (in-progress), p<N> (pending), or keyword")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args.state)
    if args.cmd in ("delete", "rm"):
        return cmd_delete(args.selector)
    parser.error("unknown command")  # unreachable — subparser is required
    return 2


if __name__ == "__main__":
    sys.exit(main())
