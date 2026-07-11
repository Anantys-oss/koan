"""One-time boot ingest: populate the store from ``missions.md`` (and the
quarantine file) the first time a database backend comes up.

Wired into ``startup_manager.run_startup`` as the ``Mission store ingest`` step —
the analog of ``index_memory_sqlite`` for the memory DB. Idempotent: gated on
``MissionStore.is_initialized()`` so it runs exactly once, after crash recovery
and pruning have stabilized ``missions.md``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app.mission_store.base import IngestReport

logger = logging.getLogger(__name__)

_Q_RE = re.compile(r"^-\s*\U0001f6e1️?\s*\[(?P<ts>[^\]]*)\]\s*"
                   r"\((?P<src>[^)]*)\)\s*(?P<reason>[^:]*):\s*(?P<text>.*)$")


def ensure_ingested(instance: str) -> Optional[IngestReport]:
    """Ingest missions.md (+ CI / Ideas / quarantine) into the store, once.

    Returns the missions IngestReport, or ``None`` if the store was already
    initialized (nothing to do).
    """
    from app.mission_store import get_mission_store
    store = get_mission_store(instance)
    # Short-circuit if the store is already populated by EITHER path: the S3
    # ingest marker (initialized_at) OR the S8 cutover sync marker (s8_synced,
    # set by ensure_store_synced / prune_missions_done's re-sync). Without the
    # is_synced() guard, a boot where startup pruning re-syncs first would then
    # append a full SECOND copy here (ingest_from_file INSERTs without deleting).
    if store.is_initialized() or store.is_synced():
        return None

    md = Path(instance) / "missions.md"
    content = md.read_text() if md.exists() else ""

    # Sibling populations first; the missions ingest sets the initialized marker
    # last, so a crash mid-ingest simply retries the whole thing next boot.
    if content:
        from app import missions
        from app.mission_store.aux_stores import CiQueueStore, IdeaStore
        CiQueueStore(instance).ingest_items(missions.get_ci_items(content))
        IdeaStore(instance).ingest_items(missions.parse_ideas(content))

    _ingest_quarantine_file(instance)

    report = store.ingest_from_file(md)
    logger.info("[mission_store] one-time ingest: %s inserted, %s unparseable",
                report.inserted, len(report.unparseable))
    return report


def _ingest_quarantine_file(instance: str) -> None:
    qpath = Path(instance) / "missions-quarantine.md"
    if not qpath.exists():
        return
    from app.mission_store.aux_stores import QuarantineStore
    store = QuarantineStore(instance)
    total = failed = 0
    for line in qpath.read_text().splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        m = _Q_RE.match(line)
        if m:
            ok = store.add(m.group("text"), m.group("reason").strip(),
                           m.group("src").strip())
        else:  # keep unparseable entries rather than dropping them
            ok = store.add(line[2:].strip(), "imported", "quarantine-file")
        total += 1
        failed += not ok
    if failed:
        # QuarantineStore.add returns False (and logs) on a DB write failure. Post
        # cutover the file is regenerated from the store on the next quarantine, so
        # an unmigrated record would be silently dropped — surface a loud migration
        # summary instead of proceeding as if every security record was preserved.
        logger.error(
            "[mission_store] quarantine migration incomplete: %s of %s records "
            "failed to migrate into the store", failed, total)
