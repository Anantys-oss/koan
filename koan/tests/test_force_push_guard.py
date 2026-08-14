"""Tests for force_push_guard.py — post-force-push content-preservation checks."""

import subprocess
from types import SimpleNamespace

import pytest

from app.force_push_guard import (
    PushGuardFindings,
    _classify_missing_commits,
    build_push_warning,
    observe_remote_head,
    verify_content_preserved,
)


def _git(cwd, *args):
    """Run git in *cwd*, raising on failure."""
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def _git_out(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit_file(repo, name, content, message):
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


@pytest.fixture
def pr_setup(tmp_path):
    """A bare remote, a seed clone, and a work clone holding a PR branch.

    Topology at fixture exit (all pushed to the bare remote):

        main:    c1 ── c2            (base advanced after the fork)
        feature: c1 ── f1 ── f2      (the PR, work clone checked out here)
    """
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "main", ".")

    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(bare), "seed")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    _commit_file(seed, "a.txt", "one", "c1")
    _git(seed, "push", "origin", "main")
    _git(seed, "checkout", "-b", "feature")
    _commit_file(seed, "f1.txt", "f1", "feature 1")
    _commit_file(seed, "f2.txt", "f2", "feature 2")
    _git(seed, "push", "origin", "feature")
    _git(seed, "checkout", "main")
    _commit_file(seed, "b.txt", "two", "c2")
    _git(seed, "push", "origin", "main")

    work = tmp_path / "work"
    _git(tmp_path, "clone", str(bare), "work")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "feature")
    pre_rebase_head = _git_out(work, "rev-parse", "HEAD")
    return SimpleNamespace(
        bare=bare, seed=seed, work=work, pre_rebase_head=pre_rebase_head,
    )


class TestVerifyContentPreserved:
    def test_clean_rebase_has_no_findings(self, pr_setup):
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_head=pr_setup.pre_rebase_head,
            push_remote="origin", branch="feature",
        )
        assert findings is not None
        assert not findings.has_findings
        assert build_push_warning(findings, "feature") == ""

    def test_dropped_commit_and_file_detected(self, pr_setup):
        """A rebase that silently loses a commit is flagged as CAUTION."""
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "reset", "--hard", "HEAD~1")  # lose "feature 2"
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_head=pr_setup.pre_rebase_head,
            push_remote="origin", branch="feature",
        )
        assert findings.has_findings and findings.is_critical
        assert any("feature 2" in c for c in findings.dropped_commits)
        assert findings.dropped_files == ["f2.txt"]
        assert findings.modified_commits == []

        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!CAUTION]")
        assert "feature 2" in warning
        assert "f2.txt" in warning
        assert pr_setup.pre_rebase_head in warning  # recovery SHA

    def test_modified_commit_detected_as_warning(self, pr_setup):
        """A commit whose patch changed in-flight is flagged, amber tier."""
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        (work / "f2.txt").write_text("f2-changed")
        _git(work, "add", "f2.txt")
        _git(work, "commit", "--amend", "--no-edit")
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_head=pr_setup.pre_rebase_head,
            push_remote="origin", branch="feature",
        )
        assert findings.has_findings and not findings.is_critical
        assert any("feature 2" in c for c in findings.modified_commits)
        assert findings.dropped_commits == []
        assert findings.dropped_files == []

        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!WARNING]")
        assert "verify" in warning

    def test_clobbered_concurrent_push_detected(self, pr_setup):
        """A human push that lands mid-rebase and gets force-pushed over."""
        work, seed = pr_setup.work, pr_setup.seed

        # A human pushes to the PR branch while the rebase is running
        _git(seed, "checkout", "feature")
        _commit_file(seed, "hotfix.txt", "urgent", "human hotfix")
        _git(seed, "push", "origin", "feature")

        _git(work, "rebase", "origin/main")
        observed = observe_remote_head("origin", "feature", str(work))
        assert observed == _git_out(seed, "rev-parse", "HEAD")
        # The observation fetched the objects, so the commit is known locally
        _git(work, "cat-file", "-e", observed)

        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_head=observed,
            push_remote="origin", branch="feature",
        )
        assert findings.is_critical
        assert any("human hotfix" in c for c in findings.clobbered_commits)

        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!CAUTION]")
        assert "human hotfix" in warning

    def test_post_push_race_detected(self, pr_setup):
        """Someone force-pushes right after Kōan — the final check sees it."""
        work, seed = pr_setup.work, pr_setup.seed
        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")

        # Immediately afterwards the branch moves again
        _git(seed, "push", "origin", "main:feature", "--force")
        racer_sha = _git_out(seed, "rev-parse", "main")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_head=pr_setup.pre_rebase_head,
            push_remote="origin", branch="feature",
        )
        assert findings.has_findings and not findings.is_critical
        assert findings.remote_head_now == racer_sha

        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!WARNING]")
        assert racer_sha[:12] in warning

    def test_guard_degrades_to_none_on_bad_input(self, pr_setup):
        """An unusable baseline SHA must not raise — the guard steps aside."""
        findings = verify_content_preserved(
            "0" * 40, "origin/main", str(pr_setup.work),
        )
        assert findings is None

    def test_missing_pre_push_observation_skips_clobber_check(self, pr_setup):
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")
        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_head="",  # observation failed
            push_remote="origin", branch="feature",
        )
        assert findings is not None
        assert findings.clobbered_commits == []


class TestObserveRemoteHead:
    def test_returns_remote_tip(self, pr_setup):
        sha = observe_remote_head("origin", "feature", str(pr_setup.work))
        assert sha == pr_setup.pre_rebase_head

    def test_unreachable_remote_returns_empty(self, pr_setup):
        assert observe_remote_head("nope", "feature", str(pr_setup.work)) == ""

    def test_missing_branch_returns_empty(self, pr_setup):
        assert observe_remote_head("origin", "ghost", str(pr_setup.work)) == ""


class TestClassifyMissingCommits:
    def test_empty_commit_is_not_flagged(self, pr_setup):
        """Rebase legitimately drops empty commits — never report them lost."""
        work = pr_setup.work
        _git(work, "commit", "--allow-empty", "-m", "empty marker")
        empty_sha = _git_out(work, "rev-parse", "HEAD")
        dropped, modified, touched = _classify_missing_commits(
            str(work), [empty_sha], set(),
        )
        assert dropped == [] and modified == [] and touched == set()


class TestBuildPushWarning:
    def test_none_findings_render_empty(self):
        assert build_push_warning(None, "feature") == ""

    def test_clean_findings_render_empty(self):
        assert build_push_warning(PushGuardFindings(), "feature") == ""

    def test_single_alert_even_with_every_finding_type(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            pushed_head="b" * 40,
            dropped_commits=["abc1234 lost work"],
            modified_commits=["def5678 reshaped work"],
            dropped_files=["x.txt"],
            clobbered_commits=["9990000 human push"],
            remote_head_now="c" * 40,
        )
        warning = build_push_warning(findings, "feature")
        assert warning.count("[!") == 1  # parsimony: exactly one alert block
        assert warning.startswith("> [!CAUTION]")
        for token in ("lost work", "reshaped work", "x.txt", "human push",
                      "c" * 12, "a" * 40):
            assert token in warning

    def test_long_lists_are_truncated(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=[f"sha{i:04d} subject {i}" for i in range(12)],
        )
        warning = build_push_warning(findings, "feature")
        assert "… and 2 more" in warning
        assert "subject 11" not in warning

    def test_recovery_instructions_always_present(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            modified_commits=["abc1234 tweaked"],
        )
        warning = build_push_warning(findings, "feature")
        assert f"git fetch origin {'a' * 12}" in warning
        assert "feature-backup" in warning

    def test_non_github_provider_degrades_to_plain_prefix(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=["abc1234 lost"],
        )
        warning = build_push_warning(findings, "feature", provider="jira")
        assert warning.startswith("CAUTION:")
        assert "[!CAUTION]" not in warning
