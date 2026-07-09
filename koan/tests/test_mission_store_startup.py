"""One-time boot ingest (app.mission_store.startup.ensure_ingested)."""

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


def test_ensure_ingested_no_file_is_safe(tmp_path):
    _fresh(tmp_path)
    report = ensure_ingested(str(tmp_path))
    assert report is not None and report.inserted == 0
    assert get_mission_store(str(tmp_path)).is_initialized() is True
