"""SQLite mirror of mission state, indexed by state and project.

``missions.md`` remains the source of truth during the transition; this DB is a
read-optimized projection populated by every committed file transition. Every
function catches :class:`sqlite3.DatabaseError` and returns a safe default,
never raising into the agent loop (mirrors ``memory_db``).

Reads consult the DB opportunistically (DB-if-present, file fallback); hot
queries like "count pending" become constant-time ``SELECT COUNT(*)`` instead
of full-file regex scans. Writes are best-effort: a DB error must never roll
back or abort the ``missions.md`` transition that already committed.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_VALID_STATES = ("pending", "in_progress", "done", "failed")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS missions (
    id           INTEGER PRIMARY KEY,
    text         TEXT NOT NULL,
    state        TEXT NOT NULL CHECK(state IN {_VALID_STATES}),
    project      TEXT NOT NULL DEFAULT 'default',
    created_at   TEXT,
    started_at   TEXT,
    completed_at TEXT,
    queued_at    TEXT,
    complexity   TEXT
);
CREATE INDEX IF NOT EXISTS idx_missions_state   ON missions(state);
CREATE INDEX IF NOT EXISTS idx_missions_project ON missions(project);
"""


def ensure_db(instance: str) -> Optional[sqlite3.Connection]:
    """Open (or create) ``instance/missions.db`` with WAL mode.

    Returns a connection, or ``None`` when the database cannot be opened.
    Callers must close the connection when done.
    """
    conn = None
    try:
        db_path = Path(instance) / "missions.db"
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] ensure_db failed: %s", e)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        return None


def _canonical(text: str) -> str:
    from app.missions import canonical_mission_key
    return canonical_mission_key(text)


# Terminal states keep historical rows; a live transition must never rewrite
# them (a re-queued identical mission gets its own fresh row instead).
_TERMINAL_STATES = ("done", "failed")


def _insert_row(instance: str, text: str, state: str, *, project: str = "default",
                started_at: str = "", completed_at: str = "",
                queued_at: str = "", complexity: str = "") -> None:
    """Insert one row in an explicit ``state`` (keyed on canonical text)."""
    if state not in _VALID_STATES:
        return
    conn = ensure_db(instance)
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO missions(text, state, project, created_at, "
            "queued_at, started_at, completed_at, complexity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_canonical(text), state, project or "default", queued_at or None,
             queued_at or None, started_at or None, completed_at or None,
             complexity or None),
        )
        conn.commit()
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] _insert_row failed: %s", e)
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def insert_mission_row(instance: str, text: str, *, project: str = "default",
                       queued_at: str = "", complexity: str = "") -> None:
    """Insert a new pending row keyed on the canonical mission text."""
    _insert_row(instance, text, "pending", project=project,
                queued_at=queued_at, complexity=complexity)


def _row_exists(instance: str, text: str) -> bool:
    """Whether any row (in any state) exists for this canonical mission text."""
    conn = ensure_db(instance)
    if conn is None:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM missions WHERE text=? LIMIT 1", (_canonical(text),)
        ).fetchone()
        return row is not None
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] _row_exists failed: %s", e)
        return False
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def set_mission_state(instance: str, text: str, state: str, *,
                      started_at: str = "", completed_at: str = "") -> bool:
    """Transition a *live* (non-terminal) row to ``state`` and stamp the relevant
    timestamp. Returns True if a row was updated, False otherwise.

    The ``WHERE state NOT IN (done, failed)`` clause protects historical rows:
    a re-queued mission that shares a canonical key with an old Done/Failed entry
    must not flip that entry's state.
    """
    if state not in _VALID_STATES:
        return False
    conn = ensure_db(instance)
    if conn is None:
        return False
    try:
        cur = conn.execute(
            "UPDATE missions SET state=?, "
            "started_at=COALESCE(?, started_at), "
            "completed_at=COALESCE(?, completed_at) "
            "WHERE text=? AND state NOT IN ('done', 'failed')",
            (state, started_at or None, completed_at or None, _canonical(text)),
        )
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] set_mission_state failed: %s", e)
        return False
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def mission_count_by_state(instance: str, state: str) -> int:
    """Constant-time count of rows in ``state``. Returns 0 when the DB is absent."""
    conn = ensure_db(instance)
    if conn is None:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM missions WHERE state=?", (state,)
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] mission_count_by_state failed: %s", e)
        return 0
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def list_by_state(instance: str, state: str) -> List[dict]:
    """Return all rows in ``state`` as dicts, ordered by insertion id."""
    conn = ensure_db(instance)
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM missions WHERE state=? ORDER BY id", (state,))]
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] list_by_state failed: %s", e)
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()


# Per-state, the lifecycle column to stamp during migration and the
# extract_timestamps() key that feeds it.
_STATE_FIELD = {
    "pending": ("queued_at", "queued"),
    "in_progress": ("started_at", "started"),
    "done": ("completed_at", "completed"),
    "failed": ("completed_at", "completed"),
}


def mirror_transition(instance: str, text: str, state: str, *, project: str = "default",
                      started_at: str = "", completed_at: str = "",
                      queued_at: str = "", complexity: str = "") -> None:
    """Best-effort: reflect a committed ``missions.md`` transition into the DB.

    Never raises — a DB error must not roll back the ``missions.md`` write that
    already committed. On a pending insert with no existing row, create it;
    otherwise update the existing row's state and the relevant timestamp.
    """
    try:
        if state == "pending":
            # If a live row already exists (a re-queued In Progress mission, or an
            # already-pending one) move/keep it pending — never duplicate it. Only
            # when no live row exists do we insert a fresh pending row. Historical
            # Done/Failed rows are deliberately ignored so a re-run of an identical
            # past mission gets its own new row.
            if set_mission_state(instance, text, "pending"):
                return
            insert_mission_row(instance, text, project=project,
                               queued_at=queued_at, complexity=complexity)
        else:
            # Transition the live row; if none exists (the mission was queued via
            # a path that bypassed the mirror, or migration skipped it), create
            # the row directly in its current state so the DB still learns of it.
            updated = set_mission_state(instance, text, state,
                                        started_at=started_at, completed_at=completed_at)
            if not updated and not _row_exists(instance, text):
                _insert_row(instance, text, state, project=project,
                            started_at=started_at, completed_at=completed_at,
                            queued_at=queued_at, complexity=complexity)
    except Exception as e:  # defensive: mirroring is never fatal
        logger.warning("[missions_db] mirror_transition swallowed: %s", e)


def prune_terminal_rows(instance: str, done_keep: int, failed_keep: int) -> int:
    """Cap terminal (``done``/``failed``) rows, keeping the most recent N per state.

    Mirrors ``missions.enforce_size_bound`` so the DB's terminal history stays
    bounded and tracks the pruned ``missions.md`` instead of growing without
    bound across a long-running daemon. "Most recent" is by insertion ``id``
    (rows are inserted in transition order). A non-positive keep deletes all
    rows in that state. Best-effort: returns the number of rows deleted, 0 on
    any error.
    """
    conn = ensure_db(instance)
    if conn is None:
        return 0
    deleted = 0
    try:
        for state, keep in (("done", done_keep), ("failed", failed_keep)):
            if keep <= 0:
                cur = conn.execute("DELETE FROM missions WHERE state=?", (state,))
            else:
                cur = conn.execute(
                    "DELETE FROM missions WHERE state=? AND id NOT IN "
                    "(SELECT id FROM missions WHERE state=? ORDER BY id DESC LIMIT ?)",
                    (state, state, keep),
                )
            deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        return deleted
    except sqlite3.DatabaseError as e:
        logger.warning("[missions_db] prune_terminal_rows failed: %s", e)
        return 0
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def reconcile(instance: str) -> dict:
    """Atomically truncate the DB and rebuild it from ``missions.md`` (idempotent).

    The truncate and the repopulate run in a **single transaction**: if the
    rebuild fails or yields nothing, the deletion rolls back to the prior rows
    instead of leaving the table silently empty/partial. The returned report
    carries an ``"ok"`` flag (``False`` on any failure) so callers can detect a
    rebuild that did not complete cleanly.
    """
    conn = ensure_db(instance)
    if conn is None:
        return {"inserted": 0, "unparseable": [], "by_state": {}, "ok": False}
    try:
        conn.execute("DELETE FROM missions")
        report = _populate_from_md(conn, instance)
        if report.get("ok", True):
            conn.commit()
        else:
            with contextlib.suppress(Exception):
                conn.rollback()
        return report
    except Exception as e:  # defensive: never raise into the agent loop
        logger.warning("[missions_db] reconcile failed: %s", e)
        with contextlib.suppress(Exception):
            conn.rollback()
        return {"inserted": 0, "unparseable": [], "by_state": {}, "ok": False}
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _mission_key_source(item: str) -> Optional[str]:
    """Return the text whose canonical key identifies this missions.md item.

    Simple missions start with ``- ``; complex missions are ``### `` blocks
    whose first line names them. Returns ``None`` for items with neither (e.g.
    a stray continuation line), which the caller flags as unparseable.
    """
    first = next((ln.strip() for ln in item.splitlines() if ln.strip()), "")
    if first.startswith("### "):
        return first[4:].strip()
    if first.startswith("- "):
        return first
    return None


def _populate_from_md(conn: Optional[sqlite3.Connection], instance: str, *,
                      dry_run: bool = False) -> dict:
    """Parse ``missions.md`` and insert one row per entry on ``conn``.

    The caller owns the transaction: this helper neither commits nor closes
    ``conn`` (so ``reconcile`` can truncate + repopulate atomically). When
    ``dry_run`` is True, ``conn`` may be ``None`` and nothing is written.

    Returns ``{"inserted", "unparseable", "by_state", "ok"}``. ``ok`` is False
    if a non-DB error (e.g. a parse-layer failure) aborts the walk — so a
    failure can never masquerade as a clean empty rebuild.
    """
    from app.missions import (
        extract_complexity_tag,
        extract_project_tag,
        extract_timestamps,
        parse_sections,
    )
    md = Path(instance) / "missions.md"
    report = {"inserted": 0, "unparseable": [], "by_state": {}, "ok": True}
    if not md.exists():
        return report
    try:
        sections = parse_sections(md.read_text())
        for state, items in sections.items():
            if state not in _VALID_STATES:
                continue
            ts_field, ts_key = _STATE_FIELD[state]
            for item in items:
                key_src = _mission_key_source(item)
                if key_src is None:
                    report["unparseable"].append(item[:120])
                    continue
                if dry_run:
                    report["inserted"] += 1
                    report["by_state"][state] = report["by_state"].get(state, 0) + 1
                    continue
                ts = extract_timestamps(item)
                when = ts.get(ts_key)
                queued = ts.get("queued")
                when_str = when.strftime("%Y-%m-%dT%H:%M") if when else None
                queued_str = queued.strftime("%Y-%m-%dT%H:%M") if queued else None
                base_cols = (_canonical(key_src), state,
                             extract_project_tag(item),
                             extract_complexity_tag(item))
                if ts_field == "queued_at":
                    # pending: ts_field is already queued_at, so naming it plus
                    # queued_at would duplicate the column. Bind it once.
                    sql = ("INSERT INTO missions(text, state, project, complexity, queued_at) "
                           "VALUES (?, ?, ?, ?, ?)")
                    params = (*base_cols, queued_str)
                else:
                    sql = (f"INSERT INTO missions(text, state, project, complexity, {ts_field}, queued_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)")
                    params = (*base_cols, when_str, queued_str)
                try:
                    conn.execute(sql, params)
                    report["inserted"] += 1
                    report["by_state"][state] = report["by_state"].get(state, 0) + 1
                except sqlite3.DatabaseError as e:
                    logger.warning("[missions_db] migrate insert failed: %s", e)
                    report["unparseable"].append(item[:120])
    except Exception as e:  # parse-layer or DB failure: signal, don't escape
        logger.warning("[missions_db] populate_from_md failed: %s", e)
        report["ok"] = False
    return report


def migrate_md_to_sqlite(instance: str, *, dry_run: bool = False) -> dict:
    """Parse ``missions.md`` and insert one row per entry (own transaction).

    Returns ``{"inserted", "unparseable", "by_state", "ok"}``. Items with no
    identifiable ``- ``/``### `` line surface in ``unparseable`` rather than
    being dropped. When ``dry_run`` is True, nothing is written — the report
    reflects what *would* be inserted (single source of truth for the migration
    CLI's dry-run output).
    """
    if dry_run:
        return _populate_from_md(None, instance, dry_run=True)
    conn = ensure_db(instance)
    if conn is None:
        return {"inserted": 0, "unparseable": [], "by_state": {}, "ok": False}
    try:
        report = _populate_from_md(conn, instance)
        conn.commit()
        return report
    finally:
        with contextlib.suppress(Exception):
            conn.close()
