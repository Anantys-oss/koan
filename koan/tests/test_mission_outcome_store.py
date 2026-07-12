from app.mission_store.aux_stores import OutcomeStore
from app.mission_store.transition import reconcile_all


def test_latest_returns_newest_outcome(tmp_path):
    store = OutcomeStore(str(tmp_path))
    store.record("do X [project:foo]", "failed", "timeout", "killed after 1800s")
    store.record("do X [project:foo]", "done", None, None)
    latest = store.latest("do X [project:foo]")
    assert latest["status"] == "done"
    assert latest["reason_category"] is None


def test_latest_none_when_absent(tmp_path):
    store = OutcomeStore(str(tmp_path))
    assert store.latest("never recorded") is None


def test_outcome_survives_missions_table_rebuild(tmp_path):
    store = OutcomeStore(str(tmp_path))
    store.record("ship it", "failed", "agent_error", "exit 1")
    # A normal write path DELETE+re-INSERTs the missions table; the outcome
    # log must be untouched by it.
    reconcile_all(str(tmp_path), "# Missions\n\n## Pending\n- ship it\n")
    assert store.latest("ship it")["reason_category"] == "agent_error"


def test_cap_keeps_most_recent(tmp_path):
    store = OutcomeStore(str(tmp_path))
    for i in range(OutcomeStore.KEEP + 10):
        store.record(f"m{i}", "done")
    # oldest evicted, newest retained
    assert store.latest("m0") is None
    assert store.latest(f"m{OutcomeStore.KEEP + 9}") is not None
