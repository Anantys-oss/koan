"""Shared SQLite connection bootstrap for the mission store and its sibling stores.

Every table in ``instance/missions.db`` (missions, CI queue, ideas, quarantine,
mission_outcomes) opens the database the same way: row factory, WAL journal
mode, a 5s busy timeout to absorb cross-process lock contention, and
commit-on-success / rollback-on-exception. ``claim_next`` additionally needs
a write lock taken *before* it reads (``BEGIN IMMEDIATE``) so two concurrent
claimants serialize instead of racing on a plain read-then-update;
``immediate=True`` selects that mode instead of the default WAL bootstrap.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager


@contextmanager
def connect(db_path: str, *, immediate: bool = False):
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        if immediate:
            # Manual transaction control so BEGIN IMMEDIATE takes the write
            # lock up front, before the caller reads.
            conn.isolation_level = None
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
