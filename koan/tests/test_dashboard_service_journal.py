"""Unit tests for app.dashboard_service.journal (no Flask client)."""
from unittest.mock import patch

from app.dashboard_service import journal as svc


def _setup(tmp_path):
    jdir = tmp_path / "journal"
    (jdir / "2026-02-01").mkdir(parents=True)
    (jdir / "2026-02-01" / "koan.md").write_text("entry koan")
    (jdir / "2026-01-15.md").write_text("flat entry")
    return jdir


def test_get_journal_dates(tmp_path):
    jdir = _setup(tmp_path)
    with patch.object(svc.state, "JOURNAL_DIR", jdir):
        dates = svc.get_journal_dates()
    assert dates == ["2026-02-01", "2026-01-15"]


def test_get_journal_dates_missing(tmp_path):
    with patch.object(svc.state, "JOURNAL_DIR", tmp_path / "nope"):
        assert svc.get_journal_dates() == []


def test_get_journal_day_nested_and_flat(tmp_path):
    jdir = _setup(tmp_path)
    with patch.object(svc.state, "JOURNAL_DIR", jdir):
        nested = svc.get_journal_day("2026-02-01")
        flat = svc.get_journal_day("2026-01-15")
    assert nested == [{"project": "koan", "content": "entry koan"}]
    assert flat == [{"project": "general", "content": "flat entry"}]


def test_get_journal_entries(tmp_path):
    jdir = _setup(tmp_path)
    with patch.object(svc.state, "JOURNAL_DIR", jdir):
        entries = svc.get_journal_entries()
    assert {e["date"] for e in entries} == {"2026-02-01", "2026-01-15"}
