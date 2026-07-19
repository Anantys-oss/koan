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
    # Neutralize ambient stray-/tmp trees (the pytest tmpdir itself matches
    # /tmp/pytest-of-*) so this test only exercises the standard roots.
    monkeypatch.setattr(page_cache, "_stray_tmp_roots", list)
    roots = page_cache.default_reclaim_roots()
    resolved = {str(Path(r).resolve()) for r in roots}
    assert str(proj.resolve()) in resolved
    assert str((tmp_path / "instance").resolve()) in resolved


def test_nested_roots_deduped(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    venv = proj / ".venv"  # venv nested inside the project workdir
    venv.mkdir(parents=True)
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    monkeypatch.setattr(
        page_cache, "get_known_projects", lambda: [("proj", str(proj))]
    )
    monkeypatch.setattr(page_cache.sys, "prefix", str(venv))
    monkeypatch.setattr(page_cache, "_stray_tmp_roots", list)
    roots = {str(r) for r in page_cache.default_reclaim_roots()}
    assert str(proj.resolve()) in roots
    assert str(venv.resolve()) not in roots  # nested → dropped


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


def test_priority_roots_ordered_before_projects(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    inst = tmp_path / "instance"
    inst.mkdir()
    venv = tmp_path / "venv"
    venv.mkdir()
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    monkeypatch.setattr(
        page_cache, "get_known_projects", lambda: [("proj", str(proj))]
    )
    monkeypatch.setattr(page_cache.sys, "prefix", str(venv))
    monkeypatch.setattr(page_cache, "_stray_tmp_roots", list)
    roots = [str(r) for r in page_cache.default_reclaim_roots()]
    # instance/ and venv (small, high-value) come before the project workdir.
    assert roots.index(str(inst.resolve())) < roots.index(str(proj.resolve()))
    assert roots.index(str(venv.resolve())) < roots.index(str(proj.resolve()))


def test_stray_tmp_roots_ignores_non_tmp_patterns(monkeypatch):
    # A pattern outside /tmp must be skipped outright: the sweep only honors
    # /tmp/* patterns. Use a fixed non-/tmp pattern (not tmp_path, which pytest
    # roots under /tmp on CI) and spy on glob to prove the guard short-circuits
    # before globbing rather than merely matching nothing.
    globbed: list[str] = []
    monkeypatch.setattr(
        page_cache, "get_cleanup_extra_tmp_globs",
        lambda: ["/var/tmp/pytest-of-*"],
    )
    import glob as _glob
    monkeypatch.setattr(_glob, "glob", lambda p: globbed.append(p) or [])
    assert page_cache._stray_tmp_roots() == []
    assert globbed == []  # non-/tmp pattern never reached glob


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX-only")
def test_stray_tmp_roots_dir_and_uid_filters(monkeypatch):
    import app.page_cache as pc

    captured = {}

    def fake_glob(pattern):
        captured["pattern"] = pattern
        return ["/tmp/pytest-of-me", "/tmp/pytest-of-other", "/tmp/pytest-file"]

    def fake_lstat(path):
        import stat as _stat
        from types import SimpleNamespace
        if path == "/tmp/pytest-file":
            return SimpleNamespace(st_mode=_stat.S_IFREG, st_uid=os.getuid())
        uid = os.getuid() if path == "/tmp/pytest-of-me" else os.getuid() + 1
        return SimpleNamespace(st_mode=_stat.S_IFDIR, st_uid=uid)

    monkeypatch.setattr(pc, "get_cleanup_extra_tmp_globs", lambda: ["/tmp/pytest-of-*"])
    monkeypatch.setattr(pc.os, "lstat", fake_lstat)
    import glob as _g
    monkeypatch.setattr(_g, "glob", fake_glob)
    roots = {str(r) for r in pc._stray_tmp_roots()}
    # Only the own-uid directory survives (other-uid dir and regular file dropped).
    assert roots == {"/tmp/pytest-of-me"}


def _run_stats(monkeypatch, stats):
    logs = []
    monkeypatch.setattr(page_cache, "_log", lambda cat, msg: logs.append((cat, msg)))
    monkeypatch.setattr(
        page_cache, "get_page_cache_reclaim_config",
        lambda: {"enabled": True, "idle_interval_s": 900, "time_budget_s": 10,
                 "extra_roots": []},
    )
    monkeypatch.setattr(page_cache, "default_reclaim_roots", lambda: [])
    monkeypatch.setattr(page_cache, "reclaim_page_cache", lambda *a, **k: stats)
    page_cache.run_reclaim("idle")
    return logs


def test_budget_hit_logged_even_below_delta(monkeypatch):
    logs = _run_stats(monkeypatch, page_cache.ReclaimStats(
        supported=True, file_mb_before=100.0, file_mb_after=99.0, budget_hit=True,
    ))
    health = [msg for cat, msg in logs if cat == "health"]
    assert health and "budget hit" in health[0]


def test_dominant_errors_logged_even_below_delta(monkeypatch):
    # files≈0, errors high → systemic denial, must not be silent.
    logs = _run_stats(monkeypatch, page_cache.ReclaimStats(
        supported=True, file_mb_before=100.0, file_mb_after=100.0,
        files=0, errors=500,
    ))
    health = [msg for cat, msg in logs if cat == "health"]
    assert health and "500 errors" in health[0]
