"""Cold-boot hydration of instance/ from a private git repo.

On a fresh deploy the instance/ volume is empty. If KOAN_INSTANCE_REPO is set,
clone the operator's private instance repo into it so Kōan boots as *their*
instance — soul, projects, skills, memory, journal, and in-flight mission state.

The FULL tree (including .git) is restored, not just config: the running agent
already commits AND pushes the whole instance/ dir via
mission_runner.commit_instance(), so state files are part of the tracked repo
and are exactly what makes resume-after-redeploy work.

Fail-open: any error leaves instance/ untouched so the caller falls back to the
instance.example/ template. Boot must never hard-fail on a clone error.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from app.utils import koan_tmp_dir

CLONE_TIMEOUT = 300  # seconds


def _configured_repo(repo: Optional[str]) -> Optional[str]:
    return repo if repo is not None else os.environ.get("KOAN_INSTANCE_REPO") or None


def _configured_branch(branch: Optional[str]) -> Optional[str]:
    return branch if branch is not None else os.environ.get("KOAN_INSTANCE_REPO_BRANCH") or None


def _already_hydrated(instance: Path) -> bool:
    return (instance / "missions.md").exists() or (instance / ".git").is_dir()


def _run(cmd: List[str]) -> bool:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT,
            env={**os.environ},
        )
        if proc.returncode != 0:
            sys.stderr.write(f"[instance_hydrator] {cmd[0]} failed: {proc.stderr.strip()}\n")
            return False
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"[instance_hydrator] {cmd[0]} error: {exc}\n")
        return False


def _clone(repo: str, dest: str, branch: Optional[str]) -> bool:
    """Prefer `gh repo clone` (uses GH_TOKEN); fall back to `git clone`."""
    git_args: List[str] = []
    if branch:
        git_args += ["--branch", branch]
    slug_like = "://" not in repo and repo.count("/") == 1
    if slug_like and shutil.which("gh") and os.environ.get("GH_TOKEN"):
        if _run(["gh", "repo", "clone", repo, dest, "--", *git_args]):
            return True
    # git fallback (self-hosted URLs, or gh unavailable). Private HTTPS repos
    # need a credential helper — railway_setup_git / `gh auth setup-git`.
    return _run(["git", "clone", *git_args, repo, dest])


def hydrate_instance_from_repo(
    instance_dir: str, repo: Optional[str] = None, branch: Optional[str] = None
) -> bool:
    """Clone the configured instance repo into instance_dir.

    Returns True only if instance/ was freshly hydrated from the repo. Returns
    False (fail-open) when unconfigured, already hydrated, or on any error —
    the caller then seeds from instance.example/.
    """
    repo = _configured_repo(repo)
    if not repo:
        return False
    instance = Path(instance_dir)
    if _already_hydrated(instance):
        return False

    branch = _configured_branch(branch)
    tmp = tempfile.mkdtemp(prefix="koan-instance-", dir=koan_tmp_dir())
    try:
        if not _clone(repo, tmp, branch):
            return False
        instance.mkdir(parents=True, exist_ok=True)
        # copytree incl. .git so commit_instance() keeps pushing; dirs_exist_ok
        # handles the pre-existing (empty) bind-mount point.
        shutil.copytree(tmp, str(instance), dirs_exist_ok=True)
        sys.stderr.write(f"[instance_hydrator] hydrated instance/ from {repo}\n")
        return True
    except OSError as exc:
        sys.stderr.write(f"[instance_hydrator] copy failed: {exc}\n")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pull_instance_repo(instance_dir: str) -> bool:
    """Reconcile operator edits pushed to the remote (opt-in periodic sync).

    `pull --rebase --autostash` so any stray local write survives and the
    agent's own commits replay on top — keeping the commit_instance() push
    path fast-forwardable. Graceful no-op when instance/ is not a git repo.
    """
    if not (Path(instance_dir) / ".git").is_dir():
        return False
    return _run(["git", "-C", instance_dir, "pull", "--rebase", "--autostash"])


def _main(argv: List[str]) -> int:
    # Usage: python -m app.instance_hydrator hydrate <instance_dir>
    if len(argv) >= 3 and argv[1] == "hydrate":
        return 0 if hydrate_instance_from_repo(argv[2]) else 1
    if len(argv) >= 3 and argv[1] == "pull":
        return 0 if pull_instance_repo(argv[2]) else 1
    sys.stderr.write("usage: instance_hydrator <hydrate|pull> <instance_dir>\n")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
