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
   of the old PR head that has no patch-id equivalent in the new history
   (which includes the freshly-fetched base, so changes that landed upstream
   count as preserved). A marked commit whose files all vanished from the new
   PR diff is *dropped*; otherwise it is *modified in-flight* (e.g. reshaped
   by conflict resolution) and listed for human verification.
2. **Clobbered concurrent pushes** — if the remote tip observed immediately
   before the force-push (see :func:`observe_remote_head`) differs from the
   pre-rebase head, someone pushed mid-rebase and the force-push erased their
   commits; they are listed by SHA and subject.
3. **Post-push race** — a final `ls-remote` after the push; if the remote no
   longer points at what Kōan pushed, someone force-pushed right after us and
   Kōan's own rebase may have been overwritten.
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

    @property
    def has_findings(self) -> bool:
        return bool(
            self.dropped_commits
            or self.modified_commits
            or self.dropped_files
            or self.clobbered_commits
            or self.remote_head_now
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


def _classify_missing_commits(
    project_path: str, missing: List[str], new_files: Set[str],
) -> tuple:
    """Split patch-missing commits into (dropped, modified, files_they_touched).

    A commit none of whose files still appear in the new PR diff has vanished
    entirely (*dropped*); one whose files are still part of the diff was
    reshaped in-flight (*modified*) and needs human verification.
    """
    dropped: List[str] = []
    modified: List[str] = []
    touched: Set[str] = set()
    for sha in missing:
        files = _commit_files(project_path, sha)
        if not files:
            continue  # empty commit — rebase legitimately drops these
        touched |= files
        if files & new_files:
            modified.append(_describe(project_path, sha))
        else:
            dropped.append(_describe(project_path, sha))
    return dropped, modified, touched


def _clobbered_commits(
    project_path: str, pre_push_remote_head: str, pre_rebase_head: str,
    pushed_head: str,
) -> List[str]:
    """Commits erased by the force-push that were not part of the rebase input.

    These exist only when someone pushed to the branch between the pipeline's
    checkout and its force-push: reachable from the remote tip observed just
    before the push, but from neither the pre-rebase head nor the new history.
    """
    if not pre_push_remote_head:
        return []
    if pre_push_remote_head in (pre_rebase_head, pushed_head):
        return []
    try:
        out = _git(
            project_path, "rev-list", "--max-count=25", pre_push_remote_head,
            f"^{pre_rebase_head}", f"^{pushed_head}",
        )
    except _GIT_ERRORS as e:
        # Objects unavailable locally (observation fetch failed) — still warn
        # with the tip SHA so the human can recover it from the remote.
        print(f"[force_push_guard] clobber rev-list failed: {e}", file=sys.stderr)
        return [f"{pre_push_remote_head[:12]} (tip of the overwritten push)"]
    return [_describe(project_path, sha) for sha in out.splitlines() if sha.strip()]


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
    pre_push_remote_head: str = "",
    push_remote: str = "",
    branch: str = "",
) -> Optional[PushGuardFindings]:
    """Run the full post-push content-preservation check.

    Must run while the rebased PR branch is still checked out (HEAD is taken
    as the pushed state, so it also covers follow-up pushes such as private
    review-gate fixes). Returns findings (possibly empty — check ran clean),
    or None when the guard itself could not run. Never raises.
    """
    try:
        pushed_head = _git(project_path, "rev-parse", "HEAD", timeout=30)
        old_files = _diff_files(project_path, pre_rebase_head, target_ref)
        new_files = _diff_files(project_path, pushed_head, target_ref)
        missing = _cherry_missing(project_path, pushed_head, pre_rebase_head)
        dropped, modified, touched = _classify_missing_commits(
            project_path, missing, new_files,
        )
        return PushGuardFindings(
            pre_rebase_head=pre_rebase_head,
            pushed_head=pushed_head,
            dropped_commits=dropped,
            modified_commits=modified,
            dropped_files=sorted((old_files - new_files) & touched),
            clobbered_commits=_clobbered_commits(
                project_path, pre_push_remote_head, pre_rebase_head, pushed_head,
            ),
            remote_head_now=_remote_moved_after_push(
                project_path, push_remote, branch, pushed_head,
            ),
        )
    except _GIT_ERRORS as e:
        print(
            f"[force_push_guard] content check could not run: {e}",
            file=sys.stderr,
        )
        return None


def _listed(items: List[str]) -> List[str]:
    """Render '<sha> <subject>' entries as indented bullets, capped."""
    shown = []
    for item in items[:_MAX_LISTED]:
        sha, _, subject = item.partition(" ")
        shown.append(f"  - `{sha}` {subject}".rstrip())
    if len(items) > _MAX_LISTED:
        shown.append(f"  - … and {len(items) - _MAX_LISTED} more")
    return shown


def build_push_warning(
    findings: Optional[PushGuardFindings],
    branch: str,
    provider: str = "github",
) -> str:
    """Render findings as ONE alert block ("" when there is nothing to say).

    CAUTION when content was lost (dropped commits/files, clobbered pushes),
    WARNING when it only changed shape or the remote raced us. Exactly one
    alert regardless of how many findings — per the parsimony rule in
    specs/components/comment-formatting.md.
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
        files = ", ".join(f"`{f}`" for f in findings.dropped_files[:_MAX_LISTED])
        lines.append(f"Files whose PR changes disappeared: {files}")
    if findings.clobbered_commits:
        lines.append(
            "Commits pushed by someone else DURING the rebase were overwritten:"
        )
        lines.extend(_listed(findings.clobbered_commits))
    if findings.modified_commits:
        lines.append(
            "Commits reshaped in-flight (e.g. by conflict resolution) — "
            "verify their changes survived:"
        )
        lines.extend(_listed(findings.modified_commits))
    if findings.remote_head_now:
        lines.append(
            f"The branch moved again right after the push (remote now at "
            f"`{findings.remote_head_now[:12]}`) — this rebase may itself have "
            f"been overwritten; reconcile before pushing again."
        )
    lines.append("")
    lines.append(
        f"Previous PR head: `{findings.pre_rebase_head}` — everything it "
        f"contained is recoverable: "
        f"`git fetch origin {findings.pre_rebase_head[:12]} && "
        f"git switch -c {branch}-backup FETCH_HEAD`"
    )
    kind = "CAUTION" if findings.is_critical else "WARNING"
    return build_alert(kind, "\n".join(lines), provider=provider)
