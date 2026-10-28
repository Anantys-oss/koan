"""Disposable checkouts of a pull request's head for review.

A `/review` mission is dispatched after ``git_prep.prepare_project_branch``,
which checks out the **base** branch and hard-resets it to ``<remote>/<base>``.
The provider then runs with ``cwd=project_path``, so the reviewed code was never
on disk — while the prompts told the model to verify against "the checked-out
code" and the output rules referred to "the new file as it appears at the PR
head". Reviews were reading the wrong tree and reporting the findings as fact.

:func:`pinned_review_worktree` fixes that: fetch the PR head into a
uuid-namespaced ref, verify it matches GitHub's live ``headRefOid``, add a
detached worktree, verify again, assert it is clean, and remove it afterwards.

It lives in ``app/`` rather than inside the review skill so the core `/review`
path gets a pinned checkout by default, and so any other caller that needs one
can reuse it.
"""

import contextlib
import os
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Tuple, Union

# Refs and directories are namespaced with a uuid so concurrent reviews of the
# same PR cannot collide.
REF_NAMESPACE = "refs/koan-review"
WORKTREE_PREFIX = "review-"

# A SIGKILL (the stagnation monitor, an OOM, a deploy restart) skips the
# ``finally`` and orphans both a directory and a ref. Sweep anything older than
# this on the way in; on a long-running daemon the difference between doing this
# and not is unbounded disk growth.
DEFAULT_MAX_AGE_HOURS = 6

_GIT_TIMEOUT = 180


def _instance_dir() -> Path:
    """Private runtime tree. Derived from ``KOAN_ROOT``, like the rest of Kōan."""
    raw_root = os.environ.get("KOAN_ROOT", "").strip()
    root = Path(raw_root) if raw_root else Path(__file__).resolve().parents[2]
    return root / "instance"


def worktree_root() -> Path:
    """Directory holding review worktrees.

    Deliberately ``instance/tmp`` and NOT :func:`app.utils.koan_tmp_dir`. That
    resolves to ``$XDG_RUNTIME_DIR`` on Linux, which is a RAM-backed tmpfs
    capped at a fraction of physical memory — a full checkout of a large
    monorepo there will ENOSPC or push the box into swap. Override with
    ``KOAN_REVIEW_WORKTREE_DIR`` when a dedicated scratch volume exists.
    """
    override = os.environ.get("KOAN_REVIEW_WORKTREE_DIR", "").strip()
    return Path(override) if override else _instance_dir() / "tmp"


def _run_git(
    cwd: Union[str, Path], *args: str, check: bool = True, secret: str = "",
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        if secret:
            detail = detail.replace(secret, "***")
        raise RuntimeError(detail)
    return result


def sweep_stale_review_worktrees(
    project_path: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> int:
    """Remove orphaned review worktrees and refs. Returns how many it cleared.

    Best-effort and never raises: a failure to tidy must not fail a review.
    """
    cleared = 0
    root = worktree_root()
    cutoff = time.time() - max_age_hours * 3600

    try:
        _run_git(project_path, "worktree", "prune", check=False)
    except Exception as exc:
        # Best-effort tidy-up: never fail a review because cleanup could not
        # run. Still say so, or an unbounded disk leak stays invisible.
        print(
            f"[review_worktree] worktree prune failed in {project_path}: {exc}",
            file=sys.stderr,
        )
        return cleared

    try:
        entries = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        entries = []

    for entry in entries:
        if not entry.name.startswith(WORKTREE_PREFIX):
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        _run_git(
            project_path, "worktree", "remove", "--force", str(entry), check=False,
        )
        shutil.rmtree(entry, ignore_errors=True)
        cleared += 1

    # Ref tidy-up is advisory; a failure here leaves a stale ref, not a broken
    # review, and the next sweep retries it.
    with contextlib.suppress(Exception):
        listed = _run_git(
            project_path, "for-each-ref", "--format=%(refname)", REF_NAMESPACE,
            check=False,
        )
        for ref in (listed.stdout or "").split():
            token = ref.rsplit("-", 1)[-1]
            if any(p.name.endswith(token) for p in entries if p.exists()):
                continue
            _run_git(project_path, "update-ref", "-d", ref, check=False)

    return cleared


@contextmanager
def pinned_review_worktree(
    owner: str,
    repo: str,
    pr_number: Union[str, int],
    project_path: str,
    *,
    head_oid_fn: Optional[Callable[[str, str, str], Optional[str]]] = None,
    sweep: bool = True,
) -> Iterator[Tuple[str, str]]:
    """Yield ``(worktree_path, head_sha)`` for a clean checkout of the PR head.

    The SHA is verified twice — once on the fetched ref and once on the checked
    out ``HEAD`` — against GitHub's live ``headRefOid``, and the tree is asserted
    clean. Anything unexpected raises; callers must NOT fall back to
    ``project_path``, because reviewing the wrong tree is the defect this exists
    to remove.

    ``head_oid_fn`` defaults to ``review_runner._fetch_pr_head_oid``, imported
    lazily so existing ``patch("app.review_runner._fetch_pr_head_oid")`` in
    tests keeps working and to avoid an import cycle.
    """
    from app.rebase_pr import _resolve_fetch_source

    if head_oid_fn is None:
        from app.review_runner import _fetch_pr_head_oid as head_oid_fn  # noqa: N813

    pr_number = str(pr_number)
    if sweep:
        # Sweeping is opportunistic housekeeping; its failure must not block the
        # review that is about to run. sweep_* reports its own diagnostics.
        with contextlib.suppress(Exception):
            sweep_stale_review_worktrees(project_path)

    live_head = str(head_oid_fn(owner, repo, pr_number) or "")
    if not live_head:
        raise RuntimeError("live PR HEAD is unavailable")

    source, secret = _resolve_fetch_source(owner, repo, project_path)
    if not source:
        raise RuntimeError("no authenticated git fetch source is available")

    token = uuid.uuid4().hex
    temp_ref = f"{REF_NAMESPACE}/{pr_number}-{token}"
    root = worktree_root()
    root.mkdir(parents=True, exist_ok=True)
    worktree = root / f"{WORKTREE_PREFIX}{pr_number}-{token}"
    added = False

    try:
        _run_git(
            project_path,
            "fetch", "--no-tags", "--force", source,
            f"pull/{pr_number}/head:{temp_ref}",
            secret=secret,
        )
        fetched = _run_git(
            project_path, "rev-parse", temp_ref, secret=secret,
        ).stdout.strip()
        if fetched != live_head:
            raise RuntimeError(
                "fetched PR HEAD does not match GitHub "
                f"({fetched[:7] or 'unknown'} != {live_head[:7]})"
            )

        _run_git(
            project_path, "worktree", "add", "--detach", str(worktree), temp_ref,
            secret=secret,
        )
        added = True

        checked_out = _run_git(worktree, "rev-parse", "HEAD").stdout.strip()
        if checked_out != live_head:
            raise RuntimeError("detached review worktree has the wrong HEAD")

        dirty = _run_git(
            worktree, "status", "--porcelain", "--untracked-files=all",
        ).stdout.strip()
        if dirty:
            raise RuntimeError("detached review worktree is not clean")

        yield str(worktree), live_head
    finally:
        if added:
            _run_git(
                project_path, "worktree", "remove", "--force", str(worktree),
                check=False,
            )
        _run_git(project_path, "update-ref", "-d", temp_ref, check=False)
        shutil.rmtree(worktree, ignore_errors=True)
