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
   commit is only reported when content-level cross-checks also fail: the
   old→pushed diff restricted to the commit's files is non-empty AND that
   diff *removes* at least one line the commit added (i.e. the change was
   undone, not merely shifted). A surviving report is *dropped* when the
   commit's files vanished from the new PR diff entirely, otherwise *modified
   in-flight* (e.g. reshaped by conflict resolution) and listed for human
   verification. Anything the checks cannot evaluate (unreadable file list,
   deletion-only/binary commit, git failure) stays flagged — the guard never
   converts its own blind spot into a clean bill of health.
2. **Lost merge resolutions** — the pipeline rebases without
   `--rebase-merges`, so a merge commit on the PR is replayed as its ordinary
   first-parent commits and any conflict-resolution content unique to the
   merge is silently discarded. `git cherry` never emits merge commits, so
   they are enumerated separately (`rev-list --merges`) and screened through
   the same content checks using their combined diff (`diff-tree --cc`),
   which by construction contains exactly the lines that differ from *every*
   parent. Trivial merges (empty combined diff) carry no unique content and
   are skipped.
3. **Clobbered concurrent pushes** — the remote tip is observed immediately
   before every force-push of the pipeline (step-6 push and any private-gate
   re-push; see :func:`observe_remote_head`) — and again when
   `--force-with-lease` is rejected, since the rejection itself means the
   remote moved after the first observation and the plain `--force` fallback
   is about to overwrite whatever moved it. If an observed tip is neither
   the pre-rebase head nor part of the pushed history, someone pushed
   mid-pipeline and the force-push erased their commits; they are listed by
   SHA and subject.
4. **Post-push race** — a final `ls-remote` after the push; if the remote no
   longer points at the last SHA Kōan actually pushed, someone force-pushed
   right after us and Kōan's own rebase may have been overwritten. The
   comparison uses the recorded pushed SHA, not local HEAD, so a local commit
   that failed to push is never misreported as a race.
"""

import contextlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from app.git_utils import run_git_strict
from app.github_alerts import build_alert

# Cap per-list rendering so a pathological rebase never floods the PR comment.
_MAX_LISTED = 10
# Cap per-commit analysis so a rewritten base history (hundreds of patch-id
# misses) cannot stall the pipeline with two subprocesses per commit.
_MAX_ANALYZED = 200

_GIT_ERRORS = (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError)

# A SHA safe to embed in a generated branch name without quoting.
_SHA_RE = re.compile(r"\A[0-9a-fA-F]{7,40}\Z")


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


def _merge_base(project_path: str, head: str, target_ref: str) -> str:
    return _git(project_path, "merge-base", head, target_ref, timeout=30)


def _diff_files(project_path: str, head: str, target_ref: str) -> Set[str]:
    """Files changed by the PR at *head* relative to its base fork point."""
    base = _merge_base(project_path, head, target_ref)
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


def _merge_commits(project_path: str, base: str, head: str) -> List[str]:
    """Merge commits in ``base..head`` — invisible to ``git cherry``.

    A plain (non-``--rebase-merges``) rebase replays only the first-parent
    commits, so any conflict resolution that lives *in* a merge commit is
    dropped without a trace. They are screened separately for that reason.
    """
    out = _git(project_path, "rev-list", "--merges", f"{base}..{head}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _describe(project_path: str, sha: str) -> str:
    try:
        return _git(
            project_path, "log", "-1", "--format=%h %s", sha, timeout=30,
        ).strip()
    except _GIT_ERRORS:
        return sha[:12]


def _parent_count(project_path: str, sha: str) -> int:
    out = _git(project_path, "rev-list", "--parents", "-1", sha, timeout=30)
    return max(1, len(out.split()) - 1)


def _commit_files(project_path: str, sha: str, *, merge: bool = False) -> Set[str]:
    """Files the commit changed. Raises when git cannot answer.

    For a merge this is the *combined* file list (``--cc``): only files whose
    content in the merge differs from every parent, i.e. the resolution.
    A merge that merely replays its parents yields an empty set.
    """
    args = ["diff-tree", "--no-commit-id", "--name-only", "-r", sha]
    if merge:
        args.insert(1, "--cc")
    out = _git(project_path, *args)
    return {line for line in out.splitlines() if line.strip()}


def _added_lines(project_path: str, sha: str, *, merge: bool = False) -> Set[str]:
    """The non-blank lines a commit contributed, whitespace-normalized.

    For a merge, the combined diff is used and only lines that differ from
    *every* parent count (they carry ``+`` in every diff column) — those are
    the conflict-resolution lines a plain rebase would silently discard.
    """
    if merge:
        columns = _parent_count(project_path, sha)
        out = _git(
            project_path, "diff-tree", "--cc", "--no-commit-id", "-r",
            "--unified=0", sha,
        )
    else:
        columns = 1
        out = _git(project_path, "show", "--format=", "--unified=0", sha)
    marker = "+" * columns
    return {
        line[columns:].strip()
        for line in out.splitlines()
        if line.startswith(marker)
        and not line.startswith("+++")
        and line[columns:].strip()
    }


def _content_preserved(
    project_path: str,
    sha: str,
    files: Set[str],
    old_head: str,
    pushed_head: str,
    *,
    merge: bool = False,
) -> bool:
    """True when nothing this commit contributed was undone by the rewrite.

    A patch-id miss is not loss by itself. Two ways it is still safe:

    * the commit's files are byte-identical at both heads — the old→pushed
      diff restricted to them is empty (upstream squash-merge); or
    * that diff does not *remove* any line the commit added — the hunk only
      moved or its surroundings changed (context-line drift on a clean
      rebase). Comparing against the removed side, rather than looking each
      added line up anywhere in the file, is what stops an identical line
      elsewhere in the same file from masking a genuinely reverted change.

    A commit that contributed no added lines (deletion-only, binary, mode
    change) cannot be verified this way and is reported — conservative by
    design. Git failures propagate; the caller reports them as unverifiable.
    """
    diff = _git(
        project_path, "diff", "--unified=0", old_head, pushed_head,
        "--", *sorted(files),
    )
    if not diff.strip():
        return True  # content preserved verbatim (e.g. upstream squash)
    added = _added_lines(project_path, sha, merge=merge)
    if not added:
        return False
    removed = {
        line[1:].strip()
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---") and line[1:].strip()
    }
    return not (added & removed)


def _classify_missing_commits(
    project_path: str,
    candidates: List[Tuple[str, bool]],
    new_files: Set[str],
    pre_rebase_head: str,
    pushed_head: str,
) -> tuple:
    """Split unmatched commits into (dropped, modified, files_touched).

    *candidates* are ``(sha, is_merge)`` pairs: patch-id-missing commits from
    ``git cherry`` plus every merge commit of the pre-rebase range. A commit
    is cleared only when :func:`_content_preserved` says so; anything the
    check could not evaluate is reported as *modified* (unverified) rather
    than silently cleared. What survives is *dropped* when none of its files
    remain in the new PR diff, otherwise *modified in-flight*.
    """
    dropped: List[str] = []
    modified: List[str] = []
    touched: Set[str] = set()
    for sha, is_merge in candidates:
        try:
            files = _commit_files(project_path, sha, merge=is_merge)
        except _GIT_ERRORS as e:
            print(
                f"[force_push_guard] file list unavailable for {sha[:12]}: {e}",
                file=sys.stderr,
            )
            modified.append(_describe(project_path, sha))
            continue
        if not files:
            # Empty commit, or a merge that resolved nothing of its own —
            # a rebase legitimately drops these.
            continue
        try:
            preserved = _content_preserved(
                project_path, sha, files, pre_rebase_head, pushed_head,
                merge=is_merge,
            )
        except _GIT_ERRORS as e:
            print(
                f"[force_push_guard] content check failed for {sha[:12]}: {e}",
                file=sys.stderr,
            )
            preserved = False
        if preserved:
            continue
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
    not skew the analysis or fake a race). It is **required**: without a
    confirmed pushed SHA there is nothing trustworthy to compare against —
    local HEAD may carry commits that never reached the remote — so the guard
    reports itself skipped (None) rather than analyzing the wrong tree.
    Returns findings (possibly empty — check ran clean), or None when the
    guard itself could not run. Never raises.
    """
    if not pushed_head:
        print(
            "[force_push_guard] no confirmed pushed SHA — guard skipped",
            file=sys.stderr,
        )
        return None
    try:
        old_files = _diff_files(project_path, pre_rebase_head, target_ref)
        new_files = _diff_files(project_path, pushed_head, target_ref)
        candidates = [
            (sha, False)
            for sha in _cherry_missing(project_path, pushed_head, pre_rebase_head)
        ]
        candidates += [
            (sha, True)
            for sha in _merge_commits(
                project_path,
                _merge_base(project_path, pre_rebase_head, target_ref),
                pre_rebase_head,
            )
        ]
        truncated = max(0, len(candidates) - _MAX_ANALYZED)
        if truncated:
            print(
                f"[force_push_guard] analysis capped: {len(candidates)} "
                f"unmatched commits, analyzing first {_MAX_ANALYZED}",
                file=sys.stderr,
            )
        dropped, modified, touched = _classify_missing_commits(
            project_path, candidates[:_MAX_ANALYZED], new_files,
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


def _code(text: str) -> str:
    """Render attacker-influenced text as a code span it cannot escape.

    Commit subjects, branch names and paths are all controlled by whoever can
    push. Inside a code span GitHub does not linkify, mention, or interpret
    markdown; embedded backticks are squashed so the span cannot be
    terminated early.
    """
    return f"`{text.replace('`', chr(39))}`"


def _recovery_command(remote: str, sha: str) -> str:
    """A copy-pasteable recovery command with no injectable input.

    A maintainer is expected to paste this into a shell, so nothing
    attacker-controlled may reach it unquoted: git ref names legally contain
    ``$()``, backticks, ``;`` and ``&``. The remote is shell-quoted and the
    backup branch is derived from the (validated) SHA rather than from the PR
    branch name, so the command carries no PR-controlled text at all.
    """
    safe_sha = sha if _SHA_RE.match(sha) else ""
    backup = f"koan-prerebase-{safe_sha[:12]}" if safe_sha else "koan-prerebase-backup"
    return (
        f"git fetch {shlex.quote(remote)} {shlex.quote(sha)} && "
        f"git switch -c {backup} FETCH_HEAD"
    )


def _listed(items: List[str]) -> List[str]:
    """Render '<sha> <subject>' entries as indented bullets, capped."""
    shown = []
    for item in items[:_MAX_LISTED]:
        sha, _, subject = item.partition(" ")
        subject = subject.strip()
        shown.append(
            f"  - {_code(sha)} {_code(subject)}" if subject else f"  - {_code(sha)}"
        )
    if len(items) > _MAX_LISTED:
        shown.append(f"  - … and {len(items) - _MAX_LISTED} more")
    return shown


def _listed_files(files: List[str]) -> str:
    rendered = ", ".join(_code(f) for f in files[:_MAX_LISTED])
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
        f"**Force-push safety check — the rewrite of {_code(branch)} needs "
        f"attention.**",
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
        f"Previous PR head: {_code(findings.pre_rebase_head)} — everything it "
        f"contained is recoverable: "
        + _code(_recovery_command(remote, findings.pre_rebase_head))
    )
    kind = "CAUTION" if findings.is_critical else "WARNING"
    return build_alert(kind, "\n".join(lines), provider=provider)
