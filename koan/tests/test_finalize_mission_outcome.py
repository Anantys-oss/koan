import app.run as run


def _stub_move(*a, **k):  # _update_mission_in_file → pretend the move committed
    return True


def test_finalize_records_done_outcome(monkeypatch, tmp_path):
    calls = []
    # Patch AT SOURCE — _finalize_mission imports record_outcome locally, so a
    # patch on run.record_outcome would never intercept it (review feedback).
    monkeypatch.setattr("app.mission_outcome.record_outcome",
                        lambda *a, **k: calls.append((a, k)) or True)
    monkeypatch.setattr("app.mission_history.record_execution", lambda *a, **k: None)
    monkeypatch.setattr(run, "_update_mission_in_file", _stub_move)
    run._finalize_mission(str(tmp_path), "do X [project:foo]", "foo", 0)
    (inst, text, status), kw = calls[-1]
    assert status == "done"
    assert kw.get("reason_category") is None


def test_finalize_records_failed_outcome_with_category(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("app.mission_outcome.record_outcome",
                        lambda *a, **k: calls.append((a, k)) or True)
    monkeypatch.setattr("app.mission_history.record_execution", lambda *a, **k: None)
    monkeypatch.setattr(run, "_update_mission_in_file", _stub_move)
    run._finalize_mission(str(tmp_path), "do X", "foo", 143)  # SIGTERM → timeout
    (inst, text, status), kw = calls[-1]
    assert status == "failed"
    assert kw["reason_category"] == "timeout"


def test_finalize_outcome_error_does_not_block(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr("app.mission_outcome.record_outcome", _boom)
    hist = []
    monkeypatch.setattr("app.mission_history.record_execution",
                        lambda *a, **k: hist.append(a))
    monkeypatch.setattr(run, "_update_mission_in_file", _stub_move)
    # Must not raise; history recording still runs.
    run._finalize_mission(str(tmp_path), "do X", "foo", 0)
    assert hist
