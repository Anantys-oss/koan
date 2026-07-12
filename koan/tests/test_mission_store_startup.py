"""One-time boot ingest (app.mission_store.startup.ensure_ingested)."""

import pytest

from app.mission_store import get_mission_store, reset_cache
from app.mission_store.aux_stores import CiQueueStore, IdeaStore, QuarantineStore
from app.mission_store.startup import ensure_ingested

FULL = (
    "# Missions\n\n"
    "## CI\n"
    "- [project:koan] https://github.com/o/r/pull/7 branch:b repo:o/r "
    "queued:2026-07-01T10:00 (attempt 1/5)\n\n"
    "## Ideas\n- a bright idea\n\n"
    "## Pending\n- do the thing [project:koan] ⏳(2026-07-01T09:00)\n\n"
    "## In Progress\n\n## Done\n- old one ✅ (2026-06-30 12:00)\n\n## Failed\n"
)


def _fresh(tmp_path):
    reset_cache()
    return tmp_path


def test_ensure_ingested_populates_all_populations(tmp_path):
    _fresh(tmp_path)
    (tmp_path / "missions.md").write_text(FULL)
    report = ensure_ingested(str(tmp_path))
    assert report is not None

    store = get_mission_store(str(tmp_path))
    assert store.count_by_state("pending") == 1
    assert store.count_by_state("done") == 1
    assert store.is_initialized() is True
    assert CiQueueStore(str(tmp_path)).get_items()[0]["pr_url"].endswith("/pull/7")
    assert IdeaStore(str(tmp_path)).list() == ["- a bright idea"]


def test_ensure_ingested_is_one_shot(tmp_path):
    _fresh(tmp_path)
    (tmp_path / "missions.md").write_text(FULL)
    assert ensure_ingested(str(tmp_path)) is not None
    reset_cache()
    # Second boot: store already initialized → no-op, no double-ingest.
    assert ensure_ingested(str(tmp_path)) is None
    assert get_mission_store(str(tmp_path)).count_by_state("pending") == 1


def test_ensure_ingested_skips_when_already_synced(tmp_path):
    # Cutover-boot regression: startup pruning re-syncs the store first (sets the
    # s8_synced marker but NOT initialized_at). ensure_ingested must short-circuit
    # on that too, or it appends a full second copy (ingest_from_file INSERTs
    # without deleting) — doubling every mission/CI/idea on the cutover boot.
    from app.mission_store.transition import reconcile_all
    _fresh(tmp_path)
    (tmp_path / "missions.md").write_text(FULL)
    reconcile_all(str(tmp_path), FULL)               # like prune's re-sync…
    get_mission_store(str(tmp_path)).mark_synced()   # …which sets s8_synced only
    assert get_mission_store(str(tmp_path)).is_initialized() is False

    assert ensure_ingested(str(tmp_path)) is None    # short-circuits, no re-ingest
    assert get_mission_store(str(tmp_path)).count_by_state("pending") == 1
    assert get_mission_store(str(tmp_path)).count_by_state("done") == 1
    assert len(CiQueueStore(str(tmp_path)).get_items()) == 1
    assert IdeaStore(str(tmp_path)).list() == ["- a bright idea"]


def test_ensure_ingested_migrates_quarantine_file(tmp_path):
    _fresh(tmp_path)
    (tmp_path / "missions.md").write_text("# Missions\n\n## Pending\n")
    (tmp_path / "missions-quarantine.md").write_text(
        "- \U0001f6e1️ [2026-07-01 10:00] (telegram) prompt injection: evil text\n"
    )
    ensure_ingested(str(tmp_path))
    rows = QuarantineStore(str(tmp_path)).list()
    assert len(rows) == 1
    assert rows[0]["source"] == "telegram" and "evil text" in rows[0]["text"]


def test_quarantine_migration_failure_is_fatal_and_leaves_store_uninitialized(
    tmp_path, monkeypatch
):
    # A failed security-record migration must abort the ingest step (raise), not
    # log-and-continue: otherwise the initialized marker gets set, the file is later
    # regenerated from the store, and the unmigrated records are dropped forever.
    _fresh(tmp_path)
    (tmp_path / "missions.md").write_text(FULL)
    (tmp_path / "missions-quarantine.md").write_text(
        "- \U0001f6e1️ [2026-07-01 10:00] (telegram) injection: bad\n"
    )
    from app.mission_store import aux_stores
    monkeypatch.setattr(aux_stores.QuarantineStore, "add", lambda *a, **k: False)

    with pytest.raises(RuntimeError, match="quarantine migration incomplete"):
        ensure_ingested(str(tmp_path))

    # Marker unset → next boot retries. And because quarantine ingests FIRST, the
    # additive CI/Ideas inserts never ran, so the retry won't double-insert them.
    assert get_mission_store(str(tmp_path)).is_initialized() is False
    assert CiQueueStore(str(tmp_path)).get_items() == []
    assert IdeaStore(str(tmp_path)).list() == []


def test_ensure_ingested_no_file_is_safe(tmp_path):
    _fresh(tmp_path)
    report = ensure_ingested(str(tmp_path))
    assert report is not None and report.inserted == 0
    assert get_mission_store(str(tmp_path)).is_initialized() is True
