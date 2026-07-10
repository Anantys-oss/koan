"""Transition-only read helpers used while migrating readers off missions.md
(S4–S7). Removed at the S8 flip, when the store becomes authoritative and
readers no longer reconcile from the file.

``read_sections`` is a drop-in for ``app.missions.parse_sections(content)``: it
returns the same ``{state: [raw missions.md lines]}`` shape, sourcing the four
lifecycle states from the store (reconciled from the still-authoritative file)
and the ``## CI`` section straight from the file (CI migrates to its sibling
store in S6).
"""

from __future__ import annotations

from pathlib import Path

from app.mission_store.base import TERMINAL_STATES, VALID_STATES, render_mission_line
from app.mission_store.resolver import get_mission_store


def read_sections(instance) -> dict:
    """Return a ``parse_sections``-shaped dict from the store + file."""
    inst = Path(instance)
    if not inst.exists():
        return {**{s: [] for s in VALID_STATES}, "ci": []}
    p = inst / "missions.md"
    content = p.read_text() if p.exists() else ""

    store = get_mission_store(str(instance))
    store.reconcile_from_content(content)

    out: dict = {}
    for state in VALID_STATES:
        missions = store.list_by_state(state)
        if state in TERMINAL_STATES:
            missions = list(reversed(missions))  # back to file/sequence order
        out[state] = [render_mission_line(m) for m in missions]

    # CI (and Ideas) remain file-authoritative until S6, so pass their raw
    # section lines straight through (as parse_sections would) for readers that
    # need them (e.g. /brief's ci count).
    from app import missions as _m
    out["ci"] = _m.parse_sections(content).get("ci", [])
    return out


def reconcile_all(instance, content: str) -> None:
    """Rebuild the whole store (missions + CI + Ideas) from a ``missions.md``
    content string. The S8 write path uses this: render_content → transform →
    reconcile_all → export_view, so the store stays authoritative while every
    existing content transform keeps working unchanged.
    """
    from app.mission_store.aux_stores import CiQueueStore, IdeaStore
    inst = str(instance)
    get_mission_store(inst).reconcile_from_content(content)
    CiQueueStore(inst).reconcile_from_content(content)
    IdeaStore(inst).reconcile_from_content(content)


def read_content(instance) -> str:
    """The full missions.md content, for readers that need the raw text (e.g.
    ``group_by_project``, which parses all sections at once).

    Transition seam (S4–S7): returns the still-authoritative file. At the S8
    flip this renders the full content from the store instead — one swap point,
    so ``group_by_project`` callers change exactly once.
    """
    p = Path(instance) / "missions.md"
    return p.read_text() if p.exists() else ""
