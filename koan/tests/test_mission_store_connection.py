"""Tests for the shared SQLite connection bootstrap (issue #2350).

``connect()`` is the single bootstrap used by ``SqliteMissionStore._connect``,
the aux stores' module-level ``_connect``, and ``claim_next``'s
``immediate=True`` path. These tests pin its observable behavior directly so
a future edit to one caller can't silently diverge from the others.
"""

import sqlite3

import pytest

from app.mission_store._connection import connect


def _make_db(tmp_path):
    db = str(tmp_path / "test.db")
    with connect(db) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    return db


def test_connect_sets_row_factory_and_wal(tmp_path):
    db = _make_db(tmp_path)
    with connect(db) as conn:
        conn.execute("INSERT INTO t (v) VALUES ('x')")
        row = conn.execute("SELECT * FROM t").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["v"] == "x"
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


def test_connect_commits_on_success(tmp_path):
    db = _make_db(tmp_path)
    with connect(db) as conn:
        conn.execute("INSERT INTO t (v) VALUES ('a')")
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_connect_rolls_back_on_exception(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(RuntimeError):
        with connect(db) as conn:
            conn.execute("INSERT INTO t (v) VALUES ('b')")
            raise RuntimeError("boom")
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_connect_immediate_takes_write_lock(tmp_path):
    db = _make_db(tmp_path)
    with connect(db, immediate=True) as conn:
        conn.execute("INSERT INTO t (v) VALUES ('locked')")
        # A second connection trying to take the write lock while the first
        # holds it (uncommitted) must fail fast rather than silently
        # interleave — this is what serializes concurrent claim_next callers.
        blocked = sqlite3.connect(db, timeout=0.05)
        try:
            with pytest.raises(sqlite3.OperationalError):
                blocked.execute("BEGIN IMMEDIATE")
        finally:
            blocked.close()
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
