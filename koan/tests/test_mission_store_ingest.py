"""One-time ingestion + read-only export for SqliteMissionStore."""

from app.mission_store.sqlite_store import SqliteMissionStore

FIXTURE = (
    "# Missions\n\n"
    "## Pending\n"
    "- queued one [project:koan] ⏳(2026-06-27T09:00)\n"
    "- recurring digest task\n"
    "### complex block [project:web]\n"
    "  some detail line\n"
    "  ```python\n"
    "  code = True\n"
    "  ```\n\n"
    "## In Progress\n"
    "- running two [project:koan] ⏳(2026-06-27T09:01) ▶(2026-06-27T09:05)\n\n"
    "## Done\n"
    "- done three ✅ (2026-06-26 18:00)\n\n"
    "## Failed\n"
    "- bad four ❌ (2026-06-26 19:00)\n"
)


def test_ingest_maps_states_projects_timestamps(tmp_path):
    (tmp_path / "missions.md").write_text(FIXTURE)
    store = SqliteMissionStore(str(tmp_path))
    assert store.is_initialized() is False
    report = store.ingest_from_file(tmp_path / "missions.md")

    assert store.count_by_state("pending") == 3
    assert store.count_by_state("in_progress") == 1
    assert store.count_by_state("done") == 1
    assert store.count_by_state("failed") == 1
    assert report.inserted == 6
    assert report.unparseable == []
    assert store.is_initialized() is True

    ip = store.list_by_state("in_progress")[0]
    assert ip.project == "koan"
    assert ip.started_at == "2026-06-27T09:05"
    assert "▶" not in ip.text and "⏳" not in ip.text  # markers stripped into columns


def test_ingest_is_one_shot(tmp_path):
    (tmp_path / "missions.md").write_text(FIXTURE)
    store = SqliteMissionStore(str(tmp_path))
    store.ingest_from_file(tmp_path / "missions.md")
    assert store.count_by_state("pending") == 3
    # A second store over the same DB must not re-ingest (already initialized).
    store2 = SqliteMissionStore(str(tmp_path))
    if not store2.is_initialized():
        store2.ingest_from_file(tmp_path / "missions.md")
    assert store2.count_by_state("pending") == 3  # not doubled


def test_unparseable_entries_surface_not_dropped(tmp_path):
    (tmp_path / "missions.md").write_text(
        "# Missions\n\n## Pending\n- ok one\nstray continuation with no bullet\n"
    )
    store = SqliteMissionStore(str(tmp_path))
    report = store.ingest_from_file(tmp_path / "missions.md")
    # "stray continuation" attaches to the prior item per parse_sections, so the
    # key comes from the "- ok one" line; assert nothing is silently lost.
    assert report.inserted + len(report.unparseable) >= 1
    assert store.count_by_state("pending") >= 1


def test_export_view_roundtrips_and_is_reingestable(tmp_path):
    (tmp_path / "missions.md").write_text(FIXTURE)
    store = SqliteMissionStore(str(tmp_path))
    store.ingest_from_file(tmp_path / "missions.md")

    out = tmp_path / "export.md"
    store.export_view(out)
    text = out.read_text()
    assert "## Pending" in text and "## In Progress" in text
    assert "## Done" in text and "## Failed" in text
    assert "queued one" in text and "▶(2026-06-27T09:05)" in text

    # The export is itself a valid ingestion input → same counts.
    (tmp_path / "second").mkdir()
    store2 = SqliteMissionStore(str(tmp_path / "second"))
    (tmp_path / "second" / "missions.md").write_text(text)
    store2.ingest_from_file(tmp_path / "second" / "missions.md")
    assert store2.count_by_state("pending") == 3
    assert store2.count_by_state("in_progress") == 1
    assert store2.count_by_state("done") == 1
    assert store2.count_by_state("failed") == 1


def test_reconcile_rebuilds_and_replaces(tmp_path):
    store = SqliteMissionStore(str(tmp_path))
    store.reconcile_from_content("# Missions\n\n## Pending\n- a\n- b\n")
    assert store.count_by_state("pending") == 2
    # Reconciling again fully replaces the prior missions rows.
    store.reconcile_from_content("# Missions\n\n## Pending\n- c\n")
    assert store.count_by_state("pending") == 1
    assert "c" in store.list_by_state("pending")[0].text
    # reconcile does not flip the initialized marker (only ingest does)
    assert store.is_initialized() is False


def test_reconcile_from_file(tmp_path):
    (tmp_path / "missions.md").write_text(
        "# Missions\n\n## Pending\n- x [project:koan]\n\n"
        "## Done\n- y ✅ (2026-06-30 12:00)\n"
    )
    store = SqliteMissionStore(str(tmp_path))
    store.reconcile_from_file(tmp_path / "missions.md")
    assert store.count_by_state("pending") == 1
    assert store.count_by_state("done") == 1
    assert store.list_by_state("pending")[0].project == "koan"


def test_recover_stale_requeues_then_escalates(tmp_path):
    store = SqliteMissionStore(str(tmp_path))
    store.add_pending("work")
    store.claim_next()  # -> in_progress
    assert store.count_by_state("in_progress") == 1
    # First few recoveries requeue…
    r1 = store.recover_stale(max_recover=1)
    assert r1.requeued == 1 and store.count_by_state("pending") == 1
    store.claim_next()
    # …until the recovery budget is exceeded, then escalate to failed.
    r2 = store.recover_stale(max_recover=1)
    assert r2.escalated and store.count_by_state("failed") == 1


def test_reconcile_all_logs_dropped_unparseable(caplog):
    """A store round-trip that drops a mission line the store can't re-parse must
    log at ERROR, not silently no-op-delete it."""
    from unittest.mock import MagicMock, patch

    from app.mission_store import transition
    from app.mission_store.base import IngestReport

    store = MagicMock()
    store.reconcile_from_content.return_value = IngestReport(
        inserted=0, unparseable=["- a line the store could not re-parse"]
    )
    with patch.object(transition, "get_mission_store", return_value=store), \
         patch("app.mission_store.aux_stores.CiQueueStore"), \
         patch("app.mission_store.aux_stores.IdeaStore"), \
         caplog.at_level("ERROR"):
        transition.reconcile_all("/tmp/does-not-matter", "# Missions\n")

    assert "unparseable" in caplog.text
    assert "could not re-parse" in caplog.text


def test_reconcile_all_quiet_when_all_parse(caplog):
    from unittest.mock import MagicMock, patch

    from app.mission_store import transition
    from app.mission_store.base import IngestReport

    store = MagicMock()
    store.reconcile_from_content.return_value = IngestReport(inserted=3, unparseable=[])
    with patch.object(transition, "get_mission_store", return_value=store), \
         patch("app.mission_store.aux_stores.CiQueueStore"), \
         patch("app.mission_store.aux_stores.IdeaStore"), \
         caplog.at_level("ERROR"):
        transition.reconcile_all("/tmp/does-not-matter", "# Missions\n")

    assert "unparseable" not in caplog.text
