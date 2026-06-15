"""SQLite FTS5 secondary index over the JSONL memory truth log.

The JSONL file remains the source of truth — SQLite is a read-optimized
projection for ranked retrieval.  All functions catch ``sqlite3.DatabaseError``
and return empty results / 0, never raising to callers.  When FTS5 is not
compiled into Python's sqlite3 build, all search functions short-circuit
gracefully.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_fts5_available: Optional[bool] = None


def _check_fts5(conn: sqlite3.Connection) -> bool:
    """Test whether FTS5 is available in this Python build.

    Only caches a positive result.  Transient errors (locked DB, I/O)
    leave the flag unset so the next call retries instead of permanently
    latching False.
    """
    global _fts5_available
    if _fts5_available is True:
        return True
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe "
            "USING fts5(test_col)"
        )
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        _fts5_available = True
        return True
    except sqlite3.OperationalError:
        logger.warning("[memory_db] FTS5 not available — falling back to JSONL-only")
        return False


def ensure_db(instance: str) -> Optional[sqlite3.Connection]:
    """Open (or create) ``instance/memory/memory.db`` with WAL mode.

    Returns a connection, or ``None`` when FTS5 is unavailable or
    the database cannot be opened.
    """
    mem_dir = Path(instance) / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    db_path = mem_dir / "memory.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        if not _check_fts5(conn):
            conn.close()
            return None
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS entries "
            "USING fts5(project, type, content, ts UNINDEXED)"
        )
        conn.commit()
        return conn
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] ensure_db failed: %s", e)
        with contextlib.suppress(Exception):
            conn.close()
        return None


def insert_entry(conn: sqlite3.Connection, entry: Dict) -> None:
    """Insert a single JSONL-shaped dict into the FTS5 table."""
    try:
        conn.execute(
            "INSERT INTO entries(project, type, content, ts) VALUES (?, ?, ?, ?)",
            (
                entry.get("project") or "",
                entry.get("type") or "",
                entry.get("content") or "",
                entry.get("ts") or "",
            ),
        )
        conn.commit()
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] insert_entry failed: %s", e)


def search_entries(
    conn: sqlite3.Connection,
    project: str,
    query_text: str,
    max_results: int = 20,
) -> List[Dict]:
    """FTS5 ranked search over session entries for a project.

    Accepts raw natural-language ``query_text`` — sanitization is handled
    internally via ``build_fts5_query()``.  Returns empty list when query
    produces no usable tokens.
    """
    from app.memory_recall import build_fts5_query

    fts_query = build_fts5_query(query_text)
    if not fts_query:
        return []

    try:
        project_lower = project.lower() if project else ""
        rows = conn.execute(
            "SELECT project, type, content, ts, rank "
            "FROM entries "
            "WHERE entries MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, max_results * 3),
        ).fetchall()

        results = []
        for proj, type_, content, ts, _rank in rows:
            entry_proj = proj or ""
            if entry_proj == "" or (project_lower and entry_proj.lower() == project_lower):
                results.append({
                    "project": proj if proj else None,
                    "type": type_,
                    "content": content,
                    "ts": ts,
                })
                if len(results) >= max_results:
                    break
        return results
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] search_entries failed: %s", e)
        return []


def search_learnings(
    conn: sqlite3.Connection,
    learnings_content: str,
    query_text: str,
    max_k: int = 40,
) -> List[str]:
    """Score learnings lines against query using a transient in-memory FTS5 table.

    Loads ``learnings_content`` into a temporary in-memory database, runs
    FTS5 ``MATCH``, and returns ranked lines.  Accepts raw natural-language
    ``query_text``.
    """
    from app.memory_recall import build_fts5_query

    fts_query = build_fts5_query(query_text)
    if not fts_query:
        return []

    mem_conn = None
    try:
        mem_conn = sqlite3.connect(":memory:")
        if not _check_fts5(mem_conn):
            return []
        mem_conn.execute(
            "CREATE VIRTUAL TABLE learnings USING fts5(line_text, line_idx UNINDEXED)"
        )

        lines = []
        for raw in learnings_content.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(line)

        mem_conn.executemany(
            "INSERT INTO learnings(line_text, line_idx) VALUES (?, ?)",
            [(line, str(idx)) for idx, line in enumerate(lines)],
        )
        mem_conn.commit()

        rows = mem_conn.execute(
            "SELECT line_text, line_idx, rank "
            "FROM learnings "
            "WHERE learnings MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (fts_query, max_k),
        ).fetchall()

        idx_to_line = {int(row[1]): row[0] for row in rows}
        return [idx_to_line[i] for i in sorted(idx_to_line)]
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] search_learnings failed: %s", e)
        return []
    finally:
        if mem_conn:
            mem_conn.close()


def recent_entries(
    conn: sqlite3.Connection,
    project: str,
    max_results: int = 20,
) -> List[Dict]:
    """Recency-only fallback, ordered by ts DESC.

    Since ``ts`` is UNINDEXED in FTS5, we do a full scan sorted by ts.
    """
    try:
        project_lower = project.lower() if project else ""
        rows = conn.execute(
            "SELECT project, type, content, ts FROM entries ORDER BY ts DESC LIMIT ?",
            (max_results * 5,),
        ).fetchall()

        results = []
        for proj, type_, content, ts in rows:
            entry_proj = proj or ""
            if entry_proj == "" or (project_lower and entry_proj.lower() == project_lower):
                results.append({
                    "project": proj if proj else None,
                    "type": type_,
                    "content": content,
                    "ts": ts,
                })
                if len(results) >= max_results:
                    break
        results.reverse()
        return results
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] recent_entries failed: %s", e)
        return []


def delete_before(conn: sqlite3.Connection, cutoff_iso: str) -> int:
    """Delete entries with ts < cutoff_iso. Returns count removed."""
    try:
        cursor = conn.execute(
            "DELETE FROM entries WHERE ts < ? AND ts != ''",
            (cutoff_iso,),
        )
        conn.commit()
        return cursor.rowcount
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] delete_before failed: %s", e)
        return 0


def entry_count(conn: sqlite3.Connection) -> Optional[int]:
    """Return total row count in entries table, or None on error."""
    try:
        row = conn.execute("SELECT count(*) FROM entries").fetchone()
        return row[0] if row else 0
    except sqlite3.DatabaseError:
        return None


def migrate_jsonl_to_sqlite(instance: str) -> int:
    """One-time migration: index all existing JSONL entries into SQLite.

    Runs only when ``memory.db`` is missing or empty.  Returns the number
    of entries indexed.
    """
    conn = ensure_db(instance)
    if conn is None:
        return 0

    try:
        count = entry_count(conn)
        if count is None or count > 0:
            conn.close()
            return 0

        log_path = Path(instance) / "memory" / "log.jsonl"
        if not log_path.exists():
            conn.close()
            return 0

        import time
        start = time.monotonic()

        count = 0
        try:
            raw = log_path.read_text(encoding="utf-8")
        except OSError:
            conn.close()
            return 0

        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entries.append((
                    obj.get("project") or "",
                    obj.get("type") or "",
                    obj.get("content") or "",
                    obj.get("ts") or "",
                ))
            except json.JSONDecodeError:
                continue

        if entries:
            conn.executemany(
                "INSERT INTO entries(project, type, content, ts) VALUES (?, ?, ?, ?)",
                entries,
            )
            conn.commit()
            count = len(entries)

        elapsed = time.monotonic() - start
        logger.info(
            "[memory_db] Migrated %d JSONL entries to SQLite in %.1fs",
            count, elapsed,
        )
        conn.close()
        return count
    except sqlite3.DatabaseError as e:
        logger.warning("[memory_db] migration failed: %s", e)
        with contextlib.suppress(Exception):
            conn.close()
        return 0


def db_path(instance: str) -> Path:
    """Return the expected path for memory.db."""
    return Path(instance) / "memory" / "memory.db"
