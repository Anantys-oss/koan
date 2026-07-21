"""Kōan /commit skill — queue a conventional-commit mission."""

import subprocess
from pathlib import Path
from typing import Optional, Tuple


def handle(ctx):
    """Handle /commit command — queue a conventional commit mission.

    Usage:
        /commit                       -- commit in the default project
        /commit <project>             -- commit in a named project
        /commit <project> <hint>      -- named project + message guidance
        /commit <hint>                -- default project + message guidance

    Alias ``cm`` is preferred over ``ci`` so continuous-integration commands
    stay unambiguous (see ``/ci_check``).
    """
    args = (ctx.args or "").strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /commit [project] [message hint]\n\n"
            "Analyzes staged (and unstaged) git changes, generates a\n"
            "conventional commit message, and creates the commit.\n\n"
            "Options:\n"
            "  project       Project name from projects.yaml (optional)\n"
            "  message hint  Free-text guidance for the commit subject\n\n"
            "Examples:\n"
            "  /commit\n"
            "  /commit myproject\n"
            "  /cm myproject fix the login timeout\n"
            "  /commit fix the login timeout"
        )

    return _queue_commit(ctx, args)


def _queue_commit(ctx, args: str) -> str:
    """Resolve project, validate git state, and queue the mission."""
    from app.utils import (
        get_known_projects,
        insert_pending_mission,
    )

    project_name, project_path, message_hint = _resolve_project_and_hint(args)
    if not project_path:
        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        if project_name:
            return (
                f"\u274c Unknown project '{project_name}'.\n"
                f"Known projects: {known}"
            )
        return "\u274c No projects configured."

    git_error = _validate_git_state(project_path)
    if git_error:
        return git_error

    hint_suffix = f" {message_hint}" if message_hint else ""
    mission_entry = f"- [project:{project_name}] /commit{hint_suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    hint_text = f" (hint: {message_hint})" if message_hint else ""
    return f"\U0001f4dd Commit queued for {project_name}{hint_text}"


def _resolve_project_and_hint(
    args: str,
) -> Tuple[Optional[str], Optional[str], str]:
    """Resolve project name/path and optional message hint from args.

    Rules:
      - empty args → first known project, no hint
      - first token is a known project → that project; rest is hint
      - first token is not a project → default project; full args are hint
    """
    from app.utils import get_known_projects, resolve_project_name_and_path

    args = (args or "").strip()
    projects = get_known_projects()
    if not projects:
        return None, None, ""

    if not args:
        name, path = projects[0]
        return name, path, ""

    parts = args.split(None, 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    name, path = resolve_project_name_and_path(first)
    if path:
        return name, path, rest

    # Not a project name — treat entire args as message hint on default project.
    default_name, default_path = projects[0]
    return default_name, default_path, args


def _validate_git_state(project_path: str) -> Optional[str]:
    """Return an error message if the working tree is not committable."""
    root = Path(project_path)
    if not (root / ".git").exists() and not _is_git_worktree(root):
        return f"\u274c Not a git repository: {project_path}"

    # Unresolved merge conflicts
    conflicts = _run_git(project_path, ["diff", "--name-only", "--diff-filter=U"])
    if conflicts is None:
        return f"\u274c Failed to inspect git state in {project_path}"
    if conflicts.strip():
        files = ", ".join(conflicts.strip().splitlines()[:5])
        return (
            f"\u274c Unresolved merge conflicts — aborting commit.\n"
            f"Conflicted files: {files}"
        )

    # Any staged or unstaged changes (including untracked)?
    status = _run_git(project_path, ["status", "--porcelain"])
    if status is None:
        return f"\u274c Failed to inspect git status in {project_path}"
    if not status.strip():
        return (
            "\u274c No changes to commit "
            "(working tree clean — stage or edit files first)."
        )

    return None


def _is_git_worktree(root: Path) -> bool:
    """True when ``root`` is a git worktree (``.git`` is a file, not a dir)."""
    git_meta = root / ".git"
    return git_meta.is_file()


def _run_git(project_path: str, args: list) -> Optional[str]:
    """Run a git subcommand; return stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout
