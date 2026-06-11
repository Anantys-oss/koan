"""Project configuration loader — reads projects.yaml.

Provides:
- load_projects_config(koan_root) -> dict: Load and validate projects.yaml
- get_projects_from_config(config) -> list[tuple[str, str]]: Extract (name, path) tuples
- get_project_config(config, name) -> dict: Get merged defaults + project overrides
- get_project_auto_merge(config, name) -> dict: Get auto-merge config for a project
- get_project_cli_provider(config, name) -> str: Get CLI provider for a project
- get_project_models(config, name) -> dict: Get model overrides for a project
- get_project_tools(config, name) -> dict: Get tool restrictions for a project
- get_project_exploration(config, name) -> bool: Get exploration flag for a project
- get_project_autoreview(config, name) -> bool: Get autoreview flag for a project
- get_project_max_open_prs(config, name) -> int: Get max open PRs limit for a project
- get_project_max_pending_branches(config, name) -> int: Get max pending branches limit
- get_project_github_authorized_users(config, name) -> list: Get GitHub authorized users
- get_project_issue_tracker(config, name) -> dict: Get issue tracker routing config

File location: projects.yaml at KOAN_ROOT (next to .env).
"""

import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

# Thread-safe mtime-keyed cache for load_projects_config().
# Avoids repeated YAML file I/O when multiple config getters call
# load_projects_config() within the same pipeline pass.
_cache_lock = threading.Lock()
_cache: dict = {}  # (koan_root, yaml_path) -> (mtime, result)


def load_projects_config(koan_root: str) -> Optional[dict]:
    """Load projects.yaml from KOAN_ROOT.

    Returns the parsed config dict, or None if file doesn't exist.
    Raises ValueError on invalid YAML or schema violations.

    Results are cached by file mtime — repeated calls with an unchanged
    file return the cached dict without re-reading the YAML.
    """
    config_path = Path(koan_root) / "projects.yaml"
    if not config_path.exists():
        return None

    try:
        current_mtime = config_path.stat().st_mtime
    except OSError:
        return None

    cache_key = (koan_root, str(config_path))
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None and cached[0] == current_mtime:
            return cached[1]

    try:
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in projects.yaml: {e}") from e

    if data is None:
        return None

    if not isinstance(data, dict):
        raise ValueError("projects.yaml must be a YAML mapping (dict)")

    _validate_config(data)

    with _cache_lock:
        _cache[cache_key] = (current_mtime, data)

    return data


def invalidate_projects_config_cache() -> None:
    """Clear the load_projects_config() mtime cache.

    Call from test teardown to prevent cross-test contamination.
    """
    with _cache_lock:
        _cache.clear()


_PROJECT_KEY_TYPES = {
    "path": (str,),
    "github_url": (str,),
    "github_urls": (list,),
    "git_auto_merge": (dict,),
    "models": (dict,),
    "tools": (dict,),
    "github": (dict,),
    "security": (dict,),
    "security_review": (dict,),
    "cli_provider": (str,),
    "submit_to_repository": (dict,),
    "stagnation": (dict, bool),
    "complexity_routing": (dict, bool),
    "exploration": (bool, str),
    "autoreview": (bool, str),
    "focus": (bool, str),
    "max_open_prs": (int, str),
    "max_pending_branches": (int, str),
    "mcp": (list,),
    "rtk": (bool, str),
    "devcontainer": (bool,),
}

_DEFAULTS_KEY_TYPES = {
    k: v for k, v in _PROJECT_KEY_TYPES.items() if k != "path"
}


def _validate_config(config: dict) -> None:
    """Validate the structure of the projects config.

    Raises ValueError on validation failures.
    """
    # defaults section is optional, must be dict if present
    defaults = config.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise ValueError("'defaults' must be a mapping")

    if isinstance(defaults, dict):
        _validate_section_keys(defaults, _DEFAULTS_KEY_TYPES, "defaults")

    # projects section is optional — missing means 0 projects (workspace provides them)
    projects = config.get("projects")
    if projects is None:
        return

    if not isinstance(projects, dict):
        raise ValueError("'projects' must be a mapping of project_name -> config")

    if len(projects) > 50:
        raise ValueError(f"Max 50 projects allowed. You have {len(projects)}.")

    # Check for case-insensitive duplicates
    seen_lower = {}
    for name in projects.keys():
        lower = name.lower()
        if lower in seen_lower:
            raise ValueError(
                f"Duplicate project name (case-insensitive): "
                f"'{seen_lower[lower]}' and '{name}'"
            )
        seen_lower[lower] = name

    for name, project in projects.items():
        if not isinstance(name, str):
            raise ValueError(f"Project name must be a string, got: {type(name).__name__}")

        if project is None:
            # Allow empty project entries (workspace override with no settings)
            continue

        if not isinstance(project, dict):
            raise ValueError(f"Project '{name}' must be a mapping, got: {type(project).__name__}")

        # path is optional — workspace projects don't need it in yaml
        path = project.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise ValueError(f"Project '{name}' has invalid path: {path!r}")

        _validate_section_keys(project, _PROJECT_KEY_TYPES, f"projects.{name}")


def _validate_section_keys(section: dict, schema: dict, context: str) -> None:
    """Validate types of known keys in a config section.

    Skips unknown keys (those are passed through as overrides).
    Raises ValueError when a known key has the wrong type.
    """
    for key, value in section.items():
        if value is None:
            continue
        expected_types = schema.get(key)
        if expected_types is None:
            continue
        # bool is subclass of int — check bool before int
        if isinstance(value, bool) and bool not in expected_types:
            type_names = "/".join(t.__name__ for t in expected_types)
            raise ValueError(
                f"'{context}.{key}' must be {type_names}, got bool"
            )
        if not isinstance(value, expected_types):
            type_names = "/".join(t.__name__ for t in expected_types)
            raise ValueError(
                f"'{context}.{key}' must be {type_names}, "
                f"got {type(value).__name__}"
            )


def validate_project_paths(config: dict) -> Optional[str]:
    """Check that all project paths exist on disk.

    Returns an error message if any path is missing, or None if all valid.
    Projects without a path (workspace-only overrides) are skipped.
    Separated from _validate_config() so tests can skip filesystem checks.
    """
    projects = config.get("projects", {})
    for name, project in projects.items():
        if project is None:
            continue
        path = project.get("path", "")
        if not path:
            continue  # Workspace project — no path to validate
        if not Path(path).is_dir():
            return f"Project '{name}' path does not exist: {path}"
    return None


def get_projects_from_config(config: dict) -> List[Tuple[str, str]]:
    """Extract sorted (name, path) tuples from config.

    Same format as get_known_projects() returns — enables drop-in replacement.
    Projects without a path (workspace-only overrides) are skipped.
    """
    projects = config.get("projects", {})
    result = []
    for name, proj in projects.items():
        if proj is None:
            continue
        path = proj.get("path", "").strip()
        if path:
            result.append((name, path))
    return sorted(result, key=lambda x: x[0].lower())


def _find_project_entry(projects: dict, project_name: str) -> dict:
    """Case-insensitive lookup of a project entry in the projects dict."""
    # Fast path: exact match
    entry = projects.get(project_name)
    if entry is not None:
        return entry
    # Slow path: case-insensitive scan
    lower = project_name.lower()
    for key, value in projects.items():
        if key.lower() == lower:
            return value
    return {}


def get_project_config(config: dict, project_name: str) -> dict:
    """Get merged config for a project (defaults + project overrides).

    Deep-merges per-section: project-level keys override default-level keys.
    Unknown sections are passed through as-is.
    Project name lookup is case-insensitive.
    """
    defaults = config.get("defaults", {}) or {}
    project = _find_project_entry(config.get("projects", {}), project_name) or {}

    merged = {}
    # Start with all default keys
    for key, value in defaults.items():
        if isinstance(value, dict):
            # Deep merge dicts (one level)
            project_value = project.get(key, {}) or {}
            merged[key] = {**value, **project_value}
        else:
            merged[key] = project.get(key, value)

    # Add project-only keys not in defaults
    for key, value in project.items():
        if key == "path":
            continue  # path is structural, not a setting
        if key not in merged:
            merged[key] = value

    return merged


def get_project_auto_merge(config: dict, project_name: str) -> dict:
    """Get auto-merge config for a project from projects.yaml.

    Returns a dict with keys: enabled, base_branch, strategy, rules.
    Falls back to defaults section, then sensible defaults.
    """
    project_cfg = get_project_config(config, project_name)
    am = project_cfg.get("git_auto_merge", {}) or {}

    return {
        "enabled": am.get("enabled", False),
        "base_branch": am.get("base_branch", "main"),
        "strategy": am.get("strategy", "squash"),
        "rules": am.get("rules", []),
    }


def resolve_base_branch(
    project_name: str, project_path: Optional[str] = None
) -> str:
    """Resolve the base branch for a project.

    Resolution order:
    1. Explicit per-project base_branch in projects.yaml
    2. Auto-detection from the remote's default branch (if project_path given)
    3. Defaults section base_branch from projects.yaml
    4. Hardcoded fallback: 'main'

    Safe to call when KOAN_ROOT is unset or config is missing — returns 'main'.
    """
    import os

    config_branch = "main"
    project_explicit = False

    try:
        koan_root = os.environ.get("KOAN_ROOT", "")
        if koan_root:
            config = load_projects_config(koan_root)
            if config:
                am = get_project_auto_merge(config, project_name)
                config_branch = am.get("base_branch", "main")

                # Check if the project explicitly sets base_branch
                projects = config.get("projects", {}) or {}
                proj_cfg = _find_project_entry(projects, project_name) or {}
                proj_am = proj_cfg.get("git_auto_merge", {}) or {}
                if proj_am.get("base_branch"):
                    project_explicit = True
    except (ValueError, OSError, KeyError):
        pass

    # If project explicitly sets the branch, trust it
    if project_explicit:
        return config_branch

    # Try auto-detection from the remote
    if project_path:
        try:
            from app.git_prep import detect_remote_default_branch, get_upstream_remote

            koan_root = os.environ.get("KOAN_ROOT", "")
            remote = "origin"
            if koan_root:
                remote = get_upstream_remote(project_path, project_name, koan_root)
            detected = detect_remote_default_branch(remote, project_path)
            if detected:
                return detected
        except Exception as e:
            print(f"[projects_config] default branch detection failed for {project_name} (non-fatal): {e}", file=sys.stderr)

    return config_branch


def get_project_cli_provider(config: dict, project_name: str) -> str:
    """Get CLI provider for a project from projects.yaml.

    Returns the provider name ("claude", "copilot", "local") or empty string
    if not configured (meaning: use the global provider).

    Note: Data accessor only — the provider resolution in cli_provider.py
    does not yet call this. Per-project provider switching requires changes
    to get_provider() to accept a project_name parameter.
    """
    project_cfg = get_project_config(config, project_name)
    return str(project_cfg.get("cli_provider", "")).strip().lower()


def get_project_models(config: dict, project_name: str) -> dict:
    """Get model overrides for a project from projects.yaml.

    Returns a dict with model role keys (mission, chat, lightweight, etc.).
    Only includes keys that are explicitly set — caller should merge with
    global defaults.
    """
    project_cfg = get_project_config(config, project_name)
    models = project_cfg.get("models", {})
    if not isinstance(models, dict):
        return {}
    return models


def get_project_tools(config: dict, project_name: str) -> dict:
    """Get tool restrictions for a project from projects.yaml.

    Returns a dict with keys: mission, chat (lists of tool names).
    Only includes keys that are explicitly set — caller should merge with
    global defaults.
    """
    project_cfg = get_project_config(config, project_name)
    tools = project_cfg.get("tools", {})
    if not isinstance(tools, dict):
        return {}
    return tools


def get_project_rtk_enabled(config: dict, project_name: str) -> bool:
    """Return whether the rtk awareness section should fire for a project.

    Reads ``rtk`` from the per-project config (with defaults merged in).
    Accepts the same shapes as the global ``optimizations.rtk.enabled``
    knob — bool, ``"auto"``, ``"true"``, ``"false"``, etc.

    Resolution:
      1. If the project sets ``rtk: false`` (or any false-y value) →
         hard opt-out, returns ``False`` regardless of global state.
      2. If the project sets ``rtk: true`` → opts in even when the global
         knob would say no.
      3. If the project sets ``rtk: auto`` (or omits it entirely, or sets
         it to anything else) → defer to the global resolution in
         :func:`app.config.is_rtk_mode`.

    The intent: the global config tracks "do I want rtk on this Kōan
    instance"; the per-project field tracks "does this project's tooling
    play nicely with rtk's filters".  A project can opt out (e.g. its test
    runner emits unusual JSON that rtk's filter would clobber) without
    affecting the rest of the instance.
    """
    project_cfg = get_project_config(config, project_name)
    from app.config import coerce_rtk_enabled, is_rtk_mode
    if "rtk" in project_cfg:
        explicit = coerce_rtk_enabled(project_cfg["rtk"])
        if explicit is not None:
            return explicit
        # "auto" or unrecognised → fall through to global.
    return is_rtk_mode()


def get_project_mcp(config: dict, project_name: str) -> list:
    """Get MCP config file paths for a project from projects.yaml.

    Returns a list of file path strings. Only includes entries explicitly
    set — caller should fall back to global config.yaml mcp list.

    Used to resolve per-project MCP server configs when projects.yaml
    contains a ``mcp:`` key (list of JSON file paths) under a project
    entry, complementing the global ``mcp:`` list in config.yaml.
    """
    project_cfg = get_project_config(config, project_name)
    mcp = project_cfg.get("mcp", [])
    if not isinstance(mcp, list):
        return []
    return mcp


def get_project_devcontainer_enabled(config: dict, project_name: str) -> bool:
    """Return whether devcontainer execution mode is enabled for a project."""
    return bool(get_project_config(config, project_name).get("devcontainer", False))


def get_project_focus(config: dict, project_name: str) -> bool:
    """Get focus flag for a project from projects.yaml.

    When True, the agent only works on explicitly queued missions for this
    project — no contemplative sessions, no DEEP mode, no autonomous
    exploration. Equivalent to ``exploration: false`` but unified under the
    focus concept.

    Supports defaults-level and per-project overrides. Common patterns:
      - ``defaults: { focus: true }`` + ``myapp: { focus: false }``
        → all projects focused except myapp
      - ``defaults: { focus: false }`` + ``vendor: { focus: true }``
        → only vendor is focused

    Returns False by default (focus not enforced).
    """
    project_cfg = get_project_config(config, project_name)
    value = project_cfg.get("focus", False)

    # Handle string values like "true", "yes", "1"
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")

    return bool(value)


def get_project_exploration(config: dict, project_name: str) -> bool:
    """Get exploration flag for a project from projects.yaml.

    Controls whether autonomous exploration (contemplative sessions and
    free-form autonomous work) is enabled for a project. When False, the
    agent only works on the project when explicit missions are queued.

    Returns True by default (exploration enabled).
    """
    project_cfg = get_project_config(config, project_name)
    value = project_cfg.get("exploration", True)

    # Handle string values like "false", "no", "0"
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "0", "")

    return bool(value)


def get_project_autoreview(config: dict, project_name: str) -> bool:
    """Get autoreview flag for a project from projects.yaml.

    When True, automatically queues /review then /rebase after any mission
    that creates a PR (and was not auto-merged). Off by default.

    Returns False by default (autoreview disabled).
    """
    project_cfg = get_project_config(config, project_name)
    value = project_cfg.get("autoreview", False)

    # Handle string values like "false", "no", "0"
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "0", "")

    return bool(value)


def get_project_max_open_prs(config: dict, project_name: str) -> int:
    """Get max open PRs limit for a project from projects.yaml.

    Controls the maximum number of open PRs allowed before autonomous
    exploration is paused for this project. When the limit is reached,
    the agent only works on explicit missions for the project.

    Returns 0 by default (unlimited).
    """
    project_cfg = get_project_config(config, project_name)
    value = project_cfg.get("max_open_prs", 0)

    # Coerce to int; invalid values map to 0 (unlimited)
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0

    # Negative or zero → unlimited
    return result if result > 0 else 0


def get_project_max_pending_branches(config: dict, project_name: str) -> int:
    """Get max pending branches limit for a project from projects.yaml.

    Controls the maximum number of pending branches (open PRs ∪ local
    unmerged branches) allowed before mission pickup and exploration are
    blocked for this project.

    Returns 10 by default. Returns 0 for unlimited (no limit).
    """
    project_cfg = get_project_config(config, project_name)
    value = project_cfg.get("max_pending_branches", 10)

    # Coerce to int; invalid values map to 0 (unlimited)
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0

    # Negative or zero → unlimited
    return result if result > 0 else 0


def get_project_github_authorized_users(config: dict, project_name: str) -> list:
    """Get GitHub authorized users for a project from projects.yaml.

    Per-project github.authorized_users completely replaces global list.
    Returns the list of authorized GitHub usernames, or ["*"] for wildcard.
    Returns empty list if not configured.
    """
    project_cfg = get_project_config(config, project_name)
    github = project_cfg.get("github", {}) or {}
    users = github.get("authorized_users", [])
    return users if isinstance(users, list) else []


def get_project_github_reply_authorized_users(config: dict, project_name: str) -> Optional[list]:
    """Get GitHub reply_authorized_users for a project from projects.yaml.

    Per-project github.reply_authorized_users completely replaces global list.
    Returns the list of authorized GitHub usernames, or ["*"] for wildcard.
    Returns None if not configured (meaning: fall back to global config.yaml).
    """
    project_cfg = get_project_config(config, project_name)
    github = project_cfg.get("github", {}) or {}
    users = github.get("reply_authorized_users")
    if users is None:
        return None
    return users if isinstance(users, list) else None


def get_project_github_natural_language(config: dict, project_name: str) -> Optional[bool]:
    """Get GitHub natural_language setting for a project from projects.yaml.

    Per-project github.natural_language overrides the global setting.
    Returns True/False if explicitly set, or None if not configured
    (meaning: fall back to global config.yaml).
    """
    project_cfg = get_project_config(config, project_name)
    github = project_cfg.get("github", {}) or {}
    value = github.get("natural_language")
    if value is None:
        return None
    return bool(value)


def get_project_security_review(config: dict, project_name: str) -> dict:
    """Get differential security review config for a project from projects.yaml.

    Controls whether a security review is run on mission diffs before auto-merge.
    Returns a dict with keys: enabled, blocking, severity_threshold.

    - enabled: Whether to run the review (default: False).
    - blocking: Whether a failed review blocks auto-merge (default: False).
    - severity_threshold: Maximum acceptable risk level before flagging
      ("low", "medium", "high", "critical"). Default: "high".
    """
    project_cfg = get_project_config(config, project_name)
    sr = project_cfg.get("security_review", {}) or {}

    return {
        "enabled": bool(sr.get("enabled", False)),
        "blocking": bool(sr.get("blocking", False)),
        "severity_threshold": str(sr.get("severity_threshold", "high")).strip().lower(),
    }


def get_project_submit_to_repository(config: dict, project_name: str) -> dict:
    """Get submit_to_repository config for a project from projects.yaml.

    Controls where PRs are submitted for this project, especially for forks.
    Returns a dict with keys: repo (owner/repo), remote (git remote name).
    Returns empty dict if not configured.
    """
    project_cfg = get_project_config(config, project_name)
    value = project_cfg.get("submit_to_repository", {})
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get("repo"):
        result["repo"] = str(value["repo"])
    if value.get("remote"):
        result["remote"] = str(value["remote"])
    return result


def get_project_issue_tracker(config: dict, project_name: str) -> dict:
    """Get normalized issue tracker config for a project from projects.yaml."""
    from app.issue_tracker.config import get_project_issue_tracker as _get

    return _get(config, project_name)


def get_project_security_config(config: dict, project_name: str) -> dict:
    """Get security configuration for a project from projects.yaml.

    Returns a dict with keys:
      - ``pvrs``: ``"auto"`` (default), ``"true"``, or ``"false"``
      - ``pvrs_threshold``: ``"high"`` (default) — minimum severity routed
        to PVRS. One of ``"critical"``, ``"high"``, ``"medium"``, ``"low"``.

    Example projects.yaml::

        defaults:
          security:
            pvrs: auto
            pvrs_threshold: high
        projects:
          myapp:
            security:
              pvrs: false  # force public issues
    """
    project_cfg = get_project_config(config, project_name)
    security = project_cfg.get("security", {})
    if not isinstance(security, dict):
        security = {}

    pvrs = str(security.get("pvrs", "auto")).strip().lower()
    if pvrs not in ("auto", "true", "false"):
        pvrs = "auto"

    threshold = str(security.get("pvrs_threshold", "high")).strip().lower()
    if threshold not in ("critical", "high", "medium", "low"):
        threshold = "high"

    return {"pvrs": pvrs, "pvrs_threshold": threshold}


def save_projects_config(koan_root: str, config: dict) -> None:
    """Write config back to projects.yaml atomically, preserving comments.

    Uses ruamel.yaml to round-trip the existing file so that user comments
    and formatting are kept intact. Falls back to plain pyyaml if ruamel
    is unavailable.
    """
    from app.utils import atomic_write

    config_path = Path(koan_root) / "projects.yaml"

    try:
        from ruamel.yaml import YAML
        import io

        ry = YAML()
        ry.preserve_quotes = True

        # Load existing file to preserve its comments
        existing = None
        if config_path.exists():
            with open(config_path, "r") as f:
                raw = f.read()
            if raw.strip():
                existing = ry.load(raw)

        if existing is not None and isinstance(existing, dict):
            _deep_merge_yaml(existing, config)
            data = existing
        else:
            data = config

        stream = io.StringIO()
        ry.dump(data, stream)
        content = stream.getvalue()

        # Add header only for brand-new files / non-dict existing content
        if (existing is None or not isinstance(existing, dict)) and not content.startswith("#"):
            header = (
                "# projects.yaml — Project configuration for Kōan\n"
                "# Auto-managed — manual edits are preserved.\n\n"
            )
            content = header + content

        atomic_write(config_path, content)
        return
    except ImportError:
        pass

    # Fallback: plain pyyaml (loses comments)
    header = (
        "# projects.yaml — Project configuration for Kōan\n"
        "# Auto-managed — manual edits are preserved.\n\n"
    )
    content = header + yaml.dump(config, default_flow_style=False, sort_keys=False)
    atomic_write(config_path, content)


def _deep_merge_yaml(target, source):
    """Recursively merge source dict into target, preserving target's comments.

    - Existing keys are updated in-place (comments on those keys survive).
    - New keys from source are added.
    - Keys removed from source are deleted from target.
    """
    # Update / add keys from source
    for key, value in source.items():
        if (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge_yaml(target[key], value)
        else:
            target[key] = value

    # Remove keys not in source
    for key in list(target.keys()):
        if key not in source:
            del target[key]


def ensure_github_urls(koan_root: str) -> List[str]:
    """Populate missing github_url fields in projects.yaml from git remotes.

    Iterates all projects, calls get_github_remote() on any project without
    a github_url field, and saves the discovered URL back to projects.yaml.

    Returns a list of log messages for discovered URLs.
    Does NOT overwrite existing github_url values.
    """
    config = load_projects_config(koan_root)
    if config is None:
        return []

    projects = config.get("projects", {})
    if not projects:
        return []

    from app.utils import get_all_github_remotes, get_github_remote

    messages = []
    modified = False

    for name, project in projects.items():
        if not isinstance(project, dict):
            continue

        path = project.get("path", "")
        if not path or not Path(path).is_dir():
            continue

        # Populate primary github_url if missing
        if not project.get("github_url"):
            github_url = get_github_remote(path)
            if github_url:
                project["github_url"] = github_url
                messages.append(f"Discovered github_url for '{name}': {github_url}")
                modified = True

        # Always refresh github_urls (all remotes) for cross-owner resolution
        all_urls = get_all_github_remotes(path)
        if all_urls and set(all_urls) != set(project.get("github_urls", [])):
            project["github_urls"] = all_urls
            modified = True

    if modified:
        try:
            save_projects_config(koan_root, config)
        except OSError as e:
            messages.append(f"Warning: could not save projects.yaml: {e}")

    return messages
