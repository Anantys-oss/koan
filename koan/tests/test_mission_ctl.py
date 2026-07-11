"""Tests for the break-glass mission-queue CLI (app.mission_ctl)."""

import pytest

from app import mission_ctl
from tests.store_helpers import seed_missions

_CONTENT = (
    "# Missions\n\n## CI\n\n"
    "## Pending\n- [project:foo] add tests\n- refactor parser\n\n"
    "## In Progress\n- fix the auth bug\n\n"
    "## Done\n\n## Failed\n"
)


def _store(instance):
    from app.mission_store import get_mission_store

    return get_mission_store(str(instance))


@pytest.fixture
def inst(tmp_path, monkeypatch):
    instance = tmp_path / "instance"
    seed_missions(instance, _CONTENT)
    monkeypatch.setattr(mission_ctl, "_instance_dir", lambda: instance)
    return instance


class TestList:
    def test_active_lists_in_progress_and_pending(self, inst, capsys):
        rc = mission_ctl.cmd_list("active")
        out = capsys.readouterr().out
        assert rc == 0
        assert "IN PROGRESS (1):" in out
        assert "PENDING (2):" in out
        assert "i1" in out and "fix the auth bug" in out
        assert "p1" in out and "add tests" in out
        assert "p2" in out and "refactor parser" in out

    def test_pending_only_omits_in_progress(self, inst, capsys):
        mission_ctl.cmd_list("pending")
        out = capsys.readouterr().out
        assert "PENDING (2):" in out
        assert "IN PROGRESS" not in out

    def test_all_includes_terminal_sections(self, inst, capsys):
        mission_ctl.cmd_list("all")
        out = capsys.readouterr().out
        assert "DONE (0):" in out and "FAILED (0):" in out

    def test_empty_queue_shows_none(self, tmp_path, monkeypatch, capsys):
        instance = tmp_path / "instance"
        seed_missions(
            instance,
            "# Missions\n\n## CI\n\n## Pending\n\n## In Progress\n\n## Done\n\n## Failed\n",
        )
        monkeypatch.setattr(mission_ctl, "_instance_dir", lambda: instance)
        mission_ctl.cmd_list("active")
        assert "(none)" in capsys.readouterr().out


class TestDelete:
    def test_delete_pending_removes_from_store_and_export(self, inst, capsys):
        rc = mission_ctl.cmd_delete("p1")
        assert rc == 0
        pending = [m.text for m in _store(inst).list_by_state("pending")]
        assert not any("add tests" in t for t in pending)
        assert any("refactor parser" in t for t in pending)
        # The read-only export is regenerated to match the store.
        md = (inst / "missions.md").read_text()
        assert "add tests" not in md
        assert "Removed pending mission" in capsys.readouterr().out

    def test_delete_in_progress_moves_to_failed_with_hint(self, inst, capsys):
        rc = mission_ctl.cmd_delete("i1")
        assert rc == 0
        store = _store(inst)
        assert store.list_by_state("in_progress") == []
        assert any("fix the auth bug" in m.text for m in store.list_by_state("failed"))
        out = capsys.readouterr().out
        assert "Failed" in out
        assert "restart" in out.lower()  # unstick-the-loop hint

    def test_delete_by_keyword_matches_pending(self, inst):
        assert mission_ctl.cmd_delete("refactor") == 0
        pending = [m.text for m in _store(inst).list_by_state("pending")]
        assert not any("refactor" in t for t in pending)

    def test_keyword_prefers_in_progress(self, inst):
        # "auth" appears only in the in-progress mission → aborted to Failed.
        assert mission_ctl.cmd_delete("auth") == 0
        store = _store(inst)
        assert any("auth" in m.text for m in store.list_by_state("failed"))

    def test_out_of_range_selector_returns_1(self, inst, capsys):
        rc = mission_ctl.cmd_delete("p9")
        assert rc == 1
        assert "No active mission matches" in capsys.readouterr().err

    def test_unknown_keyword_returns_1(self, inst, capsys):
        assert mission_ctl.cmd_delete("no-such-mission-xyz") == 1
        assert "No active mission matches" in capsys.readouterr().err


class TestResolve:
    def test_positional_selectors(self, inst):
        store = _store(inst)
        st, idx, m = mission_ctl._resolve(store, "p2")
        assert (st, idx) == ("pending", 1) and "refactor" in m.text
        st, idx, m = mission_ctl._resolve(store, "i1")
        assert st == "in_progress" and "auth" in m.text

    def test_no_match_returns_none(self, inst):
        assert mission_ctl._resolve(_store(inst), "zzz-none") is None


class TestMain:
    def test_main_list_default_active(self, inst, capsys):
        assert mission_ctl.main(["list"]) == 0
        assert "PENDING" in capsys.readouterr().out

    def test_main_delete_rm_alias(self, inst):
        assert mission_ctl.main(["rm", "p1"]) == 0
        assert not any(
            "add tests" in m.text for m in _store(inst).list_by_state("pending")
        )
