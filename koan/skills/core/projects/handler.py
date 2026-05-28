"""Kōan projects skill — list configured projects."""

import os


def _shorten_path(path):
    """Replace the user's HOME directory prefix with ~ for shorter display."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _win_rate_annotation(bandit_state, project_name: str) -> str:
    """Return a win-rate annotation string, or empty string if no data yet."""
    alpha, beta = bandit_state.get(project_name)
    n = int(alpha + beta - 2)  # subtract uniform prior to show real observations
    if n == 0:
        return ""
    rate = alpha / (alpha + beta)
    return f" [win rate: {rate:.0%} (n={n})]"


def _tracker_annotation(project_name: str) -> str:
    """Return a compact issue tracker annotation for /projects output."""
    try:
        from app.issue_tracker.config import get_tracker_for_project

        tracker = get_tracker_for_project(project_name)
    except Exception:
        return ""

    provider = tracker.get("provider", "github")
    if provider == "jira":
        key = tracker.get("jira_project") or "?"
        branch = tracker.get("default_branch", "")
        suffix = f", branch:{branch}" if branch else ""
        return f" [tracker: jira:{key}{suffix}]"

    repo = tracker.get("repo")
    return f" [tracker: github:{repo}]" if repo else ""


def handle(ctx):
    """Handle /projects command."""
    from app.utils import get_known_projects, KOAN_ROOT

    # Refresh workspace + yaml cache before displaying
    try:
        from app.projects_merged import refresh_projects, get_warnings
        refresh_projects(str(KOAN_ROOT))
        warnings = get_warnings()
    except Exception:
        warnings = []

    projects = get_known_projects()

    if not projects:
        return "No projects configured."

    # Load bandit state for win-rate annotations (best-effort; never crashes)
    bandit_state = None
    try:
        from app.bandit import load_bandit_state
        instance_dir = str(KOAN_ROOT / "instance") if hasattr(KOAN_ROOT, "__truediv__") else None
        if instance_dir is None:
            import os as _os
            instance_dir = _os.path.join(str(KOAN_ROOT), "instance")
        bandit_state = load_bandit_state(instance_dir)
    except Exception:
        pass

    lines = ["Configured projects:"]
    for name, path in projects:
        annotation = _win_rate_annotation(bandit_state, name) if bandit_state else ""
        tracker = _tracker_annotation(name)
        lines.append(f"  - {name}: {_shorten_path(path)}{annotation}{tracker}")

    if warnings:
        lines.append("")
        lines.extend(warnings)

    return "\n".join(lines)
