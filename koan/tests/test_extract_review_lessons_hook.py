"""Tests for the extract_review_lessons post_review hook template."""

import importlib.util
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_hook():
    # .py.example is not a recognized source suffix, so use SourceFileLoader.
    root = Path(__file__).resolve().parents[2]
    path = root / "instance.example" / "hooks" / "extract_review_lessons.py.example"
    loader = SourceFileLoader("extract_review_lessons_example", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_hook_writes_review_json(tmp_path):
    mod = _load_hook()
    ctx = {
        "instance_dir": str(tmp_path),
        "project_name": "my-toolkit",
        "owner": "octo",
        "repo": "repo",
        "pr_number": "42",
        "pr_url": "https://github.com/octo/repo/pull/42",
        "lgtm": False,
        "verdict_submitted": True,
        "closed": False,
        "ultra": True,
        "review_summary": {"lgtm": False},
        "review_data": {"findings": [{"severity": "critical", "title": "bug"}]},
    }
    mod.on_post_review(ctx)
    files = list((tmp_path / "reviews").glob("42_*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record["pr_number"] == "42"
    assert record["lgtm"] is False
    assert record["findings"] == [{"severity": "critical", "title": "bug"}]
    assert record["human_reaction"] is None
    assert record["project_name"] == "my-toolkit"
    assert record["verdict_submitted"] is True
    assert record["ultra"] is True


def test_hook_noop_without_instance_dir(tmp_path):
    mod = _load_hook()
    mod.on_post_review({"pr_number": "1"})  # must not raise
    assert not (tmp_path / "reviews").exists()


def test_hook_filename_uses_microsecond_timestamp(tmp_path, monkeypatch):
    """Same-second re-reviews must not overwrite each other."""
    from datetime import datetime, timezone

    mod = _load_hook()
    fixed = datetime(2026, 7, 17, 12, 0, 0, 123456, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(mod, "datetime", _FixedDateTime)
    ctx = {
        "instance_dir": str(tmp_path),
        "pr_number": "7",
        "review_data": {},
    }
    mod.on_post_review(ctx)
    files = list((tmp_path / "reviews").glob("7_*.json"))
    assert len(files) == 1
    assert files[0].name == "7_20260717T120000123456Z.json"
