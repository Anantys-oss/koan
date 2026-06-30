"""Tests for the unified artifact schema + migration harness."""

import pytest

from app import artifact_db


# --- Phase 1: registry + DDL idempotency -----------------------------------

def test_registry_covers_all_artifacts():
    assert set(artifact_db.ARTIFACT_SCHEMAS) == {
        "missions", "journal_entries", "memory_entries",
        "outbox_messages", "audit_log",
    }


def test_create_tables_idempotent(tmp_path):
    db = tmp_path / "artifacts.db"
    conn = artifact_db.connect(db)
    assert conn is not None
    artifact_db.create_tables(conn)
    artifact_db.create_tables(conn)  # second call must not raise
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for table in artifact_db.ARTIFACT_SCHEMAS:
        assert table in names
    conn.close()


# --- Phase 2: schema-drift verification ------------------------------------

def test_verify_schema_clean(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    diff = artifact_db.verify_schema(conn, "missions")
    assert diff["in_sync"] is True
    assert diff["missing"] == [] and diff["unexpected"] == []
    conn.close()


def test_verify_schema_detects_drift(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    conn.execute("CREATE TABLE missions (id INTEGER PRIMARY KEY, text TEXT)")
    conn.commit()
    diff = artifact_db.verify_schema(conn, "missions")
    assert diff["in_sync"] is False
    assert "section" in diff["missing"]   # declared but absent in DB
    conn.close()


def test_verify_schema_nonexistent_table_is_none(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    diff = artifact_db.verify_schema(conn, "missions")
    assert diff["in_sync"] is None
    conn.close()


def test_verify_schema_unknown_artifact(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    diff = artifact_db.verify_schema(conn, "not_an_artifact")
    assert diff["in_sync"] is False
    assert "error" in diff
    conn.close()


# --- Phase 3: dual-write (file authoritative) ------------------------------

def test_dual_write_file_authoritative(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    written = {}

    def file_writer(recs):
        written["recs"] = recs

    recs = [{"text": "do X", "section": "pending", "project": "koan"}]
    artifact_db.dual_write(recs, file_writer=file_writer, conn=conn, table="missions")
    assert written["recs"] == recs                      # file path always runs
    rows = conn.execute("SELECT text, section FROM missions").fetchall()
    assert rows == [("do X", "pending")]
    conn.close()


def test_dual_write_survives_db_failure(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    # tables intentionally NOT created -> insert fails, file path must still run
    written = {}
    artifact_db.dual_write([{"text": "x", "section": "pending"}],
                           file_writer=lambda r: written.setdefault("ok", True),
                           conn=conn, table="missions")
    assert written["ok"] is True
    conn.close()


def test_dual_write_propagates_file_writer_error(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)

    def boom(recs):
        raise OSError("disk full")

    with pytest.raises(OSError):
        artifact_db.dual_write([{"text": "x", "section": "pending"}],
                               file_writer=boom, conn=conn, table="missions")
    conn.close()


def test_dual_write_truncates_no_duplicate_accumulation(tmp_path):
    # Rewrite-style artifact: each dual_write is a full file rewrite, so the
    # DB projection must mirror it (replace), not append.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    artifact_db.dual_write([{"text": "a", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn, table="missions")
    artifact_db.dual_write([{"text": "a", "section": "pending"},
                            {"text": "b", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn, table="missions")
    rows = conn.execute("SELECT text FROM missions ORDER BY rowid").fetchall()
    assert [r[0] for r in rows] == ["a", "b"]   # not ["a", "a", "b"]
    conn.close()


def test_dual_write_append_mode_accumulates(tmp_path):
    # Append-only artifacts: each dual_write adds new rows without truncating.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    artifact_db.dual_write([{"text": "a", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn,
                           table="missions", mode="append")
    artifact_db.dual_write([{"text": "b", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn,
                           table="missions", mode="append")
    rows = conn.execute("SELECT text FROM missions ORDER BY rowid").fetchall()
    assert [r[0] for r in rows] == ["a", "b"]   # both kept, not truncated
    conn.close()


def test_dual_write_append_dirty_is_sticky_until_rebuild(tmp_path):
    # An append cannot backfill rows a prior failed append dropped, so a
    # once-dirty append projection must stay dirty (reads keep using the file)
    # across later successful appends — only a replace rebuild heals it.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    artifact_db.dual_write([{"text": "a", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn,
                           table="missions", mode="append")
    conn.execute("ALTER TABLE missions RENAME TO missions_tmp")  # break insert
    artifact_db.dual_write([{"text": "dropped", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn,
                           table="missions", mode="append")
    conn.execute("ALTER TABLE missions_tmp RENAME TO missions")  # restore
    file_recs = [{"text": "a", "section": "pending"},
                 {"text": "dropped", "section": "pending"},
                 {"text": "c", "section": "pending"}]
    # A later *successful* append must NOT clear the dirty flag.
    artifact_db.dual_write([{"text": "c", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn,
                           table="missions", mode="append")
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert out == file_recs                     # served from file, hole not exposed
    # A full replace rebuild heals the projection.
    artifact_db.dual_write(file_recs, file_writer=lambda r: None,
                           conn=conn, table="missions", mode="replace")
    healed = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: [])
    assert [r["text"] for r in healed] == ["a", "dropped", "c"]
    conn.close()


def test_dual_read_falls_back_on_schema_drift(tmp_path):
    # A DB whose live columns drift from the declared set must fall back to the
    # file rather than serve a divergent dict shape — even with a clean (dirty=0)
    # projection, so the schema gate (not the dirty gate) is what forces it.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    artifact_db.dual_write([{"text": "ok", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn,
                           table="missions")             # leaves dirty=0
    # Replace the table with a drifted (column-subset) shape.
    conn.execute("DROP TABLE missions")
    conn.execute("CREATE TABLE missions (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO missions (text) VALUES ('db-only')")
    conn.commit()
    file_recs = [{"text": "from-file", "section": "pending"}]
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert out == file_recs
    conn.close()


def test_dual_write_unknown_mode_raises(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    with pytest.raises(ValueError):
        artifact_db.dual_write([{"text": "x", "section": "pending"}],
                               file_writer=lambda r: None, conn=conn,
                               table="missions", mode="upsert")
    conn.close()


def test_connect_returns_none_on_oserror(tmp_path, monkeypatch):
    # mkdir failure (OSError, not DatabaseError) must still yield None.
    def boom(*a, **k):
        raise PermissionError("read-only fs")

    monkeypatch.setattr(artifact_db.Path, "mkdir", boom)
    assert artifact_db.connect(tmp_path / "sub" / "a.db") is None


def test_dual_write_unpersistable_dirty_closes_conn_for_file_fallback(tmp_path):
    # If both the projection write and the dirty flag cannot be persisted, the
    # connection is closed so later reads fall through to the authoritative file.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    artifact_db.dual_write([{"text": "old", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn, table="missions")
    # Drop both the data table and the meta table so projection AND dirty fail.
    conn.execute("DROP TABLE missions")
    conn.execute("DROP TABLE _artifact_meta")
    conn.commit()
    file_recs = [{"text": "new", "section": "pending"}]
    artifact_db.dual_write(file_recs, file_writer=lambda r: None,
                           conn=conn, table="missions")
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert out == file_recs                     # served from file, not stale DB


def test_dual_write_db_failure_flags_dirty_read_uses_file(tmp_path):
    # First write succeeds; a later projection failure must roll back and flag
    # the projection dirty so reads fall back to the (authoritative) file.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    artifact_db.dual_write([{"text": "old", "section": "pending"}],
                           file_writer=lambda r: None, conn=conn, table="missions")
    # Force the projection to fail by dropping the table after a good write.
    conn.execute("DROP TABLE missions")
    conn.commit()
    file_recs = [{"text": "new", "section": "pending"}]
    artifact_db.dual_write(file_recs, file_writer=lambda r: None,
                           conn=conn, table="missions")
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert out == file_recs                     # served from file, not stale DB
    conn.close()


# --- Phase 4: dual-read with file-stable ordering --------------------------

def test_dual_read_matches_file_order(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    file_recs = [{"text": "a", "section": "pending"},
                 {"text": "b", "section": "pending"},
                 {"text": "c", "section": "done"}]
    artifact_db.dual_write(file_recs, file_writer=lambda r: None,
                           conn=conn, table="missions")
    from_db = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert [(r["text"], r["section"]) for r in from_db] == \
           [(r["text"], r["section"]) for r in file_recs]
    conn.close()


def test_dual_read_shape_matches_file_no_surrogate_id(tmp_path):
    # DB-served read must yield the same dict shape as the file (no synthetic
    # `id` PK key), so a consumer can't tell which source served the read.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    file_recs = [{"text": "a", "section": "pending", "project": "koan",
                  "queued_ts": None, "started_ts": None, "completed_ts": None}]
    artifact_db.dual_write(file_recs, file_writer=lambda r: None,
                           conn=conn, table="missions")
    from_db = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert from_db == file_recs          # whole-dict equality, "id" excluded
    assert "id" not in from_db[0]
    conn.close()


def test_dual_read_falls_back_when_db_empty(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    file_recs = [{"text": "only-in-file", "section": "pending"}]
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert out == file_recs
    conn.close()


def test_dual_read_rejects_unknown_order_key(tmp_path):
    # An order_key not in the schema must not be interpolated into SQL; it
    # falls back to rowid ordering instead of raising / injecting.
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    file_recs = [{"text": "a", "section": "pending"},
                 {"text": "b", "section": "pending"}]
    artifact_db.dual_write(file_recs, file_writer=lambda r: None,
                           conn=conn, table="missions")
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: [],
        order_key="text; DROP TABLE missions")
    assert [r["text"] for r in out] == ["a", "b"]   # rowid order, table intact
    assert conn.execute("SELECT count(*) FROM missions").fetchone()[0] == 2
    conn.close()


# --- Phase 5: compatibility fixture + graceful degradation -----------------

@pytest.fixture
def populated_artifact(tmp_path):
    """Populate both file and DB formats; return (conn, records, file_reader)."""
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    records = [
        {"text": "ship harness", "section": "pending", "project": "koan"},
        {"text": "write docs", "section": "in_progress", "project": "koan"},
    ]

    def file_reader():
        return records

    artifact_db.dual_write(records, file_writer=lambda r: None,
                           conn=conn, table="missions")
    yield conn, records, file_reader
    conn.close()


def test_dual_read_consistency(populated_artifact):
    conn, records, file_reader = populated_artifact
    from_db = artifact_db.read_from_db_or_file(conn, "missions", file_reader=file_reader)
    assert [r["text"] for r in from_db] == [r["text"] for r in records]
    assert [r["section"] for r in from_db] == [r["section"] for r in records]


def test_graceful_when_db_unavailable():
    # conn=None simulates sqlite unavailable — must still serve the file.
    recs = [{"text": "x", "section": "pending"}]
    out = artifact_db.read_from_db_or_file(None, "missions", file_reader=lambda: recs)
    assert out == recs
