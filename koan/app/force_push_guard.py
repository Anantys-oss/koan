"""
Kōan -- Force-push content-preservation guard.

A rebase force-push rewrites a PR branch's history. When it goes wrong, it
silently drops or alters content the PR previously carried — or clobbers
commits a human pushed while the rebase was running. This guard detects those
outcomes *after* the pipeline's final push and renders them as one un-missable
amber/red alert for the PR comment, always naming the pre-rebase head SHA so
the previous state stays recoverable.

Contract (specs/components/git-github.md): the guard detects and warns, it
never blocks — every internal failure degrades to "no findings" and must not
fail the push or the mission. It has no push-time authority by design: the
race between "someone pushes" and "Kōan force-pushes" cannot be closed
client-side, only detected, so the final `ls-remote` check runs after the
push (the "final check" approach agreed on incident review).

Checks performed by :func:`verify_content_preserved`:

1. **Dropped/modified content** — `git cherry <new> <old>` marks each commit
   of the old PR head that has no patch-id equivalent in the new history.
   Patch-id equivalence alone is too strict (an upstream squash-merge or mere
   context-line drift breaks it on perfectly clean rebases), so a marked
   commit is only reported when content-level cross-checks also fail: its
   files' bytes differ between the old and the pushed head, AND at least one
   line it added is no longer present verbatim at the pushed head. A surviving
   report is *dropped* when the commit's files vanished from the new PR diff
   entirely, otherwise *modified in-flight* (e.g. reshaped by conflict
   resolution) and listed for human verification.
2. **Clobbered concurrent pushes** — the remote tip is observed immediately
   before every force-push of the pipeline (step-6 push and any private-gate
   re-push; see :func:`observe_remote_head`). If an observed tip is neither
   the pre-rebase head nor part of the pushed history, someone pushed
   mid-pipeline and the force-push erased their commits; they are listed by
   SHA and subject.
3. **Post-push race** — a final `ls-remote` after the push; if the remote no
   longer points at the last SHA Kōan actually pushed, someone force-pushed
   right after us and Kōan's own rebase may have been overwritten. The
   comparison uses the recorded pushed SHA, not local HEAD, so a local commit
   that failed to push is never misreported as a race.
"""

import contextlib
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Set

from app.git_utils import run_git_strict
from app.github_alerts import build_alert

# Cap per-list rendering so a pathological rebase never floods the PR comment.
_MAX_LISTED = 10
# Cap per-commit analysis so a rewritten base history (hundreds of patch-id
# misses) cannot stall the pipeline with two subprocesses per commit.
_MAX_ANALYZED = 200

_GIT_ERRORS = (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError)


@dataclass
class PushGuardFindings:
    """Result of a post-push content-preservation check.

    Empty lists / fields mean the check ran and found nothing wrong.
    """

    pre_rebase_head: str = ""
    pushed_head: str = ""
    dropped_commits: List[str] = field(default_factory=list)
    modified_commits: List[str] = field(default_factory=list)
    dropped_files: List[str] = field(default_factory=list)
    clobbered_commits: List[str] = field(default_factory=list)
    remote_head_now: str = ""  # set only when the remote moved after our push
    analysis_truncated: int = 0  # patch-id misses beyond the analysis cap

    @property
    def has_findings(self) -> bool:
        return bool(
            self.dropped_commits
            or self.modified_commits
            or self.dropped_files
            or self.clobbered_commits
            or self.remote_head_now
            or self.analysis_truncated
        )

    @property
    def is_critical(self) -> bool:
        """True when content was actually lost (CAUTION tier)."""
        return bool(
            self.dropped_commits or self.dropped_files or self.clobbered_commits
        )


def _git(project_path: str, *args: str, timeout: int = 60) -> str:
    return run_git_strict(*args, cwd=project_path, timeout=timeout)


def observe_remote_head(remote: str, branch: str, project_path: str) -> str:
    """Snapshot the remote branch tip immediately before a force-push.

    Returns the SHA the remote currently serves for *branch* ("" when the
    observation fails — the guard then simply skips the clobber check).
    On success the branch is also fetched so the commits are available
    locally for post-push analysis (at observation time they are still
    reachable; after the force-push they may not be). The fetch also
    refreshes ``refs/remotes/<remote>/<branch>``, so a subsequent
    ``--force-with-lease`` compares against this freshest observation.
    """
    try:
        out = _git(
            project_path, "ls-remote", remote, f"refs/heads/{branch}", timeout=30,
        )
    except _GIT_ERRORS as e:
        print(f"[force_push_guard] pre-push ls-remote failed: {e}", file=sys.stderr)
        return ""
    sha = out.split()[0] if out.split() else ""
    if sha:
        with contextlib.suppress(_GIT_ERRORS):
            _git(
                project_path, "fetch", remote,
                f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}",
            )
    return sha


def _diff_files(project_path: str, head: str, target_ref: str) -> Set[str]:
    """Files changed by the PR at *head* relative to its base fork point."""
    base = _git(project_path, "merge-base", head, target_ref, timeout=30)
    out = _git(project_path, "diff", "--name-only", f"{base}..{head}")
    return {line for line in out.splitlines() if line.strip()}


def _cherry_missing(project_path: str, new_head: str, old_head: str) -> List[str]:
    """SHAs of *old_head* commits with no patch-id equivalent in *new_head*."""
    out = _git(project_path, "cherry", new_head, old_head)
    return [
        parts[1]
        for line in out.splitlines()
        if (parts := line.split()) and parts[0] == "+" and len(parts) > 1
    ]


def _describe(project_path: str, sha: str) -> str:
    try:
        return _git(
            project_path, "log", "-1", "--format=%h %s", sha, timeout=30,
        ).strip()
    except _GIT_ERRORS:
        return sha[:12]


def _commit_files(project_path: str, sha: str) -> Set[str]:
    try:
        out = _git(
            project_path, "diff-tree", "--no-commit-id", "--name-only", "-r", sha,
        )
        return {line for line in out.splitlines() if line.strip()}
    except _GIT_ERRORS:
        return set()


def _content_unchanged_at_head(
    project_path: str, files: Set[str], old_head: str, new_head: str,
) -> bool:
    """True when every file in *files* is byte-identical at both heads.

    This is what makes an upstream squash-merge count as preserved: the
    commit's patch-id has no equivalent anywhere, yet the pushed head carries
    exactly the content the old PR head carried.
    """
    out = _git(
        project_path, "diff", "--name-only", old_head, new_head,
        "--", *sorted(files),
    )
    return not out.strip()


def _added_lines(project_path: str, sha: str) -> Set[str]:
    """The non-blank lines a commit added, whitespace-normalized."""
    out = _git(project_path, "show", "--format=", "--unified=0", sha)
    return {
        line[1:].strip()
        for line in out.splitlines()
        if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
    }


def _added_lines_survive(
    project_path: str, sha: str, files: Set[str], pushed_head: str,
) -> bool:
    """True when every line the commit added still exists at the pushed head.

    This is what keeps routine context-line drift (upstream edits near a PR
    hunk shift the patch-id) from producing warnings on clean rebases. A
    commit with no added lines (deletion-only, binary, mode change) cannot be
    verified this way and stays flagged — conservative by design.
    """
    try:
        added = _added_lines(project_path, sha)
    except _GIT_ERRORS:
        return False
    if not added:
        return False
    head_lines: Set[str] = set()
    for f in sorted(files):
        with contextlib.suppress(_GIT_ERRORS):
            blob = _git(project_path, "show", f"{pushed_head}:{f}")
            head_lines |= {line.strip() for line in blob.splitlines()}
    return added <= head_lines


def _classify_missing_commits(
    project_path: str,
    missing: List[str],
    new_files: Set[str],
    pre_rebase_head: str,
    pushed_head: str,
) -> tuple:
    """Split patch-id-missing commits into (dropped, modified, files_touched).

    A missing patch-id alone is not loss — the commit is cleared when its
    files are byte-identical across both heads (upstream squash-merge) or when
    every line it added still exists at the pushed head (context drift). What
    survives those checks is *dropped* when none of its files remain in the
    new PR diff, otherwise *modified in-flight*.
    """
    dropped: List[str] = []
    modified: List[str] = []
    touched: Set[str] = set()
    for sha in missing:
        files = _commit_files(project_path, sha)
        if not files:
            continue  # empty commit — rebase legitimately drops these
        with contextlib.suppress(_GIT_ERRORS):
            if _content_unchanged_at_head(
                project_path, files, pre_rebase_head, pushed_head,
            ):
                continue  # content preserved verbatim (e.g. upstream squash)
        if _added_lines_survive(project_path, sha, files, pushed_head):
            continue  # patch-id drift only — every added line survived
        touched |= files
        if files & new_files:
            modified.append(_describe(project_path, sha))
        else:
            dropped.append(_describe(project_path, sha))
    return dropped, modified, touched


def _clobbered_commits(
    project_path: str, observed_heads: List[str], pre_rebase_head: str,
    pushed_head: str,
) -> List[str]:
    """Commits erased by a force-push that were not part of the rebase input.

    *observed_heads* are the remote tips seen immediately before each push of
    the pipeline (step-6 push and any private-gate re-push). A tip that is
    neither the pre-rebase head nor something we pushed means someone pushed
    mid-pipeline; its commits outside both histories were overwritten.
    """
    clobbered: List[str] = []
    seen: Set[str] = set()
    for observed in observed_heads:
        if not observed or observed in (pre_rebase_head, pushed_head):
            continue
        try:
            if _git(
                project_path, "merge-base", "--is-ancestor", observed, pushed_head,
                timeout=30,
            ) == "":
                continue  # observed tip is part of the pushed history
        except _GIT_ERRORS:
            pass  # not an ancestor (exit 1) or objects unknown — inspect below
        try:
            out = _git(
                project_path, "rev-list", "--max-count=25", observed,
                f"^{pre_rebase_head}", f"^{pushed_head}",
            )
            shas = [s for s in out.splitlines() if s.strip()]
        except _GIT_ERRORS as e:
            # Objects unavailable locally (observation fetch failed) — still
            # warn with the tip SHA so the human can recover it remotely.
            print(f"[force_push_guard] clobber rev-list failed: {e}", file=sys.stderr)
            shas = []
            if observed not in seen:
                seen.add(observed)
                clobbered.append(f"{observed[:12]} (tip of the overwritten push)")
        for sha in shas:
            if sha not in seen:
                seen.add(sha)
                clobbered.append(_describe(project_path, sha))
    return clobbered


def _remote_moved_after_push(
    project_path: str, push_remote: str, branch: str, pushed_head: str,
) -> str:
    """Final post-push check: does the remote still point at what we pushed?"""
    if not push_remote or not branch:
        return ""
    try:
        out = _git(
            project_path, "ls-remote", push_remote, f"refs/heads/{branch}",
            timeout=30,
        )
    except _GIT_ERRORS as e:
        print(f"[force_push_guard] post-push ls-remote failed: {e}", file=sys.stderr)
        return ""
    sha = out.split()[0] if out.split() else ""
    return sha if sha and sha != pushed_head else ""


def verify_content_preserved(
    pre_rebase_head: str,
    target_ref: str,
    project_path: str,
    *,
    pre_push_remote_heads: Optional[List[str]] = None,
    push_remote: str = "",
    branch: str = "",
    pushed_head: str = "",
) -> Optional[PushGuardFindings]:
    """Run the full post-push content-preservation check.

    *pushed_head* is the SHA the pipeline last successfully pushed; it is the
    reference for every comparison (a local commit that failed to push must
    not skew the analysis or fake a race). When empty, falls back to HEAD —
    the PR branch must then still be checked out. Returns findings (possibly
    empty — check ran clean), or None when the guard itself could not run.
    Never raises.
    """
    try:
        if not pushed_head:
            pushed_head = _git(project_path, "rev-parse", "HEAD", timeout=30)
        old_files = _diff_files(project_path, pre_rebase_head, target_ref)
        new_files = _diff_files(project_path, pushed_head, target_ref)
        missing = _cherry_missing(project_path, pushed_head, pre_rebase_head)
        truncated = max(0, len(missing) - _MAX_ANALYZED)
        if truncated:
            print(
                f"[force_push_guard] analysis capped: {len(missing)} patch-id "
                f"misses, analyzing first {_MAX_ANALYZED}",
                file=sys.stderr,
            )
        dropped, modified, touched = _classify_missing_commits(
            project_path, missing[:_MAX_ANALYZED], new_files,
            pre_rebase_head, pushed_head,
        )
        return PushGuardFindings(
            pre_rebase_head=pre_rebase_head,
            pushed_head=pushed_head,
            dropped_commits=dropped,
            modified_commits=modified,
            dropped_files=sorted((old_files - new_files) & touched),
            clobbered_commits=_clobbered_commits(
                project_path, pre_push_remote_heads or [],
                pre_rebase_head, pushed_head,
            ),
            remote_head_now=_remote_moved_after_push(
                project_path, push_remote, branch, pushed_head,
            ),
            analysis_truncated=truncated,
        )
    except _GIT_ERRORS as e:
        print(
            f"[force_push_guard] content check could not run: {e}",
            file=sys.stderr,
        )
        return None


def _listed(items: List[str]) -> List[str]:
    """Render '<sha> <subject>' entries as indented bullets, capped.

    Subjects are attacker-influenced (anyone who can commit controls them), so
    they are rendered inside code spans — GitHub does not linkify, mention, or
    interpret markdown there. Embedded backticks are squashed so a subject
    cannot break out of its span.
    """
    shown = []
    for item in items[:_MAX_LISTED]:
        sha, _, subject = item.partition(" ")
        subject = subject.replace("`", "'").strip()
        shown.append(f"  - `{sha}` `{subject}`" if subject else f"  - `{sha}`")
    if len(items) > _MAX_LISTED:
        shown.append(f"  - … and {len(items) - _MAX_LISTED} more")
    return shown


def _listed_files(files: List[str]) -> str:
    rendered = ", ".join(f"`{f}`" for f in files[:_MAX_LISTED])
    if len(files) > _MAX_LISTED:
        rendered += f", … and {len(files) - _MAX_LISTED} more"
    return rendered


def build_push_warning(
    findings: Optional[PushGuardFindings],
    branch: str,
    provider: str = "github",
    remote: str = "origin",
) -> str:
    """Render findings as ONE alert block ("" when there is nothing to say).

    CAUTION when content was lost (dropped commits/files, clobbered pushes),
    WARNING when it only changed shape or the remote raced us. Exactly one
    alert regardless of how many findings — per the parsimony rule in
    specs/components/comment-formatting.md. *remote* is the remote the push
    actually targeted, used in the recovery command.
    """
    if findings is None or not findings.has_findings:
        return ""
    lines: List[str] = [
        f"**Force-push safety check — the rewrite of `{branch}` needs attention.**",
        "",
    ]
    if findings.dropped_commits:
        lines.append("Commits whose changes are GONE from this PR after the rebase:")
        lines.extend(_listed(findings.dropped_commits))
    if findings.dropped_files:
        lines.append(
            "Files whose PR changes disappeared: "
            + _listed_files(findings.dropped_files)
        )
    if findings.clobbered_commits:
        lines.append(
            "Commits pushed by someone else DURING the rebase were overwritten:"
        )
        lines.extend(_listed(findings.clobbered_commits))
    if findings.modified_commits:
        lines.append(
            "Commits whose patch changed in-flight and whose content could not "
            "be verified as preserved — check these survived:"
        )
        lines.extend(_listed(findings.modified_commits))
    if findings.remote_head_now:
        lines.append(
            f"The branch moved again right after the push (remote now at "
            f"`{findings.remote_head_now[:12]}`) — this rebase may itself have "
            f"been overwritten; reconcile before pushing again."
        )
    if findings.analysis_truncated:
        lines.append(
            f"Patch analysis was capped: {findings.analysis_truncated} further "
            f"rewritten commits were not analyzed (a base-history rewrite is "
            f"likely) — review the branch history manually."
        )
    lines.append("")
    lines.append(
        f"Previous PR head: `{findings.pre_rebase_head}` — everything it "
        f"contained is recoverable: "
        f"`git fetch {remote} {findings.pre_rebase_head} && "
        f"git switch -c {branch}-backup FETCH_HEAD`"
    )
    kind = "CAUTION" if findings.is_critical else "WARNING"
    return build_alert(kind, "\n".join(lines), provider=provider)
