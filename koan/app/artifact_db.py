"""Unified artifact schema + migration harness.

File stays the source of truth; SQLite is a read-optimized projection
(mirrors :mod:`app.memory_db`). Every function catches ``sqlite3.DatabaseError``
and degrades gracefully — callers never see a raised DB error.

This module ships the harness only. No live artifact read/write path is
migrated here; downstream issues wire it into concrete artifacts.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ColumnSpec:
    """A single column declaration that owns its own DDL fragment."""

    name: str
    sql_type: str = "TEXT"  # TEXT / INTEGER / REAL
    primary_key: bool = False
    nullable: bool = True

    def ddl(self) -> str:
        parts = [self.name, self.sql_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable and not self.primary_key:
            parts.append("NOT NULL")
        return " ".join(parts)


@dataclass(frozen=True)
class TableSpec:
    """An artifact table declaration that generates its own ``CREATE TABLE``."""

    name: str
    columns: List[ColumnSpec] = field(default_factory=list)

    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def create_ddl(self) -> str:
        cols = ", ".join(c.ddl() for c in self.columns)
        return f"CREATE TABLE IF NOT EXISTS {self.name} ({cols})"


# Bookkeeping table tracking whether each artifact's DB projection is in sync
# with the authoritative file. A failed/rolled-back dual_write sets dirty=1 so
# read_from_db_or_file serves the file until the next successful projection.
_META_TABLE = "_artifact_meta"


# --- Concrete artifact schemas (shapes pinned to current code) ---
ARTIFACT_SCHEMAS: Dict[str, TableSpec] = {
    # missions.md — sections + [project:name] tags (see missions.py)
    "missions": TableSpec("missions", [
        ColumnSpec("id", "INTEGER", primary_key=True),
        ColumnSpec("text", nullable=False),
        ColumnSpec("section", nullable=False),   # pending/in_progress/done/failed/ci
        ColumnSpec("project"),
        ColumnSpec("queued_ts"),
        ColumnSpec("started_ts"),
        ColumnSpec("completed_ts"),
    ]),
    # journal/YYYY-MM-DD/project.md
    "journal_entries": TableSpec("journal_entries", [
        ColumnSpec("id", "INTEGER", primary_key=True),
        ColumnSpec("date", nullable=False),
        ColumnSpec("project", nullable=False),
        ColumnSpec("content", nullable=False),
        ColumnSpec("ts"),
    ]),
    # memory/log.jsonl — columns match memory_db._EXPECTED_COLUMNS
    "memory_entries": TableSpec("memory_entries", [
        ColumnSpec("id", "INTEGER", primary_key=True),
        ColumnSpec("project"),
        ColumnSpec("type"),
        ColumnSpec("content"),
        ColumnSpec("ts"),
        ColumnSpec("source_skill"),
        ColumnSpec("tags"),
        ColumnSpec("confidence"),
        ColumnSpec("expires_at"),
    ]),
    # outbox.md (see outbox_manager)
    "outbox_messages": TableSpec("outbox_messages", [
        ColumnSpec("id", "INTEGER", primary_key=True),
        ColumnSpec("body", nullable=False),
        ColumnSpec("priority"),
        ColumnSpec("ts"),
    ]),
    # recovery.jsonl shape (see recover.py)
    "audit_log": TableSpec("audit_log", [
        ColumnSpec("id", "INTEGER", primary_key=True),
        ColumnSpec("timestamp", nullable=False),
        ColumnSpec("mission"),
        ColumnSpec("state"),
        ColumnSpec("action"),
        ColumnSpec("attempts", "INTEGER"),
        ColumnSpec("has_checkpoint", "INTEGER"),
    ]),
}


def connect(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open a SQLite connection, returning ``None`` on any DB error."""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] connect failed: %s", e)
        return None


def create_tables(conn: sqlite3.Connection) -> bool:
    """Create every artifact table idempotently. Returns ``False`` on DB error."""
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_META_TABLE} "
            "(table_name TEXT PRIMARY KEY, dirty INTEGER NOT NULL DEFAULT 0)"
        )
        for spec in ARTIFACT_SCHEMAS.values():
            conn.execute(spec.create_ddl())
        conn.commit()
        return True
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] create_tables failed: %s", e)
        return False


def _set_dirty(conn: sqlite3.Connection, table: str, dirty: bool) -> None:
    """Stage a dirty-flag update (no commit — caller owns the transaction)."""
    conn.execute(
        f"INSERT OR REPLACE INTO {_META_TABLE} (table_name, dirty) VALUES (?, ?)",
        (table, 1 if dirty else 0),
    )


def _mark_dirty(conn: sqlite3.Connection, table: str) -> None:
    """Best-effort: flag the projection divergent after a failed write."""
    try:
        _set_dirty(conn, table, True)
        conn.commit()
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] could not mark %s dirty: %s", table, e)


def _is_dirty(conn: sqlite3.Connection, table: str) -> bool:
    """Return ``True`` when the projection is known-divergent or unverifiable."""
    try:
        row = conn.execute(
            f"SELECT dirty FROM {_META_TABLE} WHERE table_name = ?", (table,)
        ).fetchone()
    except sqlite3.DatabaseError:
        return True  # can't verify -> assume divergent, serve the file
    return bool(row and row[0])


def verify_schema(conn: sqlite3.Connection, table: str) -> Dict:
    """Compare declared columns against live ``PRAGMA table_info``.

    Returns ``{in_sync, missing, unexpected}``. ``in_sync`` is ``None`` when the
    table does not exist yet (verification can run pre-create). Comparison is on
    column names only — SQLite type affinity makes type comparison noisy.
    """
    spec = ARTIFACT_SCHEMAS.get(table)
    if spec is None:
        return {"in_sync": False, "missing": [], "unexpected": [],
                "error": f"unknown artifact {table!r}"}
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] verify_schema(%s) failed: %s", table, e)
        return {"in_sync": False, "missing": [], "unexpected": [], "error": str(e)}
    if not rows:
        return {"in_sync": None, "missing": [], "unexpected": []}
    live = {r[1] for r in rows}             # r[1] = column name
    declared = set(spec.column_names())
    missing = sorted(declared - live)
    unexpected = sorted(live - declared)
    return {"in_sync": not missing and not unexpected,
            "missing": missing, "unexpected": unexpected}


def dual_write(records: List[Dict], *, file_writer: Callable[[List[Dict]], None],
               conn: Optional[sqlite3.Connection], table: str) -> None:
    """Write the authoritative file first, then best-effort project to DB.

    ``file_writer`` (e.g. a wrapper over :func:`utils.atomic_write`) owns the
    source of truth and rewrites the whole artifact, so the DB projection is
    truncated and rebuilt in one transaction to mirror it — appending would
    accumulate duplicates across rewrites. A DB projection failure is logged,
    rolled back, and the projection flagged dirty (so subsequent reads fall back
    to the file) but never propagated. If ``file_writer`` raises, the error
    propagates (the write genuinely failed).
    """
    file_writer(records)                       # authoritative — may raise
    if conn is None:
        return
    spec = ARTIFACT_SCHEMAS.get(table)
    if spec is None:
        logger.warning("[artifact_db] dual_write: unknown artifact %r", table)
        return
    cols = [c.name for c in spec.columns if not c.primary_key]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    try:
        conn.execute(f"DELETE FROM {table}")   # full-rewrite mirror, not append
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in records])
        _set_dirty(conn, table, False)
        conn.commit()
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] dual_write DB projection failed (%s): %s", table, e)
        try:
            conn.rollback()                    # leave a consistent (stale) DB
        except sqlite3.DatabaseError:
            pass
        _mark_dirty(conn, table)               # reads fall back to the file


def read_from_db_or_file(conn: Optional[sqlite3.Connection], table: str, *,
                         file_reader: Callable[[], List[Dict]],
                         order_key: Optional[str] = None) -> List[Dict]:
    """Read DB-first with file fallback, preserving file-parse ordering.

    Falls back to ``file_reader()`` when the DB is unavailable, the table is
    empty, the projection is flagged dirty (a prior write failed to project), or
    a DB error occurs. ``order_key`` overrides default insertion (rowid) ordering
    for artifacts whose file order is semantic; it is validated against the
    declared columns to avoid interpolating an arbitrary identifier into SQL.
    """
    spec = ARTIFACT_SCHEMAS.get(table)
    if conn is None or spec is None:
        return file_reader()
    if _is_dirty(conn, table):
        return file_reader()        # projection known-divergent -> trust the file
    cols = spec.column_names()
    if order_key and order_key not in cols:
        logger.warning(
            "[artifact_db] read %s: unknown order_key %r, using rowid", table, order_key)
        order_key = None
    order = f"{order_key} ASC" if order_key else "rowid ASC"
    try:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"
        ).fetchall()
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] read %s failed, file fallback: %s", table, e)
        return file_reader()
    if not rows:
        return file_reader()        # empty projection -> trust the file
    return [dict(zip(cols, row)) for row in rows]
