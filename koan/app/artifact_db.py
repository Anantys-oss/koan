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
    conn = None
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    except (sqlite3.DatabaseError, OSError) as e:
        # OSError covers mkdir failures (perms, read-only FS) so the documented
        # "None on failure" contract holds for non-DB errors too.
        logger.warning("[artifact_db] connect failed: %s", e)
        if conn is not None:
            # PRAGMA raised after connect() succeeded — close the orphan so we
            # don't leak the handle/WAL lock while still returning None.
            try:
                conn.close()
            except sqlite3.DatabaseError as close_err:
                # Log so a leaked handle/WAL lock is observable rather than
                # vanishing into a bare ``pass``.
                logger.warning(
                    "[artifact_db] could not close orphan conn: %s", close_err)
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


def _mark_dirty(conn: sqlite3.Connection, table: str) -> bool:
    """Flag the projection divergent after a failed write.

    Returns ``True`` when the dirty flag was persisted. On ``False`` the flag may
    still read ``0`` (the last good write), so the caller must invalidate the
    connection to keep the "file is source of truth" guarantee.
    """
    try:
        _set_dirty(conn, table, True)
        conn.commit()
        return True
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] could not mark %s dirty: %s", table, e)
        return False


def _is_dirty(conn: sqlite3.Connection, table: str) -> bool:
    """Return ``True`` when the projection is known-divergent or unverifiable."""
    try:
        row = conn.execute(
            f"SELECT dirty FROM {_META_TABLE} WHERE table_name = ?", (table,)
        ).fetchone()
    except sqlite3.DatabaseError as e:
        # Surface persistent meta-table corruption/locking instead of masking
        # it as a routine file fallback.
        logger.warning("[artifact_db] dirty check for %s failed: %s", table, e)
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
               conn: Optional[sqlite3.Connection], table: str,
               mode: str = "replace") -> None:
    """Write the authoritative file first, then best-effort project to DB.

    ``file_writer`` (e.g. a wrapper over :func:`utils.atomic_write`) owns the
    source of truth; the DB projection mirrors whatever it wrote.

    ``mode`` matches the artifact's file-write style:

    - ``"replace"`` (default) — rewrite-style artifacts (``missions.md``,
      ``outbox.md``): ``file_writer`` rewrites the whole file, so the projection
      is truncated and rebuilt in one transaction. Appending would accumulate
      duplicates across rewrites.
    - ``"append"`` — append-only artifacts (``journal``, ``memory`` log,
      ``recovery.jsonl``): ``records`` are the new rows only; they are inserted
      without truncating, mirroring an append to the file. A once-dirty append
      projection stays dirty across subsequent successful appends — an append
      cannot backfill rows a prior failed append dropped, so only a full rebuild
      (:func:`rebuild_from_file`) heals it.

    A DB projection failure is logged, rolled back, and the projection flagged
    dirty (so subsequent reads fall back to the file) but never propagated; if
    the dirty flag itself cannot be persisted the connection is closed so reads
    still fall through to the file. If ``file_writer`` raises, the error
    propagates (the write genuinely failed).
    """
    if mode not in ("replace", "append"):
        raise ValueError(f"dual_write: unknown mode {mode!r}")
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
        if mode == "replace":
            conn.execute(f"DELETE FROM {table}")   # full-rewrite mirror
            conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in records])
            _set_dirty(conn, table, False)         # full rebuild heals any gap
        else:  # append
            # An append inserts only the new rows; it cannot backfill rows a
            # prior failed append dropped. Clearing the dirty flag here would
            # serve a DB permanently missing those rows. So a once-dirty append
            # projection stays dirty until rebuild_from_file() heals it.
            already_dirty = _is_dirty(conn, table)
            conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in records])
            _set_dirty(conn, table, already_dirty)
        conn.commit()
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] dual_write DB projection failed (%s): %s", table, e)
        # A structural problem (table missing / schema drift) makes every write
        # fail identically; surface it once as a distinct diagnostic instead of
        # only the per-write warning above.
        drift = verify_schema(conn, table)
        if drift.get("in_sync") is not True:
            logger.warning(
                "[artifact_db] %s projection structurally unusable "
                "(in_sync=%s, missing=%s, unexpected=%s) — running file-only",
                table, drift.get("in_sync"), drift.get("missing"),
                drift.get("unexpected"))
        try:
            conn.rollback()                    # leave a consistent (stale) DB
        except sqlite3.DatabaseError as rb_err:
            logger.warning("[artifact_db] rollback failed for %s: %s", table, rb_err)
        if not _mark_dirty(conn, table):
            # Dirty flag unpersistable -> a stale dirty=0 would serve the
            # divergent DB. Close the connection so every later read raises
            # DatabaseError and falls back to the authoritative file.
            try:
                conn.close()
            except sqlite3.DatabaseError as close_err:
                # Even close() failed — surface it. A live conn with stale
                # dirty=0 would keep serving the divergent DB as if it were the
                # authoritative file; logging makes that observable.
                logger.warning(
                    "[artifact_db] could not close %s conn after failed dirty "
                    "flag: %s", table, close_err)


def rebuild_from_file(conn: Optional[sqlite3.Connection], table: str, *,
                      file_reader: Callable[[], List[Dict]]) -> bool:
    """Rebuild a projection from the authoritative file, clearing the dirty flag.

    The named recovery handle for a dirty (usually ``"append"``) projection: an
    append cannot backfill rows a prior failed append dropped, so only a full
    re-read + replace heals it (see :func:`dual_write`). Re-parses the whole file
    via ``file_reader`` and rewrites the projection in one transaction. Mirrors
    :func:`app.memory_db.migrate_jsonl_to_sqlite`; the file is never written.

    Returns ``True`` when the projection was rebuilt and marked clean, ``False``
    on any DB error (file stays authoritative, projection stays dirty).
    """
    if conn is None:
        return False
    spec = ARTIFACT_SCHEMAS.get(table)
    if spec is None:
        logger.warning("[artifact_db] rebuild_from_file: unknown artifact %r", table)
        return False
    cols = [c.name for c in spec.columns if not c.primary_key]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    records = file_reader()                    # authoritative source
    try:
        conn.execute(f"DELETE FROM {table}")   # full-rewrite mirror
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in records])
        _set_dirty(conn, table, False)         # full rebuild heals any gap
        conn.commit()
        return True
    except sqlite3.DatabaseError as e:
        logger.warning("[artifact_db] rebuild_from_file(%s) failed: %s", table, e)
        try:
            conn.rollback()                    # leave a consistent (stale) DB
        except sqlite3.DatabaseError as rb_err:
            logger.warning("[artifact_db] rollback failed for %s: %s", table, rb_err)
        return False


def read_from_db_or_file(conn: Optional[sqlite3.Connection], table: str, *,
                         file_reader: Callable[[], List[Dict]],
                         order_key: Optional[str] = None) -> List[Dict]:
    """Read DB-first with file fallback, preserving file-parse ordering.

    Falls back to ``file_reader()`` when the DB is unavailable, the table is
    empty, the projection is flagged dirty (a prior write failed to project),
    the live schema drifts from the declared columns (verified via
    :func:`verify_schema`), or a DB error occurs. ``order_key`` overrides default
    insertion (rowid) ordering
    for artifacts whose file order is semantic; it is validated against the
    declared (non-PK) columns to avoid interpolating an arbitrary identifier into
    SQL. Surrogate primary-key columns are excluded from the result so a
    DB-served read carries the same dict shape as ``file_reader()``.
    """
    spec = ARTIFACT_SCHEMAS.get(table)
    if conn is None or spec is None:
        return file_reader()
    if _is_dirty(conn, table):
        return file_reader()        # projection known-divergent -> trust the file
    # Honor the in-sync guarantee: a column-superset/-subset drift would yield a
    # different dict shape than the file, so fall back rather than serve it.
    # in_sync is None pre-create (empty projection -> falls back below anyway).
    if verify_schema(conn, table).get("in_sync") is False:
        return file_reader()
    # Exclude surrogate PK columns: the file has no synthetic ``id``, so a
    # DB-served read must yield the same dict shape as ``file_reader()`` or a
    # consumer could tell which source served the read (key presence).
    cols = [c.name for c in spec.columns if not c.primary_key]
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
