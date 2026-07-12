"""Read/sync helpers bridging ``missions.md`` and the mission store.

At S8 the store is authoritative and ``missions.md`` is a generated read-only
export. ``ensure_store_synced`` performs the one-time cutover sync (populate the
store from the file, once, gated by a persisted marker); thereafter readers read
the store directly and writers round-trip through it. ``read_sections`` /
``read_content`` are the shims the migrated readers call.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.mission_store.base import TERMINAL_STATES, VALID_STATES, render_mission_line
from app.mission_store.resolver import get_mission_store

logger = logging.getLogger(__name__)


def reconcile_all(instance, content: str) -> None:
    """Rebuild the whole store (missions + CI + Ideas) from a ``missions.md``
    content string. The S8 write path uses this: render_content → transform →
    reconcile_all → export_view, so the store stays authoritative while every
    existing content transform keeps working unchanged.
    """
    from app.mission_store.aux_stores import CiQueueStore, IdeaStore
    inst = str(instance)
    report = get_mission_store(inst).reconcile_from_content(content)
    # reconcile_from_content DELETE+re-INSERTs from the text form; any line it
    # cannot re-parse is dropped from the authoritative store while the caller's
    # new_content still carries it (persisted state and return value diverge). Log
    # loudly so that lossy round-trip is visible, not a silent no-op deletion. (We
    # log rather than raise: this runs in the per-write chokepoint, so raising would
    # brick every mission mutation on a single malformed line.)
    if report is not None and getattr(report, "unparseable", None):
        logger.error(
            "[mission_store] reconcile dropped %d unparseable mission line(s) on "
            "the store round-trip: %s",
            len(report.unparseable),
            " | ".join(s.replace("\n", " ")[:80] for s in report.unparseable[:5]),
        )
    CiQueueStore(inst).reconcile_from_content(content)
    IdeaStore(inst).reconcile_from_content(content)


def ensure_store_synced(instance) -> None:
    """Populate the store from ``missions.md`` once — the S8 cutover sync.

    Gated by the store's persisted ``s8_synced`` marker: the first boot (or, in
    tests, the first read/write against a fresh store) rebuilds missions + CI +
    Ideas from the current file and marks the store synced; thereafter the store
    is authoritative and the file is a read-only export. Cheap after the first
    call (one indexed marker check).
    """
    store = get_mission_store(str(instance))
    if store.is_synced():
        return
    p = Path(instance) / "missions.md"
    content = p.read_text() if p.exists() else ""
    reconcile_all(str(instance), content)
    store.mark_synced()


def read_sections(instance) -> dict:
    """Return a ``parse_sections``-shaped ``{state: [raw lines]}`` dict, read
    directly from the (authoritative) store."""
    inst = Path(instance)
    if not inst.exists():
        return {**{s: [] for s in VALID_STATES}, "ci": []}
    ensure_store_synced(str(inst))
    store = get_mission_store(str(inst))

    out: dict = {}
    for state in VALID_STATES:
        missions = store.list_by_state(state)
        if state in TERMINAL_STATES:
            missions = list(reversed(missions))  # oldest-first, as parse_sections
        out[state] = [render_mission_line(m) for m in missions]

    from app.mission_store.aux_stores import CiQueueStore
    out["ci"] = CiQueueStore(str(inst)).render_lines()
    return out


def read_content(instance) -> str:
    """The full ``missions.md`` content, rendered from the (authoritative) store."""
    inst = Path(instance)
    if not inst.exists():
        return ""
    ensure_store_synced(str(inst))
    return get_mission_store(str(inst)).render_content()
