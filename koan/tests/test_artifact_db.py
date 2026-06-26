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


def test_dual_read_falls_back_when_db_empty(tmp_path):
    conn = artifact_db.connect(tmp_path / "a.db")
    artifact_db.create_tables(conn)
    file_recs = [{"text": "only-in-file", "section": "pending"}]
    out = artifact_db.read_from_db_or_file(
        conn, "missions", file_reader=lambda: file_recs)
    assert out == file_recs
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
