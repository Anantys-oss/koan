"""Kōan explore/noexplore skill — toggle per-project exploration mode."""

from pathlib import Path


def handle(ctx):
    """Toggle exploration mode for a project, or show status."""
    koan_root = str(ctx.koan_root)
    args = ctx.args.strip() if ctx.args else ""
    is_disable = ctx.command_name == "noexplore"

    config = _load_config(koan_root)
    if config is None:
        return "❌ No projects.yaml found. Configure projects first."

    projects = config.get("projects") or {}

    # No args → show status (include workspace projects in display)
    if not args:
        return _show_status(config, koan_root)

    # /explore all, /noexplore all, /explore none
    lower_args = args.lower()
    if lower_args == "all":
        enable = not is_disable
        return _set_all(koan_root, config, projects, enable)
    if lower_args == "none":
        return _set_all(koan_root, config, projects, False)

    # /explore <project> or /noexplore <project>
    enable = not is_disable
    return _set_exploration(koan_root, config, projects, args, enable)


def _load_config(koan_root):
    """Load projects.yaml, returning None on failure."""
    from app.projects_config import load_projects_config

    try:
        return load_projects_config(koan_root)
    except (ValueError, OSError):
        return None


def _resolve_project_name(projects, name):
    """Case-insensitive project name lookup with alias support.

    Returns the canonical name from projects dict, or None.
    """
    from app.utils import resolve_project_from_dict
    return resolve_project_from_dict(projects, name)


def _get_exploration_status(config, project_name):
    """Get effective exploration status for a project (with defaults merge)."""
    from app.projects_config import get_project_exploration

    return get_project_exploration(config, project_name)


def _show_status(config, koan_root):
    """Show exploration status for all projects (yaml + workspace)."""
    from app.projects_merged import get_all_projects

    all_projects = get_all_projects(koan_root)
    yaml_projects = config.get("projects") or {}

    # Build combined name set: merged projects + yaml-only entries
    merged_names = {name for name, _ in all_projects}
    yaml_only_names = set(yaml_projects.keys())
    all_names = merged_names | yaml_only_names

    if not all_names:
        return "❌ No projects found (projects.yaml or workspace/)."

    lines = ["🔭 Exploration status:"]
    for name in sorted(all_names, key=str.lower):
        enabled = _get_exploration_status(config, name)
        icon = "🟢" if enabled else "⭕️"
        state = "ON" if enabled else "OFF"
        suffix = " (workspace)" if name not in yaml_only_names else ""
        lines.append(f"  {icon} {name}: {state}{suffix}")

    lines.append("")
    lines.append("/explore <project> to enable")
    lines.append("/noexplore <project> to disable")
    return "\n".join(lines)


def _set_exploration(koan_root, config, projects, name, enable):
    """Enable or disable exploration for a single project."""
    canonical = _resolve_project_name(projects, name)
    if canonical is None:
        # Check workspace projects and auto-create yaml entry if found
        canonical = _try_workspace_project(koan_root, config, projects, name)

    if canonical is None:
        from app.projects_merged import get_all_projects

        all_names = [n for n, _ in get_all_projects(koan_root)]
        known = ", ".join(sorted(all_names, key=str.lower))
        return f"❌ Unknown project: '{name}'. Known projects: {known}"

    current = _get_exploration_status(config, canonical)
    if current == enable:
        state = "enabled" if enable else "disabled"
        return f"🔭 Exploration already {state} for {canonical}."

    # Write override at project level
    project_entry = projects.get(canonical)
    if project_entry is None:
        projects[canonical] = {}
        project_entry = projects[canonical]
    project_entry["exploration"] = enable

    _save_config(koan_root, config)

    if enable:
        return f"🔭 Exploration enabled for {canonical}. Autonomous work will include this project."
    return f"🔭 Exploration disabled for {canonical}. Only explicit missions will run."


def _set_all(koan_root, config, projects, enable):
    """Enable or disable exploration for all projects (yaml + workspace).

    Also sets defaults.exploration so future projects inherit the choice.
    """
    from app.projects_merged import get_all_projects

    all_projects = get_all_projects(koan_root)

    # Build combined name set: merged projects + yaml-only entries (e.g. None entries)
    all_names = {name for name, _ in all_projects}
    all_names.update(projects.keys())

    if not all_names:
        return "❌ No projects found (projects.yaml or workspace/)."

    # Build path lookup from merged projects
    path_by_name = dict(all_projects)

    changed = 0
    for name in sorted(all_names, key=str.lower):
        current = _get_exploration_status(config, name)
        if current != enable:
            project_entry = projects.get(name)
            if project_entry is None:
                path = path_by_name.get(name, "")
                projects[name] = {"path": path} if path else {}
                project_entry = projects[name]
            project_entry["exploration"] = enable
            changed += 1

    # Set defaults.exploration so future projects inherit the choice
    defaults = config.setdefault("defaults", {})
    default_changed = defaults.get("exploration") != enable
    defaults["exploration"] = enable

    if changed == 0 and not default_changed:
        state = "enabled" if enable else "disabled"
        return f"🔭 Exploration already {state} for all projects."

    _save_config(koan_root, config)

    state = "enabled" if enable else "disabled"
    parts = []
    if changed:
        parts.append(f"{changed} project(s)")
    if default_changed:
        parts.append("future-project default")
    return f"🔭 Exploration {state} for {' + '.join(parts)}."


def _try_workspace_project(koan_root, config, projects, name):
    """Check if name matches a workspace project not yet in projects.yaml.

    If found, creates a minimal entry in the config's projects dict
    so the caller can proceed normally.

    Returns the canonical project name, or None if not found.
    """
    from app.workspace_discovery import discover_workspace_projects

    workspace_projects = discover_workspace_projects(koan_root)
    lower = name.lower()
    for ws_name, ws_path in workspace_projects:
        if ws_name.lower() == lower:
            # Auto-create entry in config
            if "projects" not in config or config["projects"] is None:
                config["projects"] = {}
            config["projects"][ws_name] = {"path": ws_path}
            # Also update the local projects reference
            projects[ws_name] = config["projects"][ws_name]
            return ws_name
    return None


def _save_config(koan_root, config):
    """Persist config to projects.yaml."""
    from app.projects_config import save_projects_config

    save_projects_config(koan_root, config)
