import os
from pathlib import Path

import pytest
from app import page_cache


def _make_tree(root: Path, n: int) -> None:
    for i in range(n):
        (root / f"f{i}.bin").write_bytes(b"x" * 4096)


def test_noop_when_fadvise_missing(tmp_path, monkeypatch):
    _make_tree(tmp_path, 3)
    monkeypatch.delattr(os, "posix_fadvise", raising=False)
    stats = page_cache.reclaim_page_cache([tmp_path])
    assert stats.supported is False
    assert stats.files == 0


@pytest.mark.skipif(not hasattr(os, "posix_fadvise"), reason="Linux-only")
def test_counts_regular_files_and_swallows_errors(tmp_path):
    _make_tree(tmp_path, 5)
    # A dangling symlink must be skipped, not counted or raised on.
    (tmp_path / "link").symlink_to(tmp_path / "missing.bin")
    stats = page_cache.reclaim_page_cache([tmp_path], budget_s=5.0)
    assert stats.files == 5
    assert stats.errors == 0  # symlink is lstat-skipped, not an error


@pytest.mark.skipif(not hasattr(os, "posix_fadvise"), reason="Linux-only")
def test_budget_cutoff_stops_walk(tmp_path):
    _make_tree(tmp_path, 50)
    # Zero budget → deadline already passed → no files processed.
    stats = page_cache.reclaim_page_cache([tmp_path], budget_s=0.0)
    assert stats.budget_hit is True
    assert stats.files == 0


def test_default_roots_include_projects_and_instance(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (tmp_path / "instance").mkdir()
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    monkeypatch.setattr(
        page_cache, "get_known_projects", lambda: [("proj", str(proj))]
    )
    roots = page_cache.default_reclaim_roots()
    resolved = {str(Path(r).resolve()) for r in roots}
    assert str(proj.resolve()) in resolved
    assert str((tmp_path / "instance").resolve()) in resolved


def test_idle_throttle_respects_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(
        page_cache, "reclaim_page_cache",
        lambda *a, **k: calls.append(1) or page_cache.ReclaimStats(supported=True),
    )
    monkeypatch.setattr(
        page_cache, "get_page_cache_reclaim_config",
        lambda: {"enabled": True, "idle_interval_s": 900, "time_budget_s": 10,
                 "extra_roots": []},
    )
    monkeypatch.setattr(page_cache, "default_reclaim_roots", lambda: [])
    page_cache._reset_idle_throttle()
    page_cache.maybe_reclaim_page_cache_idle(now=1000.0)   # first call fires
    page_cache.maybe_reclaim_page_cache_idle(now=1000.0 + 300)  # within → skip
    page_cache.maybe_reclaim_page_cache_idle(now=1000.0 + 901)  # past → fires
    assert len(calls) == 2


def test_disabled_config_skips_reclaim(monkeypatch):
    calls = []
    monkeypatch.setattr(
        page_cache, "reclaim_page_cache",
        lambda *a, **k: calls.append(1) or page_cache.ReclaimStats(supported=True),
    )
    monkeypatch.setattr(
        page_cache, "get_page_cache_reclaim_config",
        lambda: {"enabled": False, "idle_interval_s": 900, "time_budget_s": 10,
                 "extra_roots": []},
    )
    monkeypatch.setattr(page_cache, "default_reclaim_roots", lambda: [])
    page_cache._reset_idle_throttle()
    assert page_cache.maybe_reclaim_page_cache_idle(now=1000.0) is None
    stats = page_cache.run_reclaim("post-mission")
    assert stats.supported is True
    assert not calls


def test_log_suppressed_below_delta_threshold(monkeypatch):
    logs = []
    monkeypatch.setattr(page_cache, "_log", lambda cat, msg: logs.append((cat, msg)))
    monkeypatch.setattr(
        page_cache, "get_page_cache_reclaim_config",
        lambda: {"enabled": True, "idle_interval_s": 900, "time_budget_s": 10,
                 "extra_roots": []},
    )
    monkeypatch.setattr(page_cache, "default_reclaim_roots", lambda: [])
    monkeypatch.setattr(
        page_cache, "reclaim_page_cache",
        lambda *a, **k: page_cache.ReclaimStats(
            supported=True, file_mb_before=100.0, file_mb_after=99.0
        ),
    )
    page_cache.run_reclaim("idle")  # delta 1.0 MB < 3.0 → no health log
    assert not any(cat == "health" for cat, _ in logs)
