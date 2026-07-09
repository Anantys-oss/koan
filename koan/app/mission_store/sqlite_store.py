"""SQLite implementation of the :class:`MissionStore` port (the in-tree default).

Mission state is authoritative here (Constitution III, amended v2.0.0):
``instance/missions.db`` in WAL mode, keyed on an integer primary key with an
explicit ``sequence`` column for queue order. ``missions.md`` is a generated
read-only export (:meth:`SqliteMissionStore.export_view`).

Unlike the superseded #2209 *mirror*, this is the source of truth, so it does
**not** swallow errors into safe defaults (contract invariant 7): a real
``sqlite3.DatabaseError`` propagates to the caller, which decides pause/retry.
The WAL ``busy_timeout`` absorbs transient cross-process lock contention.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from app.mission_store.base import (
    TERMINAL_STATES,
    VALID_STATES,
    IngestReport,
    Mission,
    MissionStore,
    RecoverReport,
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS missions (
    id             INTEGER PRIMARY KEY,
    text           TEXT    NOT NULL,
    state          TEXT    NOT NULL CHECK(state IN {VALID_STATES}),
    project        TEXT    NOT NULL DEFAULT 'default',
    sequence       INTEGER NOT NULL DEFAULT 0,
    complexity     TEXT,
    queued_at      TEXT,
    started_at     TEXT,
    completed_at   TEXT,
    recovery_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_missions_state   ON missions(state);
CREATE INDEX IF NOT EXISTS idx_missions_project ON missions(project);
CREATE INDEX IF NOT EXISTS idx_missions_pending ON missions(state, sequence);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Lifecycle markers live in columns, never in ``text``. Strip them on ingest and
# re-render them on export.
_MARKER_RE = re.compile(r"\s*[⏳▶✅❌]\s*\(?[0-9T :\-]*\)?")


def _clean_text(item: str) -> str:
    """Normalize a raw ``missions.md`` item into stored ``text``.

    Drops a leading ``- `` and any lifecycle markers (the timestamps live in
    columns). ``### `` blocks are kept verbatim apart from marker stripping.
    """
    stripped = _MARKER_RE.sub("", item).rstrip()
    lines = stripped.splitlines()
    if lines and lines[0].lstrip().startswith("- "):
        lines[0] = lines[0].lstrip()[2:]
    return "\n".join(lines).strip()


class SqliteMissionStore(MissionStore):
    def __init__(self, instance: str):
        self._db_path = str(Path(instance) / "missions.db")
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def backend_name(self) -> str:
        return "sqlite"

    # ---- connection --------------------------------------------------------

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row_to_mission(row: sqlite3.Row) -> Mission:
        return Mission(
            id=str(row["id"]),
            text=row["text"],
            state=row["state"],
            project=row["project"],
            sequence=row["sequence"],
            complexity=row["complexity"],
            queued_at=row["queued_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _tail_seq(conn) -> int:
        row = conn.execute("SELECT COALESCE(MAX(sequence), 0) AS s FROM missions").fetchone()
        return int(row["s"]) + 1

    @staticmethod
    def _head_seq(conn) -> int:
        row = conn.execute("SELECT COALESCE(MIN(sequence), 0) AS s FROM missions").fetchone()
        return int(row["s"]) - 1

    # ---- write / lifecycle -------------------------------------------------

    def add_pending(self, text: str, *, project: str = "default",
                    complexity: Optional[str] = None, urgent: bool = False) -> Mission:
        with self._connect() as conn:
            return self._insert(conn, text, project=project, complexity=complexity,
                                urgent=urgent)

    def add_pending_many(self, texts: List[str], *, project: str = "default") -> List[Mission]:
        with self._connect() as conn:
            return [self._insert(conn, text, project=project) for text in texts]

    def _insert(self, conn, text: str, *, project: str = "default",
                complexity: Optional[str] = None, urgent: bool = False,
                queued_at: Optional[str] = None) -> Mission:
        seq = self._head_seq(conn) if urgent else self._tail_seq(conn)
        cur = conn.execute(
            "INSERT INTO missions(text, state, project, sequence, complexity, queued_at) "
            "VALUES (?, 'pending', ?, ?, ?, ?)",
            (text, project or "default", seq, complexity, queued_at),
        )
        return self.get_conn(conn, cur.lastrowid)

    @staticmethod
    def get_conn(conn, mission_id) -> Optional[Mission]:
        row = conn.execute("SELECT * FROM missions WHERE id=?", (int(mission_id),)).fetchone()
        return SqliteMissionStore._row_to_mission(row) if row else None

    def claim_next(self, *, projects: Optional[List[str]] = None) -> Optional[Mission]:
        # BEGIN IMMEDIATE takes a write lock up front so two concurrent claimants
        # serialize; the guarded UPDATE is the final safety net.
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            conn.isolation_level = None  # manual transaction control for BEGIN IMMEDIATE
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("BEGIN IMMEDIATE")
            if projects:
                placeholders = ",".join("?" for _ in projects)
                row = conn.execute(
                    f"SELECT * FROM missions WHERE state='pending' AND project IN ({placeholders}) "
                    "ORDER BY sequence ASC LIMIT 1", tuple(projects)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM missions WHERE state='pending' "
                    "ORDER BY sequence ASC LIMIT 1").fetchone()
            if row is None:
                conn.commit()
                return None
            from time import strftime
            now = strftime("%Y-%m-%dT%H:%M")
            cur = conn.execute(
                "UPDATE missions SET state='in_progress', started_at=? "
                "WHERE id=? AND state='pending'", (now, row["id"]))
            if cur.rowcount == 0:
                conn.commit()
                return self.claim_next(projects=projects)  # lost race; retry
            claimed = conn.execute("SELECT * FROM missions WHERE id=?", (row["id"],)).fetchone()
            conn.commit()
            return self._row_to_mission(claimed)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _finalize(self, conn, mission_id, new_state: str, ts_col: str) -> bool:
        from time import strftime
        now = strftime("%Y-%m-%dT%H:%M")
        cur = conn.execute(
            f"UPDATE missions SET state=?, {ts_col}=? "
            "WHERE id=? AND state NOT IN ('done','failed')",
            (new_state, now, int(mission_id)))
        return cur.rowcount > 0

    def complete(self, mission_id: str) -> bool:
        with self._connect() as conn:
            return self._finalize(conn, mission_id, "done", "completed_at")

    def fail(self, mission_id: str) -> bool:
        with self._connect() as conn:
            return self._finalize(conn, mission_id, "failed", "completed_at")

    def requeue(self, mission_id: str) -> bool:
        with self._connect() as conn:
            seq = self._head_seq(conn)
            cur = conn.execute(
                "UPDATE missions SET state='pending', sequence=?, started_at=NULL "
                "WHERE id=? AND state NOT IN ('done','failed')", (seq, int(mission_id)))
            return cur.rowcount > 0

    def set_complexity(self, mission_id: str, complexity: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE missions SET complexity=? WHERE id=?",
                               (complexity, int(mission_id)))
            return cur.rowcount > 0

    # ---- read / query ------------------------------------------------------

    def count_by_state(self, state: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM missions WHERE state=?",
                               (state,)).fetchone()
            return int(row["n"])

    def counts(self) -> dict:
        out = {s: 0 for s in VALID_STATES}
        with self._connect() as conn:
            for row in conn.execute("SELECT state, COUNT(*) AS n FROM missions GROUP BY state"):
                out[row["state"]] = int(row["n"])
        return out

    def list_by_state(self, state: str, *, project: Optional[str] = None,
                      limit: Optional[int] = None) -> List[Mission]:
        # Live states read oldest-first (queue order); terminal states newest-first.
        order = "DESC" if state in TERMINAL_STATES else "ASC"
        sql = "SELECT * FROM missions WHERE state=?"
        params: list = [state]
        if project:
            sql += " AND project=?"
            params.append(project)
        sql += f" ORDER BY sequence {order}, id {order}"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connect() as conn:
            return [self._row_to_mission(r) for r in conn.execute(sql, tuple(params))]

    def get(self, mission_id: str) -> Optional[Mission]:
        with self._connect() as conn:
            return self.get_conn(conn, mission_id)

    def peek_next(self, *, projects: Optional[List[str]] = None) -> Optional[Mission]:
        with self._connect() as conn:
            if projects:
                placeholders = ",".join("?" for _ in projects)
                row = conn.execute(
                    f"SELECT * FROM missions WHERE state='pending' AND project IN ({placeholders}) "
                    "ORDER BY sequence ASC LIMIT 1", tuple(projects)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM missions WHERE state='pending' "
                    "ORDER BY sequence ASC LIMIT 1").fetchone()
            return self._row_to_mission(row) if row else None

    # ---- maintenance -------------------------------------------------------

    def prune_terminal(self, done_keep: int, failed_keep: int) -> int:
        deleted = 0
        with self._connect() as conn:
            for state, keep in (("done", done_keep), ("failed", failed_keep)):
                if keep <= 0:
                    cur = conn.execute("DELETE FROM missions WHERE state=?", (state,))
                else:
                    cur = conn.execute(
                        "DELETE FROM missions WHERE state=? AND id NOT IN "
                        "(SELECT id FROM missions WHERE state=? ORDER BY id DESC LIMIT ?)",
                        (state, state, keep))
                deleted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return deleted

    def is_initialized(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='initialized_at'").fetchone()
            return row is not None

    def ingest_from_file(self, missions_md_path) -> IngestReport:
        from app.missions import (
            extract_complexity_tag,
            extract_project_tag,
            extract_timestamps,
            parse_sections,
        )
        report = IngestReport()
        path = Path(missions_md_path)
        _stamp = ("%Y-%m-%dT%H:%M")
        with self._connect() as conn:
            if path.exists():
                sections = parse_sections(path.read_text())
                seq = 0
                for state in VALID_STATES:
                    for item in sections.get(state, []):
                        key = self._key_source(item)
                        if key is None:
                            report.unparseable.append(item[:120])
                            continue
                        seq += 1
                        ts = extract_timestamps(item)
                        conn.execute(
                            "INSERT INTO missions(text, state, project, sequence, complexity, "
                            "queued_at, started_at, completed_at) VALUES (?,?,?,?,?,?,?,?)",
                            (_clean_text(item), state,
                             extract_project_tag(item) or "default", seq,
                             extract_complexity_tag(item),
                             ts["queued"].strftime(_stamp) if ts["queued"] else None,
                             ts["started"].strftime(_stamp) if ts["started"] else None,
                             ts["completed"].strftime(_stamp) if ts["completed"] else None))
                        report.inserted += 1
                        report.by_state[state] = report.by_state.get(state, 0) + 1
            from time import strftime
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('initialized_at', ?)",
                         (strftime("%Y-%m-%dT%H:%M"),))
        return report

    @staticmethod
    def _key_source(item: str) -> Optional[str]:
        first = next((ln.strip() for ln in item.splitlines() if ln.strip()), "")
        if first.startswith("### "):
            return first[4:].strip()
        if first.startswith("- "):
            return first
        return None

    def export_view(self, missions_md_path) -> None:
        from app.utils import atomic_write
        headers = [("pending", "Pending"), ("in_progress", "In Progress"),
                   ("done", "Done"), ("failed", "Failed")]
        lines = ["# Missions", ""]
        for state, title in headers:
            lines.append(f"## {title}")
            lines.extend(self._render(m) for m in self.list_by_state(state))
            lines.append("")
        atomic_write(Path(missions_md_path), "\n".join(lines).rstrip() + "\n")

    @staticmethod
    def _render(m: Mission) -> str:
        markers = []
        if m.queued_at:
            markers.append(f"⏳({m.queued_at})")
        if m.started_at:
            markers.append(f"▶({m.started_at})")
        if m.completed_at:
            markers.append(("❌" if m.state == "failed" else "✅") + f"({m.completed_at})")
        body = m.text if m.text.lstrip().startswith("### ") else f"- {m.text}"
        return (body + (" " + " ".join(markers) if markers else "")).rstrip()

    def recover_stale(self, *, max_recover: int = 3) -> RecoverReport:
        from time import strftime
        report = RecoverReport()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM missions WHERE state='in_progress'").fetchall()
            for row in rows:
                if row["recovery_count"] >= max_recover:
                    conn.execute(
                        "UPDATE missions SET state='failed', completed_at=? WHERE id=?",
                        (strftime("%Y-%m-%dT%H:%M"), row["id"]))
                    report.escalated.append(row["text"][:120])
                else:
                    seq = self._head_seq(conn)
                    conn.execute(
                        "UPDATE missions SET state='pending', sequence=?, started_at=NULL, "
                        "recovery_count=recovery_count+1 WHERE id=?", (seq, row["id"]))
                    report.requeued += 1
        return report
