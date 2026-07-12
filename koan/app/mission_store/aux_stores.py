"""Sibling stores for the non-lifecycle sub-populations that ``missions.md`` used
to hold: the ``## CI`` retry queue, the ``## Ideas`` list, and the quarantine log.

They live as tables in the same ``instance/missions.db`` as the missions table
(decision 2026-07-09, see ``specs/004-mission-store/data-model.md``) so the file
is fully retired. They are deliberately NOT part of the ``MissionStore`` lifecycle
port — they are separate concerns with their own callers — but share the store's
connection handling and are ingested/exported alongside the missions.

Each method mirrors the semantics of the ``app.missions`` function it replaces:
``add_ci_item``/``remove_ci_item``/``get_ci_items``/``update_ci_item_attempt`` and
``parse_ideas``/``insert_idea``/``delete_idea``/``promote_idea`` and
``quarantine_mission``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from time import strftime
from typing import List, Optional

logger = logging.getLogger(__name__)

_CI_SCHEMA = """
CREATE TABLE IF NOT EXISTS ci_queue (
    id           INTEGER PRIMARY KEY,
    project      TEXT NOT NULL DEFAULT '',
    pr_url       TEXT NOT NULL,
    branch       TEXT,
    full_repo    TEXT,
    queued       TEXT,
    attempt      INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    sequence     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ci_pr ON ci_queue(pr_url);
"""

_IDEAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id       INTEGER PRIMARY KEY,
    text     TEXT NOT NULL,
    project  TEXT NOT NULL DEFAULT 'default',
    added_at TEXT,
    sequence INTEGER NOT NULL DEFAULT 0
);
"""

_QUARANTINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS quarantine (
    id       INTEGER PRIMARY KEY,
    text     TEXT NOT NULL,
    reason   TEXT,
    source   TEXT,
    added_at TEXT
);
"""

_QUARANTINE_KEEP = 200  # cap rows (mirrors _enforce_quarantine_cap's byte cap intent)

_OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS mission_outcomes (
    id              INTEGER PRIMARY KEY,
    key             TEXT NOT NULL,
    status          TEXT NOT NULL,
    reason_category TEXT,
    detail          TEXT,
    recorded_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_key ON mission_outcomes(key);
"""


@contextmanager
def _connect(db_path: str):
    conn = sqlite3.connect(db_path, timeout=5)
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


class CiQueueStore:
    """The ``## CI`` monitoring queue (replaces the CI-section helpers)."""

    def __init__(self, instance: str):
        self._db = str(Path(instance) / "missions.db")
        with _connect(self._db) as conn:
            conn.executescript(_CI_SCHEMA)

    def add_item(self, project: str, pr_url: str, pr_number: str, branch: str,
                 full_repo: str, max_attempts: int) -> None:
        """Add or refresh a CI entry; dedup by pr_url, resetting attempts to 0."""
        queued = strftime("%Y-%m-%dT%H:%M")
        with _connect(self._db) as conn:
            conn.execute("DELETE FROM ci_queue WHERE pr_url=?", (pr_url,))
            seq = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS s FROM ci_queue").fetchone()["s"]
            conn.execute(
                "INSERT INTO ci_queue(project, pr_url, branch, full_repo, queued, "
                "attempt, max_attempts, sequence) VALUES (?,?,?,?,?,0,?,?)",
                (project or "", pr_url, branch, full_repo, queued, max_attempts, seq))

    def remove_item(self, pr_url: str) -> None:
        with _connect(self._db) as conn:
            conn.execute("DELETE FROM ci_queue WHERE pr_url=?", (pr_url,))

    def update_attempt(self, pr_url: str) -> None:
        """Increment attempt for pr_url if still below max_attempts."""
        with _connect(self._db) as conn:
            conn.execute(
                "UPDATE ci_queue SET attempt=attempt+1 "
                "WHERE pr_url=? AND attempt < max_attempts", (pr_url,))

    def get_items(self) -> List[dict]:
        """Return CI entries as dicts matching ``missions.get_ci_items`` keys."""
        with _connect(self._db) as conn:
            rows = conn.execute("SELECT * FROM ci_queue ORDER BY sequence").fetchall()
        return [self._to_item(r) for r in rows]

    @staticmethod
    def _to_item(r: sqlite3.Row) -> dict:
        m = re.search(r"/pull/(\d+)", r["pr_url"] or "")
        pr_number = m.group(1) if m else ""
        return {
            "project": r["project"], "pr_url": r["pr_url"], "pr_number": pr_number,
            "branch": r["branch"] or "", "full_repo": r["full_repo"] or "",
            "queued": r["queued"] or "", "attempt": r["attempt"],
            "max_attempts": r["max_attempts"],
            "raw_line": CiQueueStore._render_row(r),
        }

    @staticmethod
    def _render_row(r: sqlite3.Row) -> str:
        tag = f"[project:{r['project']}] " if r["project"] else ""
        return (f"- {tag}{r['pr_url']} branch:{r['branch'] or ''} "
                f"repo:{r['full_repo'] or ''} queued:{r['queued'] or ''} "
                f"(attempt {r['attempt']}/{r['max_attempts']})")

    def render_lines(self) -> List[str]:
        with _connect(self._db) as conn:
            rows = conn.execute("SELECT * FROM ci_queue ORDER BY sequence").fetchall()
        return [self._render_row(r) for r in rows]

    def ingest_items(self, items: List[dict]) -> int:
        """Bulk-load parsed CI items (from the one-time missions.md ingest)."""
        with _connect(self._db) as conn:
            seq = 0
            for it in items:
                seq += 1
                conn.execute(
                    "INSERT INTO ci_queue(project, pr_url, branch, full_repo, queued, "
                    "attempt, max_attempts, sequence) VALUES (?,?,?,?,?,?,?,?)",
                    (it.get("project", ""), it["pr_url"], it.get("branch", ""),
                     it.get("full_repo", ""), it.get("queued", ""),
                     int(it.get("attempt", 0)), int(it.get("max_attempts", 5)), seq))
        return len(items)

    def reconcile_from_content(self, content: str) -> None:
        """Rebuild the CI queue from a ``missions.md`` content string (S8 flip)."""
        from app import missions as _m
        items = _m.get_ci_items(content)
        with _connect(self._db) as conn:
            conn.execute("DELETE FROM ci_queue")
        self.ingest_items(items)


class IdeaStore:
    """The ``## Ideas`` list (replaces parse_ideas/insert_idea/delete_idea)."""

    def __init__(self, instance: str):
        self._db = str(Path(instance) / "missions.db")
        with _connect(self._db) as conn:
            conn.executescript(_IDEAS_SCHEMA)

    def list(self) -> List[str]:
        """Idea texts in insertion order (each starting with ``- ``, as
        ``parse_ideas`` returned them)."""
        with _connect(self._db) as conn:
            rows = conn.execute(
                "SELECT text FROM ideas ORDER BY sequence, id").fetchall()
        return [self._as_line(r["text"]) for r in rows]

    @staticmethod
    def _as_line(text: str) -> str:
        return text if text.lstrip().startswith("- ") else f"- {text}"

    def add(self, entry: str, *, project: str = "default") -> None:
        with _connect(self._db) as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 AS s FROM ideas").fetchone()["s"]
            conn.execute(
                "INSERT INTO ideas(text, project, added_at, sequence) VALUES (?,?,?,?)",
                (entry, project or "default", strftime("%Y-%m-%dT%H:%M"), seq))

    def delete(self, index: int) -> Optional[str]:
        """Delete by 1-based index (matching ``delete_idea``); return the text."""
        with _connect(self._db) as conn:
            rows = conn.execute("SELECT id, text FROM ideas ORDER BY sequence, id").fetchall()
            if index < 1 or index > len(rows):
                return None
            row = rows[index - 1]
            conn.execute("DELETE FROM ideas WHERE id=?", (row["id"],))
            return self._as_line(row["text"])

    def delete_all(self) -> List[str]:
        with _connect(self._db) as conn:
            rows = conn.execute("SELECT text FROM ideas ORDER BY sequence, id").fetchall()
            conn.execute("DELETE FROM ideas")
        return [self._as_line(r["text"]) for r in rows]

    def render_lines(self) -> List[str]:
        return self.list()

    def ingest_items(self, texts: List[str]) -> int:
        with _connect(self._db) as conn:
            seq = 0
            for text in texts:
                seq += 1
                conn.execute(
                    "INSERT INTO ideas(text, added_at, sequence) VALUES (?,?,?)",
                    (text, strftime("%Y-%m-%dT%H:%M"), seq))
        return len(texts)

    def reconcile_from_content(self, content: str) -> None:
        """Rebuild the Ideas list from a ``missions.md`` content string (S8 flip)."""
        from app import missions as _m
        texts = _m.parse_ideas(content)
        with _connect(self._db) as conn:
            conn.execute("DELETE FROM ideas")
        self.ingest_items(texts)


class QuarantineStore:
    """Append-only quarantine log (replaces ``quarantine_mission`` + its file)."""

    def __init__(self, instance: str):
        self._db = str(Path(instance) / "missions.db")
        with _connect(self._db) as conn:
            conn.executescript(_QUARANTINE_SCHEMA)

    def add(self, text: str, reason: str, source: str = "unknown") -> bool:
        try:
            with _connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO quarantine(text, reason, source, added_at) VALUES (?,?,?,?)",
                    (text[:500], reason, source, strftime("%Y-%m-%d %H:%M")))
                # Cap: keep the most recent _QUARANTINE_KEEP rows.
                conn.execute(
                    "DELETE FROM quarantine WHERE id NOT IN "
                    "(SELECT id FROM quarantine ORDER BY id DESC LIMIT ?)",
                    (_QUARANTINE_KEEP,))
            return True
        except sqlite3.DatabaseError as e:
            # Quarantine isolates poison-pill (e.g. prompt-injection) missions;
            # a silently-lost record defeats its purpose. Log loudly, then signal
            # failure to the caller.
            logger.error("quarantine add failed (mission NOT quarantined): %s", e)
            return False

    def list(self) -> List[dict]:
        with _connect(self._db) as conn:
            rows = conn.execute(
                "SELECT * FROM quarantine ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def render_lines(self) -> List[str]:
        with _connect(self._db) as conn:
            rows = conn.execute("SELECT * FROM quarantine ORDER BY id").fetchall()
        return [f"- \U0001f6e1️ [{r['added_at']}] ({r['source']}) "
                f"{r['reason']}: {r['text']}" for r in rows]


class OutcomeStore:
    """Append-only authoritative terminal-outcome log.

    Keyed by ``canonical_mission_key`` so it survives requeue/recovery and the
    per-write ``reconcile_all`` DELETE+re-INSERT of the missions table. Never
    part of the ``missions.md`` export — a pure audit trail read by the REST API.
    """

    KEEP = 500  # cap rows; newest retained

    def __init__(self, instance: str):
        self._db = str(Path(instance) / "missions.db")
        with _connect(self._db) as conn:
            conn.executescript(_OUTCOME_SCHEMA)

    @staticmethod
    def _key(text: str) -> str:
        from app.missions import canonical_mission_key
        return canonical_mission_key(text)

    def record(self, text: str, status: str, reason_category: Optional[str] = None,
               detail: Optional[str] = None) -> bool:
        """Append a terminal outcome; caps the log to the most recent KEEP rows."""
        try:
            with _connect(self._db) as conn:
                conn.execute(
                    "INSERT INTO mission_outcomes(key, status, reason_category, detail, recorded_at) "
                    "VALUES (?,?,?,?,?)",
                    (self._key(text), status, reason_category,
                     (detail or "")[:500] or None, strftime("%Y-%m-%d %H:%M")))
                conn.execute(
                    "DELETE FROM mission_outcomes WHERE id NOT IN "
                    "(SELECT id FROM mission_outcomes ORDER BY id DESC LIMIT ?)",
                    (self.KEEP,))
            return True
        except sqlite3.DatabaseError as e:
            logger.error("outcome record failed for %r: %s", text[:60], e)
            return False

    def latest(self, text: str) -> Optional[dict]:
        """Return the newest recorded outcome for a mission key, or None."""
        with _connect(self._db) as conn:
            row = conn.execute(
                "SELECT status, reason_category, detail, recorded_at "
                "FROM mission_outcomes WHERE key=? ORDER BY id DESC LIMIT 1",
                (self._key(text),)).fetchone()
        return dict(row) if row else None
