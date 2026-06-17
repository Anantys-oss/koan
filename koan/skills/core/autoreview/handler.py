"""Kōan autoreview/noautoreview skill — toggle per-project autoreview mode."""

from pathlib import Path


def handle(ctx):
    """Toggle autoreview mode for a project, or show status."""
    koan_root = str(ctx.koan_root)
    args = ctx.args.strip() if ctx.args else ""
    is_disable = ctx.command_name == "noautoreview"

    config = _load_config(koan_root)
    if config is None:
        return "❌ No projects.yaml found. Configure projects first."

    projects = config.get("projects") or {}

    # No args → show status (include workspace projects in display)
    if not args:
        return _show_status(config, koan_root)

    # /autoreview all or /autoreview none
    lower_args = args.lower()
    if lower_args == "all":
        return _set_all(koan_root, config, projects, True)
    if lower_args == "none":
        return _set_all(koan_root, config, projects, False)

    # /autoreview <project> or /noautoreview <project>
    enable = not is_disable
    return _set_autoreview(koan_root, config, projects, args, enable)


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
    lower = name.lower()
    for key in projects:
        if key.lower() == lower:
            return key

    from app.utils import resolve_project_alias
    canonical = resolve_project_alias(name)
    if canonical:
        for key in projects:
            if key.lower() == canonical.lower():
                return key

    return None


def _get_autoreview_status(config, project_name):
    """Get effective autoreview status for a project (with defaults merge)."""
    from app.projects_config import get_project_autoreview

    return get_project_autoreview(config, project_name)


def _show_status(config, koan_root):
    """Show autoreview status for all projects (yaml + workspace)."""
    from app.projects_merged import get_all_projects

    all_projects = get_all_projects(koan_root)
    yaml_projects = config.get("projects") or {}

    # Build combined name set: merged projects + yaml-only entries
    merged_names = {name for name, _ in all_projects}
    yaml_only_names = set(yaml_projects.keys())
    all_names = merged_names | yaml_only_names

    if not all_names:
        return "❌ No projects found (projects.yaml or workspace/)."

    lines = ["🔍 Autoreview status:"]
    for name in sorted(all_names, key=str.lower):
        enabled = _get_autoreview_status(config, name)
        icon = "🟢" if enabled else "⭕️"
        state = "ON" if enabled else "OFF"
        suffix = " (workspace)" if name not in yaml_only_names else ""
        lines.append(f"  {icon} {name}: {state}{suffix}")

    lines.append("")
    lines.append("/autoreview <project> to enable")
    lines.append("/noautoreview <project> to disable")
    return "\n".join(lines)


def _set_autoreview(koan_root, config, projects, name, enable):
    """Enable or disable autoreview for a single project."""
    canonical = _resolve_project_name(projects, name)
    if canonical is None:
        # Check workspace projects and auto-create yaml entry if found
        canonical = _try_workspace_project(koan_root, config, projects, name)

    if canonical is None:
        from app.projects_merged import get_all_projects

        all_names = [n for n, _ in get_all_projects(koan_root)]
        known = ", ".join(sorted(all_names, key=str.lower))
        return f"❌ Unknown project: '{name}'. Known projects: {known}"

    current = _get_autoreview_status(config, canonical)
    if current == enable:
        state = "enabled" if enable else "disabled"
        return f"🔍 Autoreview already {state} for {canonical}."

    # Write override at project level
    project_entry = projects.get(canonical)
    if project_entry is None:
        projects[canonical] = {}
        project_entry = projects[canonical]
    project_entry["autoreview"] = enable

    _save_config(koan_root, config)

    if enable:
        return f"🔍 Autoreview enabled for {canonical}. /review + /rebase will be queued after each PR."
    return f"🔍 Autoreview disabled for {canonical}."


def _set_all(koan_root, config, projects, enable):
    """Enable or disable autoreview for all projects (yaml + workspace)."""
    from app.projects_merged import get_all_projects

    all_projects = get_all_projects(koan_root)

    # Build combined name set: merged projects + yaml-only entries
    all_names = {name for name, _ in all_projects}
    all_names.update(projects.keys())

    if not all_names:
        return "❌ No projects found (projects.yaml or workspace/)."

    # Build path lookup from merged projects
    path_by_name = dict(all_projects)

    changed = 0
    for name in sorted(all_names, key=str.lower):
        current = _get_autoreview_status(config, name)
        if current != enable:
            project_entry = projects.get(name)
            if project_entry is None:
                path = path_by_name.get(name, "")
                projects[name] = {"path": path} if path else {}
                project_entry = projects[name]
            project_entry["autoreview"] = enable
            changed += 1

    if changed == 0:
        state = "enabled" if enable else "disabled"
        return f"🔍 Autoreview already {state} for all projects."

    _save_config(koan_root, config)

    state = "enabled" if enable else "disabled"
    return f"🔍 Autoreview {state} for {changed} project(s)."


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
