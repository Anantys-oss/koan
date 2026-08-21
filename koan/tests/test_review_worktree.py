"""Tests for the pinned PR-head review worktree.

Runs against real temp git repositories with no network: the PR head is a real
ref in a real "remote", so the fetch/verify/checkout path is exercised end to
end rather than mocked away.
"""

import subprocess
from pathlib import Path

import pytest

from app.review_worktree import (
    REF_NAMESPACE,
    WORKTREE_PREFIX,
    pinned_review_worktree,
    sweep_stale_review_worktrees,
    worktree_root,
)


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=check,
    )


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A bare 'remote' carrying refs/pull/7/head, plus a local clone."""
    monkeypatch.setenv("KOAN_REVIEW_WORKTREE_DIR", str(tmp_path / "wt"))

    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main", ".")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    (origin / "base.txt").write_text("base\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "base")

    # A "PR" commit published under refs/pull/7/head, as GitHub does.
    _git(origin, "checkout", "-q", "-b", "feature")
    (origin / "feature.txt").write_text("the reviewed change\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "feature")
    head_sha = _git(origin, "rev-parse", "HEAD").stdout.strip()
    _git(origin, "update-ref", "refs/pull/7/head", head_sha)
    _git(origin, "checkout", "-q", "main")

    local = tmp_path / "local"
    _git(tmp_path, "clone", "-q", str(origin), str(local))

    monkeypatch.setattr(
        "app.rebase_pr._resolve_fetch_source",
        lambda owner, repo, path: (str(origin), ""),
    )
    return local, head_sha


class TestPinnedWorktree:
    def test_yields_clean_checkout_at_pr_head(self, repos):
        local, head_sha = repos
        with pinned_review_worktree(
            "o", "r", "7", str(local), head_oid_fn=lambda *a: head_sha,
        ) as (path, sha):
            assert sha == head_sha
            assert _git(path, "rev-parse", "HEAD").stdout.strip() == head_sha
            # The whole point: the reviewed file is actually on disk.
            assert (Path(path) / "feature.txt").read_text() == "the reviewed change\n"
            assert not _git(path, "status", "--porcelain").stdout.strip()
            held = path
        assert not Path(held).exists(), "worktree must be removed on exit"

    def test_temp_ref_is_deleted_on_exit(self, repos):
        local, head_sha = repos
        with pinned_review_worktree(
            "o", "r", "7", str(local), head_oid_fn=lambda *a: head_sha,
        ):
            refs = _git(local, "for-each-ref", "--format=%(refname)", REF_NAMESPACE)
            assert REF_NAMESPACE in refs.stdout
        refs = _git(local, "for-each-ref", "--format=%(refname)", REF_NAMESPACE)
        assert not refs.stdout.strip(), "temp ref leaked"

    def test_cleans_up_when_the_body_raises(self, repos):
        local, head_sha = repos
        held = {}
        with pytest.raises(ValueError):
            with pinned_review_worktree(
                "o", "r", "7", str(local), head_oid_fn=lambda *a: head_sha,
            ) as (path, _):
                held["path"] = path
                raise ValueError("boom")
        assert not Path(held["path"]).exists()
        refs = _git(local, "for-each-ref", "--format=%(refname)", REF_NAMESPACE)
        assert not refs.stdout.strip()

    def test_sha_mismatch_raises_and_cleans_up(self, repos):
        local, _ = repos
        wrong = "0" * 40
        with pytest.raises(RuntimeError, match="does not match GitHub"):
            with pinned_review_worktree(
                "o", "r", "7", str(local), head_oid_fn=lambda *a: wrong,
            ):
                pytest.fail("must not yield on a SHA mismatch")
        refs = _git(local, "for-each-ref", "--format=%(refname)", REF_NAMESPACE)
        assert not refs.stdout.strip()

    def test_missing_live_head_raises(self, repos):
        local, _ = repos
        with pytest.raises(RuntimeError, match="live PR HEAD is unavailable"):
            with pinned_review_worktree(
                "o", "r", "7", str(local), head_oid_fn=lambda *a: "",
            ):
                pytest.fail("must not yield without a live HEAD")

    def test_no_fetch_source_raises(self, repos, monkeypatch):
        local, head_sha = repos
        monkeypatch.setattr(
            "app.rebase_pr._resolve_fetch_source", lambda *a: ("", ""),
        )
        with pytest.raises(RuntimeError, match="no authenticated git fetch source"):
            with pinned_review_worktree(
                "o", "r", "7", str(local), head_oid_fn=lambda *a: head_sha,
            ):
                pytest.fail("must not yield without a fetch source")

    def test_concurrent_pins_do_not_collide(self, repos):
        local, head_sha = repos
        with pinned_review_worktree(
            "o", "r", "7", str(local), head_oid_fn=lambda *a: head_sha,
        ) as (first, _):
            with pinned_review_worktree(
                "o", "r", "7", str(local), head_oid_fn=lambda *a: head_sha,
            ) as (second, _):
                assert first != second
                assert Path(first).exists() and Path(second).exists()


class TestSweep:
    def test_removes_stale_and_keeps_fresh(self, repos):
        local, _ = repos
        root = worktree_root()
        root.mkdir(parents=True, exist_ok=True)
        stale = root / f"{WORKTREE_PREFIX}7-deadbeef"
        fresh = root / f"{WORKTREE_PREFIX}8-cafebabe"
        for d in (stale, fresh):
            d.mkdir()
            (d / "marker").write_text("x")
        import os
        import time
        old = time.time() - 24 * 3600
        os.utime(stale, (old, old))

        cleared = sweep_stale_review_worktrees(str(local), max_age_hours=6)

        assert cleared == 1
        assert not stale.exists(), "stale worktree must be removed"
        assert fresh.exists(), "a fresh worktree must be left alone"

    def test_ignores_unrelated_directories(self, repos):
        local, _ = repos
        root = worktree_root()
        root.mkdir(parents=True, exist_ok=True)
        other = root / "something-else"
        other.mkdir()
        import os
        import time
        old = time.time() - 24 * 3600
        os.utime(other, (old, old))

        sweep_stale_review_worktrees(str(local), max_age_hours=6)
        assert other.exists(), "sweep must only touch review worktrees"

    def test_never_raises_on_a_bad_path(self):
        # Tidy-up failing must never fail a review.
        assert sweep_stale_review_worktrees("/nonexistent/path/xyz") == 0
