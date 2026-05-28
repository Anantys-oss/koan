"""Project issue tracker configuration resolution."""

import os
import re
from pathlib import Path
from typing import Dict, Optional

from app.projects_config import (
    get_project_config,
    get_project_submit_to_repository,
    invalidate_projects_config_cache,
    load_projects_config,
    save_projects_config,
)

DEFAULT_ISSUE_TYPE = "Task"
VALID_PROVIDERS = {"github", "jira"}

_GITHUB_REPO_RE = re.compile(
    r"(?:github\.com[:/])?([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


def normalize_github_repo(value: str) -> str:
    """Normalize owner/repo or a GitHub URL to owner/repo."""
    value = (value or "").strip()
    match = _GITHUB_REPO_RE.search(value)
    if not match:
        return value
    return f"{match.group(1)}/{match.group(2)}"


def _load_projects(koan_root: str = "") -> Optional[dict]:
    root = koan_root or os.environ.get("KOAN_ROOT", "")
    if not root:
        return None
    try:
        return load_projects_config(root)
    except (OSError, ValueError):
        return None


def get_project_issue_tracker(
    projects_config: Optional[dict],
    project_name: str,
) -> Dict[str, str]:
    """Return normalized issue_tracker config for a project.

    The per-project ``issue_tracker`` section selects the backend. Projects
    without that section default to GitHub so existing installs keep working.
    """
    project_cfg = get_project_config(projects_config or {}, project_name)
    raw = project_cfg.get("issue_tracker", {}) or {}
    if not isinstance(raw, dict):
        raw = {}

    provider = str(raw.get("provider", "")).strip().lower()
    if provider not in VALID_PROVIDERS:
        provider = "github"

    github_repo = normalize_github_repo(
        raw.get("repo")
        or project_cfg.get("github_url", "")
        or project_cfg.get("github_repo", "")
    )

    return {
        "provider": provider,
        "repo": github_repo,
        "jira_project": str(raw.get("jira_project", "")).strip().upper(),
        "jira_issue_type": str(
            raw.get("jira_issue_type", DEFAULT_ISSUE_TYPE)
        ).strip() or DEFAULT_ISSUE_TYPE,
        "default_branch": str(raw.get("default_branch", "")).strip(),
    }


def get_tracker_for_project(
    project_name: str,
    koan_root: str = "",
    legacy_config: Optional[dict] = None,
) -> Dict[str, str]:
    """Resolve tracker config from projects.yaml.

    ``legacy_config`` is accepted for older call sites but intentionally
    ignored. Jira project ownership now lives only in projects.yaml; missing
    issue_tracker sections default to GitHub.
    """
    _ = legacy_config
    projects_cfg = _load_projects(koan_root)
    return get_project_issue_tracker(projects_cfg, project_name)


def get_jira_project_map_for_polling(
    legacy_config: Optional[dict] = None,
    koan_root: str = "",
) -> Dict[str, str]:
    """Build Jira project-key -> Koan project map for polling.

    Only projects with ``issue_tracker.provider: jira`` in projects.yaml are
    registered to this instance. ``legacy_config`` is accepted for older call
    sites but intentionally ignored.
    """
    _ = legacy_config
    result: Dict[str, str] = {}
    projects_cfg = _load_projects(koan_root)
    for project_name, tracker in _iter_project_trackers(projects_cfg):
        if tracker["provider"] == "jira" and tracker["jira_project"]:
            result[tracker["jira_project"]] = project_name
    return result


def get_jira_branch_map_for_polling(
    legacy_config: Optional[dict] = None,
    koan_root: str = "",
) -> Dict[str, str]:
    """Build Jira project-key -> default branch map for polling."""
    _ = legacy_config
    result: Dict[str, str] = {}
    projects_cfg = _load_projects(koan_root)
    for _project_name, tracker in _iter_project_trackers(projects_cfg):
        if (
            tracker["provider"] == "jira"
            and tracker["jira_project"]
            and tracker["default_branch"]
        ):
            result[tracker["jira_project"]] = tracker["default_branch"]
    return result


def _iter_project_trackers(projects_cfg: Optional[dict]):
    if not projects_cfg:
        return
    for project_name in (projects_cfg.get("projects") or {}):
        tracker = get_project_issue_tracker(projects_cfg, project_name)
        yield project_name, tracker


def find_project_for_jira_key(
    issue_key: str,
    koan_root: str = "",
    legacy_config: Optional[dict] = None,
) -> str:
    """Resolve a Jira issue key to a Koan project name."""
    _ = legacy_config
    if not issue_key or "-" not in issue_key:
        return ""
    project_key = issue_key.split("-", 1)[0].upper()
    project_map = get_jira_project_map_for_polling(
        {}, koan_root=koan_root,
    )
    return project_map.get(project_key, "")


def detect_legacy_jira_projects(config: Optional[dict]) -> list[str]:
    """Return Jira project keys still configured in deprecated config.yaml map."""
    jira = (config or {}).get("jira") or {}
    projects = jira.get("projects") or {}
    if not isinstance(projects, dict):
        return []
    return sorted(str(key).strip().upper() for key in projects if str(key).strip())


def format_legacy_jira_projects_warning(keys: list[str]) -> str:
    """Format a concise warning for ignored config.yaml jira.projects entries."""
    if not keys:
        return ""
    key_list = ", ".join(keys)
    return (
        "jira.projects in instance/config.yaml is ignored. "
        f"Move Jira project mapping ({key_list}) to projects.yaml under "
        "each project's issue_tracker section."
    )


def resolve_code_repository(project_name: str, project_path: str = "") -> str:
    """Return the GitHub owner/repo used for PRs for a Koan project."""
    koan_root = os.environ.get("KOAN_ROOT", "")
    projects_cfg = _load_projects(koan_root)
    if projects_cfg:
        submit_cfg = get_project_submit_to_repository(projects_cfg, project_name)
        if submit_cfg.get("repo"):
            return normalize_github_repo(submit_cfg["repo"])
        tracker = get_project_issue_tracker(projects_cfg, project_name)
        if tracker.get("repo"):
            return normalize_github_repo(tracker["repo"])
        project_cfg = get_project_config(projects_cfg, project_name)
        if project_cfg.get("github_url"):
            return normalize_github_repo(project_cfg["github_url"])

    if project_path:
        try:
            from app.github import origin_repo, resolve_target_repo

            target = resolve_target_repo(project_path, project_name=project_name)
            if target:
                return normalize_github_repo(target)
            origin = origin_repo(project_path)
            if origin:
                return normalize_github_repo(origin)
        except (ImportError, OSError, RuntimeError):
            pass

    return ""


def set_project_tracker(
    koan_root: str,
    project_name: str,
    tracker: Dict[str, str],
) -> None:
    """Persist issue_tracker settings for a project in projects.yaml."""
    config = _load_projects(koan_root) or {"projects": {}}
    projects = config.setdefault("projects", {})
    project = projects.setdefault(project_name, {})
    if project is None:
        project = {}
        projects[project_name] = project
    if not isinstance(project, dict):
        raise ValueError(f"Project '{project_name}' must be a mapping")

    provider = str(tracker.get("provider", "")).lower()
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unsupported issue tracker provider: {provider}")

    section = {"provider": provider}
    if provider == "github":
        repo = normalize_github_repo(tracker.get("repo", ""))
        if repo:
            section["repo"] = repo
    else:
        jira_project = str(tracker.get("jira_project", "")).strip().upper()
        if not jira_project:
            raise ValueError("jira_project is required for Jira tracker config")
        section["jira_project"] = jira_project
        issue_type = str(
            tracker.get("jira_issue_type", DEFAULT_ISSUE_TYPE)
        ).strip() or DEFAULT_ISSUE_TYPE
        section["jira_issue_type"] = issue_type

    branch = str(tracker.get("default_branch", "")).strip()
    if branch:
        section["default_branch"] = branch

    project["issue_tracker"] = section
    Path(koan_root).mkdir(parents=True, exist_ok=True)
    save_projects_config(koan_root, config)
    # Drop the in-process projects.yaml cache so the next reader sees the
    # write immediately. Without this, callers within the same mtime second
    # would observe the pre-write config and silently route to the old
    # tracker.
    invalidate_projects_config_cache()
