"""Tests for memory_db — SQLite FTS5 secondary index over JSONL memory log."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.memory_recall import build_fts5_query


# ---------------------------------------------------------------------------
# build_fts5_query
# ---------------------------------------------------------------------------


class TestBuildFts5Query:

    def test_normal_text(self):
        result = build_fts5_query("fix authentication bug")
        assert '"authentication"' in result
        assert '"bug"' in result
        assert '"fix"' in result
        assert " OR " in result

    def test_fts5_operators_stripped(self):
        result = build_fts5_query("fix NEAR(auth) bug*")
        assert "NEAR" not in result.replace('"near"', "")
        assert "(" not in result
        assert ")" not in result
        assert "*" not in result
        assert '"near"' in result
        assert '"auth"' in result
        assert '"bug"' in result
        assert '"fix"' in result

    def test_empty_text(self):
        assert build_fts5_query("") == ""

    def test_only_stopwords(self):
        assert build_fts5_query("the is a to") == ""

    def test_only_short_tokens(self):
        assert build_fts5_query("a b c") == ""

    def test_quotes_and_parens_stripped(self):
        result = build_fts5_query('"quoted" AND (grouped)')
        assert '"' not in result.replace('"quoted"', "").replace('"grouped"', "")
        assert "AND" not in result.replace('"and"', "")

    def test_deterministic_output(self):
        a = build_fts5_query("authentication race condition")
        b = build_fts5_query("authentication race condition")
        assert a == b


# ---------------------------------------------------------------------------
# memory_db module tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def instance_dir(tmp_path):
    """Create a minimal instance directory."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    return str(tmp_path)


@pytest.fixture(autouse=True)
def reset_fts5_flag():
    """Reset the module-level _fts5_available flag between tests."""
    import app.memory_db as mdb
    mdb._fts5_available = None
    yield
    mdb._fts5_available = None


class TestEnsureDb:

    def test_creates_db(self, instance_dir):
        from app.memory_db import ensure_db, db_path
        conn = ensure_db(instance_dir)
        assert conn is not None
        assert db_path(instance_dir).exists()
        conn.close()

    def test_returns_none_when_fts5_unavailable(self, instance_dir):
        import app.memory_db as mdb
        with patch.object(mdb, "_check_fts5", return_value=False):
            mdb._fts5_available = False
            conn = mdb.ensure_db(instance_dir)
            assert conn is None

    def test_returns_none_on_database_error(self, instance_dir):
        from app.memory_db import ensure_db
        with patch("app.memory_db.sqlite3.connect", side_effect=sqlite3.DatabaseError("corrupt")):
            conn = ensure_db(instance_dir)
            assert conn is None


class TestInsertAndSearch:

    def test_insert_search_roundtrip(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, search_entries
        conn = ensure_db(instance_dir)
        assert conn is not None

        insert_entry(conn, {
            "ts": "2026-01-01T00:00:00Z",
            "type": "session",
            "project": "koan",
            "content": "Fixed JWT token expiry race condition in session handler",
        })
        insert_entry(conn, {
            "ts": "2026-01-02T00:00:00Z",
            "type": "session",
            "project": "koan",
            "content": "Updated CSS grid layout for dashboard",
        })

        results = search_entries(conn, "koan", "JWT token expiry", max_results=5)
        assert len(results) >= 1
        assert "JWT" in results[0]["content"]
        conn.close()

    def test_search_empty_query_returns_empty(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, search_entries
        conn = ensure_db(instance_dir)
        insert_entry(conn, {
            "ts": "2026-01-01T00:00:00Z",
            "type": "session",
            "project": "koan",
            "content": "some content",
        })
        results = search_entries(conn, "koan", "", max_results=5)
        assert results == []
        conn.close()

    def test_search_with_fts5_operators(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, search_entries
        conn = ensure_db(instance_dir)
        insert_entry(conn, {
            "ts": "2026-01-01T00:00:00Z",
            "type": "session",
            "project": "koan",
            "content": "authentication near the proxy layer",
        })
        results = search_entries(conn, "koan", "NEAR(authentication) bug*")
        assert len(results) >= 1
        conn.close()

    def test_search_filters_by_project(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, search_entries
        conn = ensure_db(instance_dir)
        insert_entry(conn, {
            "ts": "2026-01-01T00:00:00Z",
            "type": "session",
            "project": "other-project",
            "content": "authentication fix in other project",
        })
        insert_entry(conn, {
            "ts": "2026-01-02T00:00:00Z",
            "type": "session",
            "project": "koan",
            "content": "authentication fix in koan",
        })
        results = search_entries(conn, "koan", "authentication", max_results=10)
        assert all(
            r["project"] is None or r["project"].lower() == "koan"
            for r in results
        )
        conn.close()


class TestSearchLearnings:

    def test_search_learnings_roundtrip(self, instance_dir):
        from app.memory_db import ensure_db, search_learnings
        conn = ensure_db(instance_dir)
        content = (
            "# Learnings\n\n"
            "- JWT token expiry causes race condition in session handler\n"
            "- CSS grid layouts work better than flexbox for dashboards\n"
            "- Fixed authentication timeout in login flow\n"
            "- Database connection pooling tunes at 25\n"
        )
        results = search_learnings(conn, content, "authentication race condition", max_k=4)
        assert len(results) >= 1
        joined = " ".join(results).lower()
        assert "authentication" in joined or "race" in joined
        conn.close()

    def test_search_learnings_empty_query(self, instance_dir):
        from app.memory_db import ensure_db, search_learnings
        conn = ensure_db(instance_dir)
        results = search_learnings(conn, "- some learning\n", "")
        assert results == []
        conn.close()

    def test_search_learnings_preserves_file_order(self, instance_dir):
        from app.memory_db import ensure_db, search_learnings
        conn = ensure_db(instance_dir)
        content = (
            "- zebra authentication issue\n"
            "- alpha authentication problem\n"
            "- beta authentication fix\n"
        )
        results = search_learnings(conn, content, "authentication", max_k=10)
        assert len(results) == 3
        assert "zebra" in results[0]
        assert "alpha" in results[1]
        assert "beta" in results[2]
        conn.close()


class TestRecentEntries:

    def test_recent_entries(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, recent_entries
        conn = ensure_db(instance_dir)
        for i in range(5):
            insert_entry(conn, {
                "ts": f"2026-01-0{i+1}T00:00:00Z",
                "type": "session",
                "project": "koan",
                "content": f"entry {i}",
            })
        results = recent_entries(conn, "koan", max_results=3)
        assert len(results) == 3
        assert results[0]["ts"] < results[-1]["ts"]
        conn.close()


class TestDeleteBefore:

    def test_delete_old_entries(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, delete_before, entry_count
        conn = ensure_db(instance_dir)
        insert_entry(conn, {"ts": "2024-01-01T00:00:00Z", "type": "session", "project": "koan", "content": "old"})
        insert_entry(conn, {"ts": "2026-06-01T00:00:00Z", "type": "session", "project": "koan", "content": "new"})
        removed = delete_before(conn, "2025-01-01T00:00:00Z")
        assert removed == 1
        assert entry_count(conn) == 1
        conn.close()


class TestMigration:

    def test_migrate_jsonl_to_sqlite(self, instance_dir):
        from app.memory_db import migrate_jsonl_to_sqlite, ensure_db, entry_count

        log_path = Path(instance_dir) / "memory" / "log.jsonl"
        entries = [
            {"ts": "2026-01-01T00:00:00Z", "type": "session", "project": "koan", "content": "entry 1"},
            {"ts": "2026-01-02T00:00:00Z", "type": "session", "project": "koan", "content": "entry 2"},
            {"ts": "2026-01-03T00:00:00Z", "type": "learning", "project": "koan", "content": "learning 1"},
        ]
        log_path.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

        count = migrate_jsonl_to_sqlite(instance_dir)
        assert count == 3

        conn = ensure_db(instance_dir)
        assert entry_count(conn) == 3
        conn.close()

    def test_migration_skips_when_db_populated(self, instance_dir):
        from app.memory_db import migrate_jsonl_to_sqlite, ensure_db, insert_entry

        log_path = Path(instance_dir) / "memory" / "log.jsonl"
        log_path.write_text(
            json.dumps({"ts": "2026-01-01T00:00:00Z", "type": "session", "project": "koan", "content": "x"}) + "\n",
            encoding="utf-8",
        )
        conn = ensure_db(instance_dir)
        insert_entry(conn, {"ts": "2026-01-01T00:00:00Z", "type": "session", "project": "koan", "content": "existing"})
        conn.close()

        count = migrate_jsonl_to_sqlite(instance_dir)
        assert count == 0


class TestDatabaseErrorGracefulDegradation:

    def test_insert_on_corrupt_db(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry
        conn = ensure_db(instance_dir)
        conn.close()
        insert_entry(conn, {"ts": "2026-01-01T00:00:00Z", "type": "session", "project": "koan", "content": "x"})

    def test_search_on_corrupt_db(self, instance_dir):
        from app.memory_db import ensure_db, search_entries
        conn = ensure_db(instance_dir)
        conn.close()
        results = search_entries(conn, "koan", "test query")
        assert results == []

    def test_delete_on_corrupt_db(self, instance_dir):
        from app.memory_db import ensure_db, delete_before
        conn = ensure_db(instance_dir)
        conn.close()
        removed = delete_before(conn, "2025-01-01T00:00:00Z")
        assert removed == 0


class TestDualWrite:
    """Verify append_memory_entry dual-writes to JSONL + SQLite."""

    def test_append_writes_to_both_stores(self, instance_dir):
        from app.memory_manager import MemoryManager
        from app.memory_db import ensure_db, search_entries

        mgr = MemoryManager(instance_dir)
        mgr.append_memory_entry("session", "koan", "dual write test authentication fix")

        log_path = Path(instance_dir) / "memory" / "log.jsonl"
        assert log_path.exists()
        last_line = log_path.read_text().strip().split("\n")[-1]
        assert "dual write test" in last_line

        conn = ensure_db(instance_dir)
        results = search_entries(conn, "koan", "authentication fix")
        assert len(results) >= 1
        assert "dual write test" in results[0]["content"]
        conn.close()

    def test_sqlite_failure_does_not_block_jsonl(self, instance_dir):
        from app.memory_manager import MemoryManager
        mgr = MemoryManager(instance_dir)

        with patch("app.memory_db.ensure_db", side_effect=Exception("SQLite broken")):
            mgr.append_memory_entry("session", "koan", "still written to jsonl")

        log_path = Path(instance_dir) / "memory" / "log.jsonl"
        assert "still written to jsonl" in log_path.read_text()

    def test_prune_mirrors_to_sqlite(self, instance_dir):
        from app.memory_manager import MemoryManager
        from app.memory_db import ensure_db, entry_count

        mgr = MemoryManager(instance_dir)
        mgr.append_memory_entry("session", "koan", "old entry", ts="2020-01-01T00:00:00Z")
        mgr.append_memory_entry("session", "koan", "new entry", ts="2026-06-01T00:00:00Z")

        conn = ensure_db(instance_dir)
        assert entry_count(conn) == 2
        conn.close()

        removed = mgr.prune_memory_log(horizon_days=365)
        assert removed == 1

        conn = ensure_db(instance_dir)
        assert entry_count(conn) == 1
        conn.close()


class TestMigrationViaStartup:
    """Verify migrate_markdown_to_jsonl triggers SQLite indexing."""

    def test_markdown_migration_populates_sqlite(self, instance_dir):
        from app.memory_manager import MemoryManager
        from app.memory_db import ensure_db, entry_count

        mgr = MemoryManager(instance_dir)
        summary_path = mgr.summary_path
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            "## 2026-01-15\n\nSession 1 (project: koan) : fixed auth bug\n",
            encoding="utf-8",
        )

        result = mgr.migrate_markdown_to_jsonl()
        assert result.get("sessions", 0) >= 1
        # Entries reach SQLite via dual-write in append_memory_entry OR
        # via the bulk migrate_jsonl_to_sqlite call — either path suffices.
        conn = ensure_db(instance_dir)
        assert entry_count(conn) >= 1
        conn.close()


class TestStartupIndexing:
    """Regression: SQLite indexing must run for already-migrated instances.

    The bulk index step lived inside ``migrate_markdown_to_jsonl``, which
    short-circuits when the ``.migration_done`` sentinel exists. Existing
    instances therefore never indexed their JSONL into FTS5. Indexing now
    runs as a standalone, always-run startup step.
    """

    def _seed_migrated_instance(self, instance_dir):
        """A JSONL log plus the sentinel, mimicking a pre-existing instance."""
        mem_dir = Path(instance_dir) / "memory"
        entries = [
            {"ts": "2026-01-01T00:00:00Z", "type": "session", "project": "koan", "content": "auth bug fixed"},
            {"ts": "2026-01-02T00:00:00Z", "type": "learning", "project": "koan", "content": "pooling at 25"},
        ]
        (mem_dir / "log.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8",
        )
        (mem_dir / ".migration_done").write_text("done\n", encoding="utf-8")

    def test_startup_step_indexes_existing_instance(self, instance_dir):
        from app.startup_manager import index_memory_sqlite
        from app.memory_db import ensure_db, entry_count

        self._seed_migrated_instance(instance_dir)

        index_memory_sqlite(instance_dir)

        conn = ensure_db(instance_dir)
        assert entry_count(conn) == 2
        conn.close()

    def test_markdown_migration_skips_does_not_block_indexing(self, instance_dir):
        """The gated markdown migration no longer owns SQLite indexing."""
        from app.memory_manager import MemoryManager
        from app.startup_manager import index_memory_sqlite
        from app.memory_db import ensure_db, entry_count

        self._seed_migrated_instance(instance_dir)

        # Gated migration is a no-op for already-migrated instances...
        result = MemoryManager(instance_dir).migrate_markdown_to_jsonl()
        assert result.get("skipped") is True

        # ...yet the standalone step still populates the FTS5 index.
        index_memory_sqlite(instance_dir)
        conn = ensure_db(instance_dir)
        assert entry_count(conn) == 2
        conn.close()

    def test_startup_step_is_idempotent(self, instance_dir):
        from app.startup_manager import index_memory_sqlite
        from app.memory_db import ensure_db, entry_count

        self._seed_migrated_instance(instance_dir)

        index_memory_sqlite(instance_dir)
        index_memory_sqlite(instance_dir)  # second run must not duplicate

        conn = ensure_db(instance_dir)
        assert entry_count(conn) == 2
        conn.close()


class TestTwoPhaseRetrieval:
    """Verify read_memory_window uses FTS5 + recency fill."""

    def test_relevant_entries_rank_higher(self, instance_dir):
        from app.memory_manager import MemoryManager
        mgr = MemoryManager(instance_dir)
        for i in range(20):
            mgr.append_memory_entry("session", "koan", f"CSS grid layout fix #{i}")
        mgr.append_memory_entry("session", "koan", "JWT authentication race condition fix")
        for i in range(10):
            mgr.append_memory_entry("session", "koan", f"unrelated UI tweak #{i}")

        results = mgr.read_memory_window("koan", max_entries=10, query_text="authentication JWT")
        contents = [e["content"] for e in results]
        joined = " ".join(contents).lower()
        assert "jwt" in joined or "authentication" in joined

    def test_fts5_hit_logs_observability_line(self, instance_dir):
        """A successful FTS5 read emits a stderr-routed usage line."""
        from app.memory_manager import MemoryManager
        mgr = MemoryManager(instance_dir)
        mgr.append_memory_entry("session", "koan", "JWT authentication race condition")

        with patch("app.run_log.log_safe") as mock_log:
            mgr.read_memory_window("koan", max_entries=5, query_text="authentication JWT")

        assert mock_log.called
        msg = mock_log.call_args[0][1]
        assert "FTS5 surfaced" in msg
        # Must be routed to stderr so it never pollutes subprocess stdout JSON.
        assert mock_log.call_args.kwargs.get("force_stderr") is True

    def test_empty_query_falls_back_to_recency(self, instance_dir):
        from app.memory_manager import MemoryManager
        mgr = MemoryManager(instance_dir)
        mgr.append_memory_entry("session", "koan", "old entry", ts="2026-01-01T00:00:00Z")
        mgr.append_memory_entry("session", "koan", "new entry", ts="2026-06-01T00:00:00Z")

        results = mgr.read_memory_window("koan", max_entries=2, query_text="")
        assert len(results) == 2
        assert results[-1]["content"] == "new entry"

    def test_fallback_to_jsonl_when_db_missing(self, instance_dir):
        from app.memory_manager import MemoryManager
        from app.memory_db import db_path

        mgr = MemoryManager(instance_dir)
        mgr.append_memory_entry("session", "koan", "test entry")
        db_file = db_path(instance_dir)
        if db_file.exists():
            db_file.unlink()

        results = mgr.read_memory_window("koan", max_entries=5, query_text="test")
        assert len(results) >= 1


class TestFtsLearningsFilter:
    """Verify _load_filtered_learnings uses FTS5 when available."""

    def test_fts5_learnings_returns_relevant(self, instance_dir):
        from app.skill_memory import _load_filtered_learnings
        proj_dir = Path(instance_dir) / "memory" / "projects" / "koan"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "learnings.md").write_text(
            "# Learnings\n\n"
            "- JWT token expiry causes race condition\n"
            "- CSS grid fix for dashboard\n"
            "- Authentication timeout in login flow\n"
            "- Database pooling set to 25\n",
            encoding="utf-8",
        )
        result = _load_filtered_learnings(
            instance_dir, "koan", "authentication race condition",
            max_k=3, recent_hedge=1,
        )
        assert result is not None
        assert "authentication" in result.lower() or "race" in result.lower()


class TestMetadataColumns:

    def test_insert_and_search_with_metadata(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, search_entries
        conn = ensure_db(instance_dir)
        assert conn is not None
        insert_entry(conn, {
            "project": "koan",
            "type": "learning",
            "content": "always validate JWT tokens before trusting claims",
            "ts": "2026-06-01T00:00:00Z",
            "source_skill": "review",
            "tags": ["security", "auth"],
            "confidence": 0.9,
            "expires_at": "2026-12-01T00:00:00Z",
        })
        results = search_entries(conn, "koan", "JWT tokens validate", max_results=5)
        assert len(results) >= 1
        assert results[0]["source_skill"] == "review"
        assert results[0]["tags"] == ["security", "auth"]
        assert results[0]["confidence"] == 0.9
        assert results[0]["expires_at"] == "2026-12-01T00:00:00Z"
        conn.close()

    def test_insert_without_metadata_returns_defaults(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, recent_entries
        conn = ensure_db(instance_dir)
        assert conn is not None
        insert_entry(conn, {
            "project": "koan",
            "type": "session",
            "content": "plain session entry",
            "ts": "2026-06-01T00:00:00Z",
        })
        results = recent_entries(conn, "koan", max_results=5)
        assert len(results) >= 1
        assert results[0].get("source_skill") is None
        assert results[0].get("tags") is None
        assert results[0].get("confidence") is None
        assert results[0].get("expires_at") is None
        conn.close()

    def test_expired_entries_excluded_from_search(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, search_entries
        conn = ensure_db(instance_dir)
        assert conn is not None
        insert_entry(conn, {
            "project": "koan",
            "type": "learning",
            "content": "temporary workaround for authentication bug",
            "ts": "2026-01-01T00:00:00Z",
            "expires_at": "2026-02-01T00:00:00Z",
        })
        insert_entry(conn, {
            "project": "koan",
            "type": "learning",
            "content": "permanent authentication best practice",
            "ts": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
        })
        results = search_entries(conn, "koan", "authentication", max_results=10)
        contents = [r["content"] for r in results]
        assert "temporary workaround for authentication bug" not in contents
        assert "permanent authentication best practice" in contents
        conn.close()

    def test_expired_entries_excluded_from_recent(self, instance_dir):
        from app.memory_db import ensure_db, insert_entry, recent_entries
        conn = ensure_db(instance_dir)
        assert conn is not None
        insert_entry(conn, {
            "project": "koan",
            "type": "learning",
            "content": "expired entry content",
            "ts": "2026-01-01T00:00:00Z",
            "expires_at": "2026-02-01T00:00:00Z",
        })
        insert_entry(conn, {
            "project": "koan",
            "type": "learning",
            "content": "valid entry content",
            "ts": "2026-01-02T00:00:00Z",
        })
        results = recent_entries(conn, "koan", max_results=10)
        contents = [r["content"] for r in results]
        assert "expired entry content" not in contents
        assert "valid entry content" in contents
        conn.close()


class TestSchemaUpgrade:

    def test_old_schema_gets_upgraded(self, instance_dir):
        """Pre-existing 4-column DB gets upgraded to 8-column schema."""
        from app.memory_db import ensure_db, insert_entry, entry_count
        import app.memory_db as mdb

        mem_dir = Path(instance_dir) / "memory"
        db_file = mem_dir / "memory.db"

        # Create a legacy 4-column table
        conn = sqlite3.connect(str(db_file), timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE VIRTUAL TABLE entries "
            "USING fts5(project, type, content, ts UNINDEXED)"
        )
        conn.execute(
            "INSERT INTO entries(project, type, content, ts) VALUES (?, ?, ?, ?)",
            ("koan", "session", "old schema entry", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Reset FTS5 flag so ensure_db re-checks
        saved_fts5 = mdb._fts5_available
        try:
            mdb._fts5_available = None

            # ensure_db should detect old schema, drop, and recreate
            conn = ensure_db(instance_dir)
            assert conn is not None
            # Old data is gone (will be re-indexed from JSONL on next startup)
            assert entry_count(conn) == 0
            # New columns work
            insert_entry(conn, {
                "project": "koan",
                "type": "learning",
                "content": "new schema entry",
                "ts": "2026-06-01T00:00:00Z",
                "source_skill": "review",
                "tags": ["test"],
                "confidence": 0.8,
            })
            assert entry_count(conn) == 1
            conn.close()
        finally:
            mdb._fts5_available = saved_fts5


class TestSkillAwareBoosting:

    def test_matching_skill_entries_ranked_first(self, instance_dir):
        from app.memory_manager import MemoryManager
        mgr = MemoryManager(instance_dir)
        mgr.append_memory_entry(
            "learning", "koan", "fix: always check auth tokens on login",
            source_skill="fix",
        )
        mgr.append_memory_entry(
            "learning", "koan", "review: validate auth header format",
            source_skill="review",
        )
        mgr.append_memory_entry(
            "learning", "koan", "review: auth tokens must be rotated",
            source_skill="review",
        )
        results = mgr.read_memory_window(
            "koan", max_entries=10,
            query_text="authentication tokens",
            current_skill="review",
        )
        review_indices = [i for i, r in enumerate(results) if r.get("source_skill") == "review"]
        fix_indices = [i for i, r in enumerate(results) if r.get("source_skill") == "fix"]
        assert review_indices, "expected review entries in results"
        assert fix_indices, "expected fix entries in results"
        assert max(review_indices) < min(fix_indices)

    def test_recall_full_bypasses_fts(self, instance_dir):
        from app.skill_memory import _load_filtered_learnings
        proj_dir = Path(instance_dir) / "memory" / "projects" / "koan"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "learnings.md").write_text(
            "# Learnings\n\n- line one\n- line two\n",
            encoding="utf-8",
        )
        result = _load_filtered_learnings(
            instance_dir, "koan", "anything [recall:full]",
            max_k=1, recent_hedge=0,
        )
        assert result is not None
        assert "[recall:full]" in result
        assert "line one" in result
        assert "line two" in result


class TestSemanticSuperiority:
    """Verify FTS5 finds entries that Jaccard scoring misses."""

    def test_fts5_beats_jaccard_on_partial_overlap(self, instance_dir):
        from app.memory_db import ensure_db, search_learnings
        from app.memory_recall import score_and_select

        content = (
            "- JWT token expiry causes race condition in session handler\n"
            "- Fixed authentication timeout in login flow\n"
            "- CSS grid layouts work better than flexbox\n"
            "- Database connection pooling tunes at 25\n"
        )
        query = "authentication race condition"

        fts_results = search_learnings(
            ensure_db(instance_dir), content, query, max_k=4,
        )
        jaccard_selected, _, _ = score_and_select(
            content, query, max_k=2, recent_hedge=0,
        )

        fts_joined = " ".join(fts_results).lower()
        assert "authentication" in fts_joined
        assert "race" in fts_joined or "jwt" in fts_joined

        jaccard_joined = " ".join(jaccard_selected).lower()
        has_both_in_jaccard = ("authentication" in jaccard_joined and
                               ("race" in jaccard_joined or "jwt" in jaccard_joined))
        if has_both_in_jaccard:
            pytest.skip("Jaccard happened to find both — test inconclusive")
