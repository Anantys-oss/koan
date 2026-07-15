"""Isolate koan's own repo-development tooling from runtime CLI sessions.

A deployed koan instance is a git clone of the koan repo, so the repo's
contributor tooling — root CLAUDE.md / AGENTS.md and the .claude/ project-skills
tree (brain, speckit-*) — lives at KOAN_ROOT. Several runtime CLI sessions
(chat bridge, contemplative, rituals, outbox formatting) run with
cwd=KOAN_ROOT, so Claude Code auto-loads that contributor context and it leaks
into operator-facing output (e.g. advising operators to run /brain sync).

This module relocates those artifacts out of the auto-load path and marks their
tracked paths skip-worktree so `git status` stays clean and fast-forward
self-updates don't fight the move. Idempotent; safe on every startup and after
every self-update.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Union

from app.git_utils import run_git
from app.run_log import log

QUARANTINE_DIRNAME = ".repo-tooling-quarantine"

# Repo-root artifacts Claude Code auto-loads when cwd=KOAN_ROOT.
# Relative to KOAN_ROOT. Nested CLAUDE.md files (koan/**/CLAUDE.md) are NOT
# included: they only auto-load when a session reads files under that subtree,
# which the four runtime session types never do (they read instance/ state).
_ARTIFACTS = ("CLAUDE.md", "AGENTS.md", ".claude")


def _is_git_repo(root: Path) -> bool:
    rc, out, _ = run_git("rev-parse", "--is-inside-work-tree", cwd=str(root))
    return rc == 0 and out.strip() == "true"


def _tracked_files_under(root: Path, rel: str) -> list[str]:
    """Tracked paths (files) at/under `rel`, relative to root. Empty if none."""
    rc, out, _ = run_git("ls-files", "-z", "--", rel, cwd=str(root))
    if rc != 0 or not out:
        return []
    return [p for p in out.split("\0") if p]


def _set_skip_worktree(root: Path, files: list[str], skip: bool) -> None:
    flag = "--skip-worktree" if skip else "--no-skip-worktree"
    for f in files:
        run_git("update-index", flag, "--", f, cwd=str(root))


def sanitize_repo_tooling(koan_root: Union[str, Path]) -> list[str]:
    """Relocate repo-dev artifacts out of KOAN_ROOT's Claude auto-load path.

    Returns the list of artifact paths (relative to koan_root) relocated during
    this call (empty when already isolated, untracked, or not a git repo).
    """
    root = Path(koan_root)
    if not _is_git_repo(root):
        return []

    quarantine = root / "instance" / QUARANTINE_DIRNAME
    relocated: list[str] = []
    for rel in _ARTIFACTS:
        src = root / rel
        if not src.exists():
            continue  # already relocated, or never present
        tracked = _tracked_files_under(root, rel)
        if not tracked:
            continue  # untracked operator file — leave it alone
        # Mark skip-worktree BEFORE moving so the physical absence reads as
        # clean and ff-updates don't try to restore it.
        _set_skip_worktree(root, tracked, skip=True)
        dest = quarantine / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        src.replace(dest)  # atomic within the same filesystem
        relocated.append(rel)
        log("guard", f"Isolated repo tooling: {rel} -> instance/{QUARANTINE_DIRNAME}/{rel}")
    return relocated


def restore_repo_tooling(koan_root: Union[str, Path]) -> list[str]:
    """Inverse of sanitize_repo_tooling: move artifacts back and clear
    skip-worktree. Returns the list restored. Best-effort; used for operators
    who want to develop koan on this checkout again."""
    root = Path(koan_root)
    quarantine = root / "instance" / QUARANTINE_DIRNAME
    if not quarantine.exists():
        return []
    restored: list[str] = []
    for rel in _ARTIFACTS:
        src = quarantine / rel
        if not src.exists():
            continue
        dest = root / rel
        if dest.exists():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dest)
        if _is_git_repo(root):
            _set_skip_worktree(root, _tracked_files_under(root, rel), skip=False)
        restored.append(rel)
        log("guard", f"Restored repo tooling: instance/{QUARANTINE_DIRNAME}/{rel} -> {rel}")
    return restored
