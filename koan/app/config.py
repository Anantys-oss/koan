"""Configuration loading and access — extracted from utils.py for clarity.

Handles:
- Tool configuration (chat/mission tools, descriptions)
- Model configuration (per-role model selection)
- Claude CLI flag building
- Behavioral settings (max_runs, interval, fast_reply, etc.)
- Auto-merge configuration
- CLI provider shell helpers

Note: load_config() itself lives in utils.py to avoid circular imports.
Functions here call it via import to ensure mocks propagate correctly.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional


def _load_config() -> dict:
    """Import and call load_config from utils — ensures mock patches propagate."""
    from app.utils import load_config
    return load_config()


def _load_project_overrides(project_name: str) -> dict:
    """Load per-project overrides from projects.yaml.

    Returns the merged project config (defaults + project-specific) or
    empty dict if projects.yaml doesn't exist or the project isn't found.
    """
    if not project_name:
        return {}
    try:
        from app.projects_config import load_projects_config, get_project_config
        koan_root = os.environ.get("KOAN_ROOT", "")
        if not koan_root:
            return {}
        projects_config = load_projects_config(koan_root)
        if not projects_config:
            return {}
        if project_name not in projects_config.get("projects", {}):
            return {}
        return get_project_config(projects_config, project_name)
    except Exception as e:
        print(f"[config] Error loading project overrides for {project_name}: {e}", file=sys.stderr)
        return {}


def _get_tools_for_role(role: str, default: List[str], project_name: str = "") -> str:
    """Get comma-separated tool list for a role, with per-project override.

    Args:
        role: Tool role key ("chat" or "mission").
        default: Default tool list if nothing is configured.
        project_name: Optional project name for per-project overrides.

    Returns:
        Comma-separated tool names.
    """
    # Check per-project override first
    project_overrides = _load_project_overrides(project_name)
    project_tools = project_overrides.get("tools", {})
    if isinstance(project_tools, dict) and role in project_tools:
        tools = project_tools[role]
        if isinstance(tools, list):
            return ",".join(tools)

    config = _load_config()
    tools = config.get("tools", {}).get(role, default)
    if isinstance(tools, str):
        return tools
    if isinstance(tools, list):
        return ",".join(tools)
    return ",".join(default)


def get_chat_tools(project_name: str = "") -> str:
    """Get comma-separated list of tools for chat responses.

    Chat uses a restricted set by default (read-only) to prevent prompt
    injection attacks from Telegram messages. Bash is explicitly excluded.

    Config key: tools.chat (default: Read, Glob, Grep)
    Per-project override: projects.yaml tools.chat

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        Comma-separated tool names.
    """
    return _get_tools_for_role("chat", ["Read", "Glob", "Grep"], project_name)


def get_mission_tools(project_name: str = "") -> str:
    """Get comma-separated list of tools for mission execution.

    Missions run with full tool access including Bash for code execution.

    Config key: tools.mission (default: Read, Glob, Grep, Edit, Write, Bash, Skill)
    Per-project override: projects.yaml tools.mission

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        Comma-separated tool names.
    """
    return _get_tools_for_role("mission", ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "Skill"], project_name)


def get_contemplative_tools(project_name: str = "") -> str:
    """Get comma-separated list of tools for contemplative sessions.

    Contemplative sessions use a restricted set (read + write, no Bash)
    for reflection and memory updates.

    Config key: tools.contemplative (default: Read, Write, Glob, Grep)
    Per-project override: projects.yaml tools.contemplative

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        Comma-separated tool names.
    """
    return _get_tools_for_role("contemplative", ["Read", "Write", "Glob", "Grep"], project_name)


# Backward compatibility alias
def get_allowed_tools() -> str:
    """Deprecated: Use get_chat_tools() or get_mission_tools() instead."""
    return get_mission_tools()


def get_tools_description() -> str:
    """Get tools description from config for inclusion in prompts."""
    config = _load_config()
    return config.get("tools", {}).get("description", "")


_MODEL_CONFIG_NORMALIZED = False  # Module-level guard to emit deprecation warnings once per process


def _normalize_model_config(config: dict) -> dict:
    """Normalize legacy flat/models_for_* structure to nested models.default/models.{provider}.

    Returns normalized config dict with models section structure:
    {
        "models": {
            "default": {...},
            "claude": {...},
            "codex": {...},
            ...
        },
        ...other config keys...
    }

    Detects and folds:
    - Legacy flat models.{role} keys into models.default
    - Legacy models_for_{provider} top-level keys into models.{provider}

    New structure takes precedence over legacy when both exist (collision handling).
    """
    global _MODEL_CONFIG_NORMALIZED
    normalized = config.copy()

    # Known role keys for legacy flat detection
    _KNOWN_ROLES = {"mission", "chat", "lightweight", "fallback", "review_mode", "reflect"}

    # Get the current models section
    models_section = normalized.get("models") or {}
    if not isinstance(models_section, dict):
        models_section = {}

    # Detect legacy flat layout: if models section contains role keys, it's flat
    has_legacy_flat = bool(_KNOWN_ROLES & set(models_section.keys()))

    # Detect legacy provider sections: top-level models_for_* keys
    legacy_provider_keys = [k for k in normalized.keys() if k.startswith("models_for_")]
    has_legacy_for = bool(legacy_provider_keys)

    if has_legacy_flat or has_legacy_for:
        if not _MODEL_CONFIG_NORMALIZED and not os.environ.get("_KOAN_MODELS_DEPRECATION_SHOWN"):
            _MODEL_CONFIG_NORMALIZED = True
            os.environ["_KOAN_MODELS_DEPRECATION_SHOWN"] = "1"
            deprecation_msg = (
                "[DEPRECATED] Flat 'models:' keys and 'models_for_*' top-level keys detected.\n"
                "  New structure: nest under 'models.default:' and 'models.{provider}:'.\n"
                "  See docs/users/model-configuration.md for migration guide."
            )
            print(deprecation_msg, file=sys.stderr)
        else:
            _MODEL_CONFIG_NORMALIZED = True

    # Start building normalized nested structure
    normalized_models = {}

    # Step 1: Resolve the default section. An explicit models.default always wins;
    # legacy flat roles only seed default when no explicit default exists.
    if "default" in models_section and isinstance(models_section["default"], dict):
        normalized_models["default"] = models_section["default"]
    elif has_legacy_flat:
        normalized_models["default"] = {k: v for k, v in models_section.items() if k in _KNOWN_ROLES}

    # Step 2: Fold any existing provider sections from the flat models dict
    for provider_name in models_section.keys():
        if provider_name not in _KNOWN_ROLES and provider_name != "default":
            # A provider key (like "claude", "codex") already nested under models
            if isinstance(models_section[provider_name], dict):
                normalized_models[provider_name] = models_section[provider_name]

    # Step 3: Fold legacy models_for_* top-level keys
    for key in legacy_provider_keys:
        provider_value = normalized.pop(key)
        if isinstance(provider_value, dict):
            # Extract provider name from "models_for_<name>" and normalize (underscores only)
            provider_name = key[len("models_for_") :]  # Already underscores from top-level key
            # If this provider already exists in normalized_models, new form wins
            if provider_name not in normalized_models:
                normalized_models[provider_name] = provider_value

    # Update the models section with normalized structure. Preserve any already-nested
    # structure and overlay the resolved default/provider sections on top.
    normalized["models"] = {**models_section, **normalized_models}

    return normalized


def get_model_config(project_name: str = "") -> dict:
    """Get model configuration from config.yaml with per-project overrides.

    Resolution order for each key:
    1. projects.yaml models.{key} for the project (if set) — highest priority
    2. config.yaml models.{provider}.{key} (provider-specific nested section)
    3. config.yaml models.default.{key} (global fallback)
    4. Built-in default

    Supports both legacy and new config structures:
    - Legacy flat models.{role} → normalized to models.default.{role}
    - Legacy models_for_{provider} → normalized to models.{provider}
    - New nested models.default, models.{provider}

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        Dict with keys: mission, chat, lightweight, fallback, review_mode, reflect.
        Empty strings mean "use default model".
    """
    config = _load_config()
    config = _normalize_model_config(config)

    defaults = {
        "mission": "",
        "chat": "",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
        "reflect": "",  # Model for second-pass reflection; defaults to lightweight when unset
    }

    # Get normalized models section
    models_section = config.get("models", {}) or {}
    if not isinstance(models_section, dict):
        models_section = {}

    # Get default (fallback) models
    default_models = models_section.get("default", {}) or {}
    if not isinstance(default_models, dict):
        default_models = {}

    # Start with defaults, then apply default models
    result = {k: default_models.get(k, v) for k, v in defaults.items()}

    # Apply provider-specific section per key
    try:
        from app.provider import get_provider_name

        provider_name = get_provider_name()
        # Try both hyphenated and underscored versions of the provider name
        # Users can write nested keys as either "ollama-launch" or "ollama_launch"
        provider_models = models_section.get(provider_name, {}) or {}
        if not provider_models or not isinstance(provider_models, dict):
            # Try underscore version if hyphenated didn't work
            provider_key = provider_name.replace("-", "_")
            provider_models = models_section.get(provider_key, {}) or {}

        if isinstance(provider_models, dict):
            for key in defaults:
                if key in provider_models:
                    result[key] = provider_models[key]
    except Exception as e:
        print(f"[config] provider model section lookup failed: {e}", file=sys.stderr)

    # Apply per-project overrides (highest priority)
    project_overrides = _load_project_overrides(project_name)
    project_models = project_overrides.get("models", {})
    if isinstance(project_models, dict):
        for key in defaults:
            if key in project_models:
                result[key] = project_models[key]

    return result


def get_mcp_configs(project_name: str = "") -> List[str]:
    """Get MCP server config file paths from config.yaml with per-project overrides.

    Resolution order:
    1. projects.yaml mcp list for the project (replaces global if set)
    2. config.yaml mcp list
    3. Empty list (no MCP servers)

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        List of file paths to MCP config JSON files.
    """
    config = _load_config()
    result = config.get("mcp", [])
    if not isinstance(result, list):
        result = []

    # Per-project override replaces global list entirely
    project_overrides = _load_project_overrides(project_name)
    project_mcp = project_overrides.get("mcp")
    if project_mcp is not None:
        result = project_mcp if isinstance(project_mcp, list) else []

    return [entry for entry in result if isinstance(entry, str) and entry]


# Default tier-to-resource mapping used when complexity_routing is enabled
# but specific tier values are absent from config.yaml.
_COMPLEXITY_ROUTING_DEFAULTS: dict = {
    "trivial": {"model": "haiku", "max_turns": 50, "timeout_multiplier": 0.5},
    "simple":  {"model": "sonnet", "max_turns": 100, "timeout_multiplier": 0.75},
    "medium":  {"model": "",       "max_turns": 100, "timeout_multiplier": 1.0},
    "complex":  {"model": "",       "max_turns": 500, "timeout_multiplier": 1.5},
    "critical": {"model": "",       "max_turns": 500, "timeout_multiplier": 2.0},
}


def get_complexity_routing_config(project_name: str = "") -> Optional[dict]:
    """Get complexity routing configuration with per-project overrides.

    Resolution order:
    1. Per-project ``complexity_routing`` key in projects.yaml (if set).
       - A bare ``false`` / disabled flag disables routing for that project.
    2. Global ``complexity_routing`` key in config.yaml.
    3. Returns ``None`` when routing is disabled or not configured.

    When routing is enabled the returned dict has a ``tiers`` sub-dict
    mapping tier name → {model, max_turns, timeout_multiplier}.

    An empty model string means "use whatever models.mission resolves to"
    (no override).

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        Dict with ``enabled`` and ``tiers`` keys, or ``None`` when disabled.
    """
    config = _load_config()
    global_routing = config.get("complexity_routing", {})

    # Per-project override — resolve before merging with global
    project_overrides = _load_project_overrides(project_name)
    project_routing = project_overrides.get("complexity_routing")

    # A bare False or {"enabled": false} at project level disables entirely
    if project_routing is False or (
        isinstance(project_routing, dict)
        and not project_routing.get("enabled", True)
    ):
        return None

    # Merge: start with global, apply project-level tier overrides
    if isinstance(project_routing, dict):
        routing = {**global_routing, **project_routing}
    else:
        routing = global_routing if isinstance(global_routing, dict) else {}

    # Disabled at global level
    if not routing.get("enabled", False):
        return None

    # Build merged tier map — fill missing tiers from defaults
    raw_tiers = routing.get("tiers", {})
    if not isinstance(raw_tiers, dict):
        raw_tiers = {}

    tiers: dict = {}
    for tier_name, tier_defaults in _COMPLEXITY_ROUTING_DEFAULTS.items():
        override = raw_tiers.get(tier_name, {})
        if not isinstance(override, dict):
            override = {}
        tiers[tier_name] = {**tier_defaults, **override}

    return {"enabled": True, "tiers": tiers}


def _safe_int(value, default: int) -> int:
    """Safely convert a config value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_start_on_pause() -> bool:
    """Check if start_on_pause is enabled in config.yaml.

    Returns True if koan should boot directly into pause mode.
    """
    config = _load_config()
    return bool(config.get("start_on_pause", False))


def is_focus_mode() -> bool:
    """Check if permanent focus mode is enabled via config.

    Focus mode disables all autonomous work so Kōan only runs missions
    that were explicitly queued (via Telegram, recurring, or GitHub
    @mention). No contemplative sessions, no DEEP mode, no exploration
    fallback.

    This is the config-level permanent switch. The ``/focus`` Telegram
    command provides time-bounded focus via ``.koan-focus`` file — both
    mechanisms produce the same runtime behavior.

    Resolution order:
    1. ``KOAN_FOCUS`` env var (truthy: ``1``, ``true``, ``yes``, ``on``)
    2. ``focus`` key in ``config.yaml``
    3. Default: ``False``

    Returns:
        True when permanent focus mode is active.
    """
    env_value = os.environ.get("KOAN_FOCUS", "").strip().lower()
    if env_value in ("1", "true", "yes", "on"):
        return True
    if env_value in ("0", "false", "no", "off"):
        return False
    config = _load_config()
    return bool(config.get("focus", False))


def get_start_passive() -> bool:
    """Check if start_passive is enabled in config.yaml.

    Returns True if koan should boot directly into passive mode
    (read-only: no missions, no exploration, no Claude CLI calls).
    """
    config = _load_config()
    return bool(config.get("start_passive", False))


def get_startup_reflection() -> bool:
    """Check if startup_reflection is enabled in config.yaml.

    Returns True if koan should run the self-reflection check on startup.
    Defaults to False to avoid unexpected Claude CLI calls at boot time.
    """
    config = _load_config()
    return bool(config.get("startup_reflection", False))


def get_auto_pause() -> bool:
    """Check if auto-pause is enabled in config.yaml.

    When True (default), Kōan auto-pauses after max_runs or idle timeout.
    When False, only quota exhaustion and consecutive errors trigger pause.
    """
    config = _load_config()
    value = config.get("auto_pause")
    if value is None:
        return True
    return bool(value)


def get_enable_multiple_instances() -> bool:
    """Check if multiple-instance mode is enabled in config.yaml.

    When True, suppresses warnings about @mentions from repos not in
    projects.yaml — expected when several Kōan instances share one
    GitHub account, each watching a different set of repos.
    """
    config = _load_config()
    return bool(config.get("enable_multiple_instances", False))


def get_skip_permissions() -> bool:
    """Check if skip_permissions is enabled in config.yaml.

    When True, ``--dangerously-skip-permissions`` is added to Claude CLI
    invocations — required for MCP tools to work in autonomous mode.
    """
    config = _load_config()
    return bool(config.get("skip_permissions", False))


def get_debug_enabled() -> bool:
    """Check if debug mode is enabled in config.yaml.

    When True, detailed mission execution logs are written to .koan-debug.log.
    """
    config = _load_config()
    return bool(config.get("debug", False))


def is_session_resume_enabled() -> bool:
    """Check if session resumption is enabled for post-mission reflection.

    When True, the reflection phase reuses the main mission's Claude session
    via ``--resume``, saving tokens by keeping the prior conversation context.
    Default: True (opt-out via ``session_resume_enabled: false``).
    """
    config = _load_config()
    return bool(config.get("session_resume_enabled", True))


def is_dashboard_enabled() -> bool:
    """Check if dashboard is enabled for managed startup.

    When True, ``make start`` / ``make stop`` / ``make restart`` also
    manage the dashboard process alongside run and awake.
    """
    config = _load_config()
    dashboard_cfg = config.get("dashboard", {})
    if isinstance(dashboard_cfg, dict):
        return bool(dashboard_cfg.get("enabled", False))
    return False


def get_dashboard_port() -> int:
    """Return the configured dashboard port (default: 5001)."""
    config = _load_config()
    dashboard_cfg = config.get("dashboard", {})
    if isinstance(dashboard_cfg, dict):
        return int(dashboard_cfg.get("port", 5001))
    return 5001


def get_dashboard_nickname() -> str:
    """Return the configured dashboard instance nickname (default: empty)."""
    config = _load_config()
    dashboard_cfg = config.get("dashboard", {})
    if isinstance(dashboard_cfg, dict):
        return str(dashboard_cfg.get("nickname", "")).strip()
    return ""


def is_api_enabled() -> bool:
    """Check if REST API is enabled for managed startup.

    When True, ``make start`` / ``make stop`` also manage the API process.
    Disabled by default — must be explicitly opted in.

    Config key: api.enabled (default: False)
    """
    config = _load_config()
    api_cfg = config.get("api", {})
    if isinstance(api_cfg, dict):
        return bool(api_cfg.get("enabled", False))
    return False


def is_debug_on_fix_failure() -> bool:
    """Check if auto-debug escalation is enabled for failed /fix missions.

    Config key: debug_escalation.on_fix_failure (default: False)
    """
    config = _load_config()
    cfg = config.get("debug_escalation", {})
    if isinstance(cfg, dict):
        return bool(cfg.get("on_fix_failure", False))
    return False


def get_configured_messaging_level_explicit() -> Optional[str]:
    """Return messaging.level only if explicitly set in config.yaml, else None."""
    config = _load_config()
    messaging_cfg = config.get("messaging", {})
    if isinstance(messaging_cfg, dict) and "level" in messaging_cfg:
        return str(messaging_cfg["level"]).strip().lower()
    return None


def get_configured_messaging_level() -> str:
    """Return the persistent bridge verbosity level (default: 'normal').

    Config key: messaging.level  (one of: debug, normal)
    """
    level = get_configured_messaging_level_explicit()
    return level if level in ("debug", "normal") else "normal"


def get_api_host() -> str:
    """Return the API bind host (default: 127.0.0.1).

    Config key: api.host (default: 127.0.0.1)
    """
    config = _load_config()
    api_cfg = config.get("api", {})
    if isinstance(api_cfg, dict):
        return str(api_cfg.get("host", "127.0.0.1"))
    return "127.0.0.1"


def get_api_port() -> int:
    """Return the API listen port (default: 8420).

    Config key: api.port (default: 8420)
    """
    config = _load_config()
    api_cfg = config.get("api", {})
    if isinstance(api_cfg, dict):
        return _safe_int(api_cfg.get("port", 8420), 8420)
    return 8420


def get_api_token() -> str:
    """Resolve the API bearer token.

    Resolution order:
    1. KOAN_API_TOKEN env var
    2. api.token in config.yaml
    3. Empty string (fail-closed at server startup)
    """
    token = os.environ.get("KOAN_API_TOKEN", "").strip()
    if token:
        return token
    config = _load_config()
    api_cfg = config.get("api", {})
    if isinstance(api_cfg, dict):
        return str(api_cfg.get("token", "")).strip()
    return ""


def get_api_threads() -> int:
    """Return the number of waitress worker threads (default: 8).

    Config key: api.threads (default: 8)
    """
    config = _load_config()
    api_cfg = config.get("api", {})
    if isinstance(api_cfg, dict):
        return _safe_int(api_cfg.get("threads", 8), 8)
    return 8


def get_cli_output_journal() -> bool:
    """Check if CLI output journal streaming is enabled.

    When True, mission and contemplative CLI output is streamed to the
    project's daily journal file in real-time for ``tail -f`` visibility.

    Config key: cli_output_journal (default: True — opt-out to disable).
    """
    config = _load_config()
    value = config.get("cli_output_journal")
    if value is None:
        return True
    return bool(value)


def is_ci_check_enabled() -> bool:
    """Check if the CI check system is enabled.

    Controls the entire CI check pipeline: queue draining, auto-dispatch
    of fix missions on CI failures, and the ``/ci_check`` skill command.
    Disable to save tokens when CI monitoring is not needed.

    Config key: ci_check.enabled (default: True)
    """
    config = _load_config()
    ci_cfg = config.get("ci_check", {})
    if isinstance(ci_cfg, dict):
        return bool(ci_cfg.get("enabled", True))
    if isinstance(ci_cfg, bool):
        return ci_cfg
    import sys
    print(
        f"[config] ci_check has unexpected type {type(ci_cfg).__name__!r}, defaulting to enabled",
        file=sys.stderr,
    )
    return True


def is_unlimited_quota() -> bool:
    """Return True when the operator declares the CLI provider has no quota limit.

    When enabled, all proactive quota gating is disabled: no budget-based mode
    downgrades, no burn-rate warnings, no preflight quota probes.  Reactive
    detection (CLI exits with a quota error) still works — if the provider
    actually hits a limit, Koan pauses and requeues as usual.

    Config key: usage.unlimited_quota (default: False).

    Never raises — returns False on any failure so callers need no wrapping.
    """
    try:
        config = _load_config()
        usage = config.get("usage", {})
        if not isinstance(usage, dict):
            return False
        if "unlimited_quota" in usage:
            return bool(usage.get("unlimited_quota", False))
        return bool(config.get("unlimited_quota", False))
    except Exception as e:
        print(f"[config] is_unlimited_quota error: {e}", file=sys.stderr)
        return False


def get_max_runs() -> int:
    """Get maximum runs per day from config.yaml.

    This is the primary source of truth for max_runs configuration.
    Returns default of 60 if not configured.
    """
    config = _load_config()
    return _safe_int(config.get("max_runs_per_day", 60), 60)


def get_interval_seconds() -> int:
    """Get interval between runs in seconds from config.yaml.

    This is the primary source of truth for run interval configuration.
    Returns default of 300 (5 minutes) if not configured.
    """
    config = _load_config()
    return _safe_int(config.get("interval_seconds", 300), 300)


def get_same_project_stickiness_percent() -> int:
    """Get same-project stickiness chance (0-100) for cache reuse.

    When > 0, autonomous exploration may intentionally stay on the same
    project as the previous run with this probability. This helps keep
    prompt prefixes cache-hot across consecutive runs on the same project.

    Config key: prompt_caching.same_project_stickiness_percent
    Default: 0 (disabled, preserves legacy anti-repeat behavior)
    """
    config = _load_config()
    prompt_cfg = config.get("prompt_caching", {})
    if not isinstance(prompt_cfg, dict):
        return 0
    value = _safe_int(prompt_cfg.get("same_project_stickiness_percent", 0), 0)
    return max(0, min(100, value))


def get_fast_reply_model() -> str:
    """Get model to use for fast replies (command handlers like /usage, /sparring).

    When config.fast_reply is True, returns the lightweight model (usually Haiku)
    for faster, cheaper responses. When False, returns empty string (use default).

    Returns:
        Model name string (e.g., "haiku") or empty string for default model.
    """
    config = _load_config()
    fast_reply = config.get("fast_reply", False)
    if fast_reply:
        models = get_model_config()
        return models["lightweight"]
    return ""


def get_branch_prefix() -> str:
    """Get the branch prefix used for agent-created branches.

    Reads 'branch_prefix' from config.yaml. Defaults to 'koan' if not set.
    Always returns the prefix with a trailing '/' (e.g., 'koan/').

    This allows multiple bot instances to use distinct prefixes
    (e.g., 'koan-bot1/', 'koan-bot2/') so their branches don't collide.
    """
    config = _load_config()
    prefix = config.get("branch_prefix", "").strip()
    if not prefix:
        prefix = "koan"
    # Strip trailing slash if present, we'll add it ourselves
    prefix = prefix.rstrip("/")
    return f"{prefix}/"


def get_skill_timeout() -> int:
    """Get timeout in seconds for skill execution (fix, implement, recreate).

    Controls how long Claude CLI calls are allowed to run before being
    killed.  This applies to the heavy-lifting skills that invoke Claude
    with full tool access.

    Config key: skill_timeout (default: 7200 — 2 hours).

    Returns:
        Timeout in seconds.
    """
    config = _load_config()
    return _safe_int(config.get("skill_timeout", 7200), 7200)


def _missions_section() -> dict:
    return _load_config().get("missions", {}) or {}


def get_missions_done_keep() -> int:
    """Max Done items retained in missions.md. Config: missions.done_keep (50)."""
    return _safe_int(_missions_section().get("done_keep", 50), 50)


def get_missions_failed_keep() -> int:
    """Max Failed items retained in missions.md. Config: missions.failed_keep (30)."""
    return _safe_int(_missions_section().get("failed_keep", 30), 30)


def get_missions_max_lines() -> int:
    """Hard line cap for missions.md; 0 disables. Config: missions.max_lines (500)."""
    return _safe_int(_missions_section().get("max_lines", 500), 500)


def get_mission_timeout() -> int:
    """Get timeout in seconds for regular mission execution.

    Controls the watchdog timer for Claude CLI missions dispatched from
    the main agent loop. Prevents runaway sessions that block the queue.

    Config key: mission_timeout (default: 3600 — 60 minutes).
    Set to 0 to disable the timeout (not recommended).

    Returns:
        Timeout in seconds.
    """
    config = _load_config()
    return _safe_int(config.get("mission_timeout", 3600), 3600)


def get_first_output_timeout() -> int:
    """Get timeout in seconds for first output from CLI subprocesses.

    If the Claude CLI produces zero stdout within this window, the
    process is killed early instead of waiting the full skill/mission
    timeout. A session that is silent for this long is almost certainly
    stuck (API hang, network issue, quota wait).

    Config key: first_output_timeout (default: 600 — 10 minutes).
    Set to 0 to disable.

    Returns:
        Timeout in seconds.
    """
    config = _load_config()
    return _safe_int(config.get("first_output_timeout", 600), 600)


def get_rebase_first_output_timeout() -> int:
    """Get first-output timeout override for /rebase skill missions.

    Uses ``rebase_first_output_timeout`` when configured, otherwise falls
    back to ``first_output_timeout``.
    """
    config = _load_config()
    default_timeout = _safe_int(config.get("first_output_timeout", 600), 600)
    return _safe_int(config.get("rebase_first_output_timeout", default_timeout), default_timeout)


def get_rebase_review_idle_timeout() -> int:
    """Get inactivity timeout for /rebase review-feedback Claude step.

    If no real CLI/tool output appears for this long, the step is
    considered stalled and is aborted.

    Config key: rebase_review_idle_timeout.
    Fallback: rebase_first_output_timeout.
    """
    config = _load_config()
    fallback = get_rebase_first_output_timeout()
    return _safe_int(config.get("rebase_review_idle_timeout", fallback), fallback)


def get_rebase_review_max_duration() -> int:
    """Get hard wall-clock cap for /rebase review-feedback Claude step.

    Allows long active reviews to continue while still enforcing an upper
    bound on total runtime.

    Config key: rebase_review_max_duration.
    Fallback: skill_timeout.
    """
    config = _load_config()
    fallback = get_skill_timeout()
    return _safe_int(config.get("rebase_review_max_duration", fallback), fallback)


def get_rebase_ci_idle_timeout() -> int:
    """Get inactivity timeout for /rebase CI-fix Claude steps.

    Config key: rebase_ci_idle_timeout.
    Fallback: rebase_first_output_timeout.
    """
    config = _load_config()
    fallback = get_rebase_first_output_timeout()
    return _safe_int(config.get("rebase_ci_idle_timeout", fallback), fallback)


def get_rebase_ci_max_duration() -> int:
    """Get hard wall-clock cap for /rebase CI-fix Claude steps.

    Config key: rebase_ci_max_duration.
    Fallback: skill_timeout.
    """
    config = _load_config()
    fallback = get_skill_timeout()
    return _safe_int(config.get("rebase_ci_max_duration", fallback), fallback)


def get_rebase_include_bot_feedback() -> bool:
    """Whether /rebase review feedback should include bot-authored comments.

    When true (default), rebase feedback prompts include bot-authored
    review/issue comments. Set false to keep noisy CI/bot output out of the
    prompt and use only human-authored feedback.
    """
    config = _load_config()
    return bool(config.get("rebase_include_bot_feedback", True))


def is_rebase_foreign_prs_allowed() -> bool:
    """Allow Telegram /rebase to target PRs from other branch prefixes.

    Config key: allow_rebase_foreign_prs (default: False).
    """
    config = _load_config()
    return bool(config.get("allow_rebase_foreign_prs", False))


def is_strip_co_authored_by_enabled() -> bool:
    """Whether to strip Co-Authored-By / "Generated with Claude Code" trailers
    from generated commit messages.

    Off by default — commits keep whatever trailers the CLI appends. Operators
    who want Kōan commits to land under their own git identity with no co-author
    attribution can opt in via config.

    Config key: strip_co_authored_by (default: False).
    """
    config = _load_config()
    return bool(config.get("strip_co_authored_by", False))


def get_skill_max_turns() -> int:
    """Get max turns for skill execution (fix, implement, incident).

    Controls the maximum number of agentic turns Claude CLI is allowed
    to take during heavy-lifting skill invocations. Higher values allow
    complex implementations to complete without hitting the ceiling.

    Config key: skill_max_turns (default: 200).

    Returns:
        Maximum number of turns.
    """
    config = _load_config()
    return _safe_int(config.get("skill_max_turns", 200), 200)


def get_analysis_max_turns() -> int:
    """Get max turns for read-only analysis skills (dead_code, tech_debt, audit).

    These skills only use read tools (Read, Glob, Grep) and need fewer turns
    than implementation skills, but the previous hardcoded defaults (25-30)
    were too tight for non-trivial codebases.

    Config key: analysis_max_turns (default: 75).

    Returns:
        Maximum number of turns.
    """
    config = _load_config()
    return _safe_int(config.get("analysis_max_turns", 75), 75)


def get_rebase_max_conflict_rounds() -> int:
    """Get max conflict resolution rounds for rebase (default 10)."""
    config = _load_config()
    return max(1, _safe_int(config.get("rebase_max_conflict_rounds", 10), 10))


def get_contemplative_max_turns() -> int:
    """Get max turns for contemplative reflection sessions.

    Contemplative prompts read several memory files (soul.md, summary.md,
    personality-evolution.md, learnings.md) and write output, requiring at
    least 6-7 tool calls.  The previous hardcoded value of 10 was too tight
    for projects with complex memory state.

    Config key: contemplative_max_turns (default: 15).

    Returns:
        Maximum number of turns.
    """
    config = _load_config()
    return _safe_int(config.get("contemplative_max_turns", 15), 15)


def get_post_mission_timeout() -> int:
    """Get timeout in seconds for the post-mission pipeline.

    Controls the overall deadline for post-mission steps: verification,
    reflection, PR review learning, and auto-merge.  Without this ceiling,
    accumulated steps can block the agent loop for too long.

    Config key: post_mission_timeout (default: 300 — 5 minutes).

    Returns:
        Timeout in seconds.
    """
    config = _load_config()
    return _safe_int(config.get("post_mission_timeout", 300), 300)


def get_notify_mission_results() -> bool:
    """Whether to forward Claude's mission result text to outbox.md.

    When True, the post-mission pipeline appends the Claude session's final
    result string to outbox.md whenever it indicates an alert outcome
    (SKIP/FAIL/ERROR/BLOCKED) or comes from a skill that opted in via
    ``forward_result: true`` in its SKILL.md. Guarantees the user sees the
    result on Telegram even when the Claude session's sandbox blocked writes
    to instance/.

    Config key: notify_mission_results (default: True).
    """
    config = _load_config()
    val = config.get("notify_mission_results", True)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "no", "0", "off")
    return True


# Default effort levels per autonomous mode.
# Keys are autonomous modes, values are Claude CLI --effort levels.
# "medium" is the provider default when no flag is passed — omitted here
# so no flag is emitted unless the user configures an override.
_DEFAULT_EFFORT_MAP = {
    "review": "low",
    "implement": "",
    "deep": "high",
}

# Valid effort levels (matches Claude CLI --effort flag).
_VALID_EFFORT_LEVELS = {"low", "medium", "high", "max", ""}


def get_effort_for_mode(autonomous_mode: str = "") -> str:
    """Get the reasoning effort level for the given autonomous mode.

    Reads ``effort:`` section from config.yaml. Supports per-mode overrides:

        effort:
          review: low
          implement: medium
          deep: high

    Or a single value to apply to all modes:

        effort: high

    Set ``effort: ""`` or omit the section entirely to disable effort
    control (no ``--effort`` flag will be emitted).

    Args:
        autonomous_mode: Current mode (review/implement/deep/wait).

    Returns:
        Effort level string (e.g. "low", "high", "max") or empty string.
    """
    config = _load_config()
    effort_config = config.get("effort")

    if effort_config is None:
        # No config — use defaults
        return _DEFAULT_EFFORT_MAP.get(autonomous_mode, "")

    if isinstance(effort_config, str):
        # Single value for all modes
        level = effort_config.strip().lower()
        return level if level in _VALID_EFFORT_LEVELS else ""

    if isinstance(effort_config, dict):
        # Per-mode overrides
        level = str(effort_config.get(autonomous_mode, "")).strip().lower()
        if level in _VALID_EFFORT_LEVELS:
            return level
        # Fall back to defaults if mode not in config
        return _DEFAULT_EFFORT_MAP.get(autonomous_mode, "")

    return ""


# -- Thinking / extended reasoning configuration ----------------------------

# Mode hierarchy for the ``min_mode`` gate.  Modes to the right are
# "higher" — thinking is only enabled when the current mode's rank is
# >= the configured minimum.
_MODE_RANK = {"wait": 0, "review": 1, "implement": 2, "deep": 3}


def get_thinking_config() -> dict:
    """Return the ``thinking:`` section from config.yaml.

    Expected shape::

        thinking:
          enabled: true          # master switch (default false)
          budget_tokens: 10000   # soft thinking-token cap (default 0 = no cap)
          min_mode: deep         # minimum autonomous mode (default "deep")

    Returns a dict with keys ``enabled`` (bool), ``budget_tokens`` (int),
    and ``min_mode`` (str).
    """
    config = _load_config()
    section = config.get("thinking") or {}
    if not isinstance(section, dict):
        return {"enabled": False, "budget_tokens": 0, "min_mode": "deep"}
    return {
        "enabled": bool(section.get("enabled", False)),
        "budget_tokens": int(section.get("budget_tokens", 0)),
        "min_mode": str(section.get("min_mode", "deep")).strip().lower(),
    }


def should_enable_thinking(autonomous_mode: str = "", tier: str = "") -> bool:
    """Return True if thinking should be activated.

    Thinking is only enabled when ALL conditions are met:
    1. The ``thinking:`` config master switch is on.
    2. The mission's complexity tier is ``critical``.
    3. The current autonomous mode is at or above ``min_mode``.

    This ties extended thinking to mission complexity rather than a
    blanket boolean — only the most complex missions benefit.
    """
    cfg = get_thinking_config()
    if not cfg["enabled"]:
        return False
    if tier != "critical":
        return False
    current_rank = _MODE_RANK.get(autonomous_mode, -1)
    min_rank = _MODE_RANK.get(cfg["min_mode"], 3)
    return current_rank >= min_rank


def get_stagnation_config(project_name: str = "") -> dict:
    """Get stagnation-monitor configuration.

    The stagnation monitor watches a running Claude CLI mission for a
    stuck-in-a-loop pattern (identical trailing stdout hash across
    several samples) and kills the subprocess before the full mission
    timeout elapses, saving quota.

    Config keys (under ``stagnation:`` in ``config.yaml``):
        enabled (bool): master switch (default True).
        check_interval_seconds (int): seconds between samples (default 60).
        abort_after_cycles (int): consecutive identical samples required
            to trigger abort. Must be >= 2. Default 3.
        sample_lines (int): trailing stdout lines hashed each sample
            (default 50).
        max_retry_on_stagnation (int): how many times a stagnated mission
            is re-queued before being marked Failed. ``0`` disables the
            retry loop entirely (mission is failed on the first stagnation).
            Default 3.
        max_total_retries (int): ceiling on combined retry attempts across
            both stagnation requeues and crash-recovery requeues for the same
            logical mission. ``0`` disables the combined cap (independent
            per-system limits still apply). Default 0 (disabled). When set,
            provides a single operator knob to bound the total number of
            automatic retry attempts regardless of which system triggered them.
        max_crash_retries (int): maximum crash-recovery attempts before
            escalating a mission to Failed. Must be >= 1. Default 3.

    Per-project overrides via ``projects.yaml`` ``stagnation:`` take
    precedence. Setting ``enabled: false`` at project level disables the
    monitor for that project only. Setting it to the boolean ``false``
    directly (``stagnation: false``) is also accepted as a shortcut.

    Args:
        project_name: Optional project name for per-project overrides.

    Returns:
        Dict with the resolved values — always contains all seven keys.
    """
    defaults = {
        "enabled": True,
        "check_interval_seconds": 60,
        "abort_after_cycles": 3,
        "sample_lines": 50,
        "max_retry_on_stagnation": 3,
        "max_total_retries": 0,
        "max_crash_retries": 3,
    }
    config = _load_config()
    base = config.get("stagnation", {})
    if base is False:
        base = {"enabled": False}
    elif not isinstance(base, dict):
        base = {}

    project_overrides = _load_project_overrides(project_name)
    proj = project_overrides.get("stagnation", {})
    if proj is False:
        proj = {"enabled": False}
    elif not isinstance(proj, dict):
        proj = {}

    merged = {**defaults, **base, **proj}

    abort_after = _safe_int(merged.get("abort_after_cycles"), defaults["abort_after_cycles"])
    if abort_after < 2:
        abort_after = 2

    max_retry = _safe_int(merged.get("max_retry_on_stagnation"), defaults["max_retry_on_stagnation"])
    if max_retry < 0:
        max_retry = 0

    max_total = _safe_int(merged.get("max_total_retries"), defaults["max_total_retries"])
    if max_total < 0:
        max_total = 0

    max_crash = _safe_int(merged.get("max_crash_retries"), defaults["max_crash_retries"])
    if max_crash < 1:
        max_crash = 1

    return {
        "enabled": bool(merged.get("enabled", defaults["enabled"])),
        "check_interval_seconds": max(
            1, _safe_int(merged.get("check_interval_seconds"), defaults["check_interval_seconds"]),
        ),
        "abort_after_cycles": abort_after,
        "sample_lines": max(1, _safe_int(merged.get("sample_lines"), defaults["sample_lines"])),
        "max_retry_on_stagnation": max_retry,
        "max_total_retries": max_total,
        "max_crash_retries": max_crash,
    }


def get_autonomous_health_config() -> dict:
    """Get autonomous health diagnostic configuration.

    When a project's recent success rate falls below a threshold and it
    has accumulated enough stagnation/empty sessions, the iteration
    manager can autonomously inject a diagnostic mission (tech_debt,
    dead_code, or audit) instead of regular exploration.

    Config keys (under ``autonomous_health:`` in ``config.yaml``):
        enabled (bool): master switch (default False — opt-in).
        success_rate_floor (float): success rate below which diagnostics
            trigger. Default 0.25.
        staleness_floor (int): consecutive non-productive sessions
            required (from get_staleness_score). Default 3.
        cooldown_days (int): minimum days between diagnostic missions
            for the same project. Default 21.
        min_mode (str): minimum autonomous mode required. Default
            "implement" (also allows "deep").

    Returns:
        Dict with resolved values — always contains all keys.
    """
    defaults = {
        "enabled": False,
        "success_rate_floor": 0.25,
        "staleness_floor": 3,
        "cooldown_days": 21,
        "min_mode": "implement",
    }
    config = _load_config()
    section = config.get("autonomous_health", {})
    if section is False:
        section = {"enabled": False}
    elif not isinstance(section, dict):
        section = {}

    merged = {**defaults, **section}

    staleness_floor = _safe_int(merged.get("staleness_floor"), defaults["staleness_floor"])
    if staleness_floor < 1:
        staleness_floor = 1
    cooldown_days = _safe_int(merged.get("cooldown_days"), defaults["cooldown_days"])
    if cooldown_days < 1:
        cooldown_days = 1

    try:
        success_rate_floor = float(merged.get("success_rate_floor", defaults["success_rate_floor"]))
    except (ValueError, TypeError):
        success_rate_floor = defaults["success_rate_floor"]
    success_rate_floor = max(0.0, min(1.0, success_rate_floor))

    min_mode = str(merged.get("min_mode", defaults["min_mode"]))
    if min_mode not in ("review", "implement", "deep"):
        min_mode = defaults["min_mode"]

    return {
        "enabled": bool(merged.get("enabled", defaults["enabled"])),
        "success_rate_floor": success_rate_floor,
        "staleness_floor": staleness_floor,
        "cooldown_days": cooldown_days,
        "min_mode": min_mode,
    }


def get_plan_review_config() -> dict:
    """Get plan review loop configuration from config.yaml.

    Controls whether a lightweight subagent reviews generated plans before
    they are posted to GitHub, and how many re-generation rounds are allowed.

    Config key: plan_review (default: enabled=True, max_rounds=3,
    implement_gate=True, assumptions_check=True)

    Returns:
        Dict with keys:
          - enabled (bool): Whether the review loop runs (default: True)
          - max_rounds (int): Maximum re-generation rounds (default: 3)
          - implement_gate (bool): Whether /implement runs a plan-review
            gate before execution (default: True)
          - assumptions_check (bool): Whether to run assumptions
            pressure-test before structural critic (default: True)
    """
    config = _load_config()
    plan_review = config.get("plan_review", {})
    if not isinstance(plan_review, dict):
        plan_review = {}
    return {
        "enabled": bool(plan_review.get("enabled", True)),
        "max_rounds": _safe_int(plan_review.get("max_rounds", 3), 3),
        "implement_gate": bool(plan_review.get("implement_gate", True)),
        "assumptions_check": bool(plan_review.get("assumptions_check", True)),
    }


_PRIVATE_REVIEW_GATE_DEFAULTS = {
    "enabled": False,
    "max_rounds": 3,
    "min_severity": "warning",
    "enabled_skills": ["fix", "implement", "rebase"],
    "budget_aware": True,
    "dedup": True,
    "tracker_max_age_days": 30,
}

_PRIVATE_REVIEW_SEVERITY_ALIASES = {
    "critical": "critical",
    "blocking": "critical",
    "warning": "warning",
    "important": "warning",
    "high": "warning",
    "suggestion": "suggestion",
    "suggestions": "suggestion",
    "all": "suggestion",
}


def _safe_bool(value, default: bool) -> bool:
    """Safely coerce common config bool shapes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "yes", "1", "on", "enabled"):
            return True
        if normalized in ("false", "no", "0", "off", "disabled", ""):
            return False
    return default


def _normalize_private_review_severity(value) -> str:
    """Map user-facing severity tokens to review schema severities."""
    token = str(value or "").strip().lower()
    return _PRIVATE_REVIEW_SEVERITY_ALIASES.get(
        token,
        _PRIVATE_REVIEW_GATE_DEFAULTS["min_severity"],
    )


def _normalize_private_review_gate_skills(value) -> list:
    """Return configured skill names for the shared private review gate."""
    default = list(_PRIVATE_REVIEW_GATE_DEFAULTS["enabled_skills"])
    if value is None:
        return default
    if isinstance(value, str):
        raw_items = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return default
    return [
        item for item in (
            str(raw).strip().lower() for raw in raw_items
        )
        if item
    ]


def _review_gate_section(config: dict, key: str) -> dict:
    section = config.get(key, {})
    return section if isinstance(section, dict) else {}


def get_private_review_gate_config(
    project_name: str = "",
    skill_origin: str = "",
) -> dict:
    """Get the shared private review gate configuration.

    Config key: private_review_gate.

      - enabled (bool): run the backend-only PR review/fix loop. Default:
        False (opt-in during the testing phase; enable per-instance/project).
      - max_rounds (int): maximum review/fix rounds. Default: 3.
      - min_severity (str): lowest severity to auto-fix. Default: warning
        (aliases: important/high).
      - enabled_skills (list[str] or str): skills that use the gate.
        Default: fix, implement, rebase.
      - budget_aware (bool): skip/limit rounds under quota pressure via the
        usage governor. Default: True.
      - dedup (bool): skip re-reviewing a PR head already reviewed clean.
        Default: True.
      - tracker_max_age_days (int): dedup tracker entry retention. Default: 30.

    Per-project overrides in projects.yaml use the same key and override
    global values one field at a time.
    """
    config = _load_config()
    merged = {
        **_PRIVATE_REVIEW_GATE_DEFAULTS,
        **_review_gate_section(config, "private_review_gate"),
    }

    project_overrides = _load_project_overrides(project_name)
    merged.update(
        _review_gate_section(project_overrides, "private_review_gate")
    )

    max_rounds = _safe_int(
        merged.get("max_rounds"),
        _PRIVATE_REVIEW_GATE_DEFAULTS["max_rounds"],
    )
    enabled_skills = _normalize_private_review_gate_skills(
        merged.get("enabled_skills"),
    )
    enabled = _safe_bool(
        merged.get("enabled"),
        _PRIVATE_REVIEW_GATE_DEFAULTS["enabled"],
    )
    skill = str(skill_origin or "").strip().lower()
    if skill and skill not in enabled_skills:
        enabled = False

    return {
        "enabled": enabled,
        "max_rounds": max(0, max_rounds),
        "min_severity": _normalize_private_review_severity(
            merged.get("min_severity"),
        ),
        "enabled_skills": enabled_skills,
        "budget_aware": _safe_bool(
            merged.get("budget_aware"),
            _PRIVATE_REVIEW_GATE_DEFAULTS["budget_aware"],
        ),
        "dedup": _safe_bool(
            merged.get("dedup"),
            _PRIVATE_REVIEW_GATE_DEFAULTS["dedup"],
        ),
        "tracker_max_age_days": max(0, _safe_int(
            merged.get("tracker_max_age_days"),
            _PRIVATE_REVIEW_GATE_DEFAULTS["tracker_max_age_days"],
        )),
    }


def get_skill_allowed_hosts() -> List[str]:
    """Return the optional Git-host allow-list for /skill install.

    Read from ``skills.allowed_hosts`` in config.yaml. Each entry is a
    ``host`` or ``host/path-prefix`` (e.g. ``github.com/myorg``). An empty
    or missing list means no host restriction — the approval gate still
    applies.
    """
    config = _load_config()
    skills_cfg = config.get("skills", {}) or {}
    hosts = skills_cfg.get("allowed_hosts", []) or []
    if not isinstance(hosts, list):
        return []
    return [str(h).strip() for h in hosts if str(h).strip()]


def get_contemplative_chance() -> int:
    """Get probability (0-100) of triggering contemplative mode on autonomous runs.

    When no mission is pending, this is the chance that koan will run a
    contemplative session instead of autonomous work. Allows for regular
    moments of reflection without waiting for budget exhaustion.

    Returns:
        Integer percentage (0-100). Default: 10 (one in ten autonomous runs).
    """
    config = _load_config()
    value = _safe_int(config.get("contemplative_chance", 10), 10)
    return max(0, min(100, value))


def build_claude_flags(
    model: str = "",
    fallback: str = "",
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build extra CLI flags — provider-aware.

    Delegates to the configured CLI provider for proper flag generation.

    Args:
        model: Model name/alias (empty = use default)
        fallback: Fallback model when primary is overloaded (empty = none)
        disallowed_tools: Tools to block (e.g., ["Bash", "Edit", "Write"] for read-only)

    Returns:
        List of CLI flag strings to append to the command.
    """
    from app.cli_provider import build_cli_flags
    return build_cli_flags(model=model, fallback=fallback, disallowed_tools=disallowed_tools)


def get_claude_flags_for_role(
    role: str, autonomous_mode: str = "", project_name: str = ""
) -> str:
    """Get CLI flags for a Claude invocation role, as a space-separated string.

    Provider-aware: delegates to the configured CLI provider for proper flag generation.
    Supports per-project model overrides from projects.yaml.

    Args:
        role: One of "mission", "chat", "lightweight", "contemplative"
        autonomous_mode: Current mode (review/implement/deep) — affects tool restrictions
        project_name: Optional project name for per-project model overrides

    Returns:
        Space-separated CLI flags string (may be empty)
    """
    from app.cli_provider import get_provider

    models = get_model_config(project_name)
    provider = get_provider()

    model = ""
    fallback = ""
    disallowed: Optional[List[str]] = None

    if role == "mission":
        model = models["mission"]
        if autonomous_mode == "review" and models["review_mode"]:
            model = models["review_mode"]
        fallback = models["fallback"]
        if autonomous_mode == "review":
            disallowed = ["Bash", "Edit", "Write"]
    elif role == "contemplative":
        model = models["lightweight"]
    elif role == "chat":
        model = models["chat"]
        fallback = models["fallback"]

    flags = provider.build_extra_flags(model=model, fallback=fallback, disallowed_tools=disallowed)
    return " ".join(flags)


def get_cli_binary_for_shell() -> str:
    """Get the CLI binary name for shell scripts.

    Returns the binary command (e.g., "claude", "copilot", "gh copilot").
    Called from run.py to set CLI_BIN.
    """
    from app.cli_provider import get_cli_binary
    return get_cli_binary()


def get_cli_provider_name() -> str:
    """Get the configured CLI provider name for display.

    Returns "claude", "codex", "copilot", or "ollama-launch".
    """
    from app.cli_provider import get_provider_name
    return get_provider_name()


def get_auto_merge_config(config: dict, project_name: str) -> dict:
    """Get auto-merge config with per-project override support.

    Resolution order:
    1. projects.yaml (if it exists) — per-project git_auto_merge
    2. config.yaml — global git_auto_merge only

    Args:
        config: Full config dict from load_config()
        project_name: Name of the project (e.g., "koan", "anantys-back")

    Returns:
        Merged config with keys: enabled, base_branch, strategy, rules
    """
    # Try projects.yaml first
    try:
        from app.projects_config import load_projects_config, get_project_auto_merge
        koan_root = os.environ.get("KOAN_ROOT", "")
        projects_config = load_projects_config(koan_root) if koan_root else None
        if projects_config and project_name in projects_config.get("projects", {}):
            return get_project_auto_merge(projects_config, project_name)
    except Exception as e:
        print(f"[config] Auto-merge config load error for {project_name}: {e}", file=sys.stderr)

    # Fall back to config.yaml global settings
    global_cfg = config.get("git_auto_merge", {})
    return {
        "enabled": global_cfg.get("enabled", True),
        "base_branch": global_cfg.get("base_branch", "main"),
        "strategy": global_cfg.get("strategy", "squash"),
        "rules": global_cfg.get("rules", []),
    }


def get_branch_cleanup_config() -> dict:
    """Get branch cleanup configuration from config.yaml.

    Controls automatic deletion of merged local and remote branches during
    git sync. Cleanup runs every ``git_sync_interval`` iterations for each
    project.

    Config key: branch_cleanup
      - enabled (bool): Master switch (default: True)
      - delete_remote_branches (bool): Also push-delete remote branches
          after local deletion (default: True). Set to False to only
          clean up local refs without touching the remote.

    Returns:
        Dict with keys: enabled (bool), delete_remote_branches (bool).
    """
    config = _load_config()
    cleanup_cfg = config.get("branch_cleanup", {})
    if not isinstance(cleanup_cfg, dict):
        cleanup_cfg = {}
    return {
        "enabled": bool(cleanup_cfg.get("enabled", True)),
        "delete_remote_branches": bool(cleanup_cfg.get("delete_remote_branches", True)),
        "cleanup_interval_hours": int(cleanup_cfg.get("cleanup_interval_hours", 24)),
        "notify_orphans": bool(cleanup_cfg.get("notify_orphans", True)),
    }


def get_prompt_guard_config() -> dict:
    """Get prompt guard configuration.

    Returns:
        Dict with keys: enabled (bool), block_mode (bool).
        Defaults: enabled=True, block_mode=True (reject).
    """
    config = _load_config()
    guard_cfg = config.get("prompt_guard", {})
    return {
        "enabled": guard_cfg.get("enabled", True),
        "block_mode": guard_cfg.get("block_mode", True),
    }


def get_review_concurrency_config() -> dict:
    """Get review concurrency configuration from config.yaml.

    Controls parallelism for GitHub API calls during PR reviews. The LLM
    call (Claude CLI) is always sequential — only GitHub data-fetching is
    parallelised.

    Config key: review_concurrency
      - enabled (bool): Enable parallel GitHub API fetches (default: True)
      - github_workers (int): Max concurrent GitHub API calls (default: 4)

    Returns:
        Dict with keys:
          - enabled (bool): Whether parallel fetching is active.
          - github_workers (int): ThreadPoolExecutor max_workers for gh calls.
    """
    config = _load_config()
    review_cfg = config.get("review_concurrency", {})
    if not isinstance(review_cfg, dict):
        review_cfg = {}
    return {
        "enabled": bool(review_cfg.get("enabled", True)),
        "github_workers": _safe_int(review_cfg.get("github_workers", 4), 4),
    }


def get_recovery_config() -> dict:
    """Get crash and error recovery configuration from config.yaml.

    Controls how the agent loop handles consecutive iteration errors and
    unexpected crashes in main().  All values have defaults so recovery
    works out of the box even when the section is absent.

    Config key: recovery
      - max_consecutive_errors (int): Pause after this many consecutive
            iteration errors. Default: 10.
      - max_main_crashes (int): Give up after this many crashes in main().
            Default: 5.
      - backoff_multiplier (int): Linear backoff step in seconds.
            Default: 10.
      - max_backoff_main (int): Backoff ceiling for main() crashes.
            Default: 60.
      - max_backoff_iteration (int): Backoff ceiling for iteration errors.
            Default: 300.
      - error_notification_interval (int): Notify every N errors after the
            first. Default: 5.

    Returns:
        Dict with all keys present and values as ints.
    """
    defaults = {
        "max_consecutive_errors": 10,
        "max_main_crashes": 5,
        "backoff_multiplier": 10,
        "max_backoff_main": 60,
        "max_backoff_iteration": 300,
        "error_notification_interval": 5,
    }
    config = _load_config()
    section = config.get("recovery", {})
    if not isinstance(section, dict):
        section = {}

    result = {}
    for key, default in defaults.items():
        result[key] = _safe_int(section.get(key, default), default)
    return result


def get_review_reply_config() -> dict:
    """Get review reply guard configuration from config.yaml.

    Controls self-reply prevention and thread depth limits for PR review
    comment replies.

    Config key: review_reply
      - max_thread_depth (int): Stop replying in a thread after this many
            total comments (default: 5).

    Returns:
        Dict with keys:
          - max_thread_depth (int): Maximum comments per thread.
    """
    config = _load_config()
    review_cfg = config.get("review_reply", {})
    if not isinstance(review_cfg, dict):
        review_cfg = {}
    return {
        "max_thread_depth": _safe_int(review_cfg.get("max_thread_depth", 5), 5),
    }


def get_review_ignore_config() -> dict:
    """Get review ignore patterns from config.yaml.

    Controls which files are excluded from PR review diffs. Patterns are
    applied before building the Claude prompt, reducing token spend on
    generated code, lock files, and vendor directories.

    Config key: review_ignore
      - glob (list): Glob patterns (e.g. "vendor/**", "*.lock")
      - regex (list): Regex patterns matched against full path

    Returns:
        Dict with keys: glob (list), regex (list). Both always present;
        values default to [].
    """
    config = _load_config()
    review_ignore = config.get("review_ignore", {}) or {}
    if not isinstance(review_ignore, dict):
        return {"glob": [], "regex": []}

    globs = review_ignore.get("glob", [])
    if not isinstance(globs, list):
        globs = []

    regexes = review_ignore.get("regex", [])
    if not isinstance(regexes, list):
        regexes = []

    return {"glob": [str(p) for p in globs], "regex": [str(p) for p in regexes]}


def get_review_reflect_config() -> dict:
    """Get review reflection pass configuration from config.yaml.

    The reflection pass runs a second lightweight Claude call to score
    each finding and filter low-signal suggestions before posting.

    Config key: review_reflect
      - threshold (int, 0-10): Minimum score for a finding to be kept.
        Default: 5. Set to 0 to disable filtering (all findings pass).

    Returns:
        Dict with key: threshold (int). Always present; defaults to 5.
    """
    config = _load_config()
    reflect_cfg = config.get("review_reflect", {}) or {}
    if not isinstance(reflect_cfg, dict):
        reflect_cfg = {}
    threshold = reflect_cfg.get("threshold", 5)
    try:
        threshold = int(threshold)
    except (TypeError, ValueError):
        threshold = 5
    return {"threshold": max(0, min(10, threshold))}


def get_review_memory_config() -> dict:
    """Get the review session-memory injection configuration from config.yaml.

    When enabled, the review prompt also includes recent typed project memory
    (decisions, observations — not learnings, which are already injected) from
    the persistent FTS5 memory index, ranked against the PR content. Off by
    default so the extra prompt tokens are opt-in.

    Config key: review_memory
      - enabled (bool): inject recent session memory into reviews.
        Default: False.
      - max_entries (int): maximum memory entries to include. Default: 8.

    Returns:
        Dict with keys: enabled (bool), max_entries (int >= 0).
    """
    config = _load_config()
    mem_cfg = config.get("review_memory", {}) or {}
    if not isinstance(mem_cfg, dict):
        mem_cfg = {}
    max_entries = _safe_int(mem_cfg.get("max_entries"), 8)
    return {
        "enabled": _safe_bool(mem_cfg.get("enabled"), False),
        "max_entries": max(0, max_entries),
    }


def get_review_context_config() -> dict:
    """Get the /review existing-comment context configuration from config.yaml.

    Controls how `/review` surfaces existing PR comments in its prompt. The
    bot's own most recent structured review is injected into a dedicated
    ``{PRIOR_REVIEW}`` prompt slot (head-preserving budget) so re-reviews build
    on it instead of losing it to the recency-truncated conversation thread.

    Config key: review_context
      - include_bot_feedback (bool): include bot-authored feedback (the prior
        review). When absent, falls back to ``rebase_include_bot_feedback``
        (default True) so existing behavior is preserved.
      - prior_review_max_chars (int): cap for the prior-review slot, head-kept.
        Default: 10000.

    Returns:
        Dict with keys: include_bot_feedback (bool), prior_review_max_chars (int >= 0).
    """
    config = _load_config()
    ctx = config.get("review_context", {}) or {}
    if not isinstance(ctx, dict):
        ctx = {}

    if "include_bot_feedback" in ctx:
        include_bot_feedback = _safe_bool(ctx.get("include_bot_feedback"), True)
    else:
        include_bot_feedback = get_rebase_include_bot_feedback()

    max_chars = _safe_int(ctx.get("prior_review_max_chars"), 10000)
    return {
        "include_bot_feedback": include_bot_feedback,
        "prior_review_max_chars": max(0, max_chars),
    }


def get_review_calibration_config() -> dict:
    """Get review calibration configuration from config.yaml.

    Config key: review_calibration
      - batch_size (int, >=1): Minimum new outcome entries before
        triggering a calibration pass. Default: 10.
      - stale_days (int, >=1): Days after which unprocessed sidecar
        files are cleaned up. Default: 90.
    """
    config = _load_config()
    cal_cfg = config.get("review_calibration", {}) or {}
    if not isinstance(cal_cfg, dict):
        cal_cfg = {}

    batch_size = cal_cfg.get("batch_size", 10)
    try:
        batch_size = int(batch_size)
    except (TypeError, ValueError):
        batch_size = 10
    batch_size = max(1, batch_size)

    stale_days = cal_cfg.get("stale_days", 90)
    try:
        stale_days = int(stale_days)
    except (TypeError, ValueError):
        stale_days = 90
    stale_days = max(1, stale_days)

    return {"batch_size": batch_size, "stale_days": stale_days}


def get_review_triage_config() -> dict:
    """Get review triage configuration from config.yaml.

    Content-aware triage classifies each file in a PR diff as trivial or
    worth reviewing.  Trivial files (lockfiles, whitespace-only changes,
    renames with no content delta, generated code) are filtered before
    the main review prompt, saving tokens on the expensive model call.

    Config key: review_triage::

        review_triage:
          enabled: true
          skip_lockfiles: true
          skip_generated: true
          skip_whitespace_only: true
          skip_renames: true

    Returns:
        Dict with boolean flags.  All keys always present; defaults shown above.
    """
    config = _load_config()
    triage = config.get("review_triage", {}) or {}
    if not isinstance(triage, dict):
        triage = {}

    def _bool(key: str, default: bool) -> bool:
        val = triage.get(key, default)
        return bool(val) if isinstance(val, bool) else default

    return {
        "enabled": _bool("enabled", False),
        "skip_lockfiles": _bool("skip_lockfiles", True),
        "skip_generated": _bool("skip_generated", True),
        "skip_whitespace_only": _bool("skip_whitespace_only", True),
        "skip_renames": _bool("skip_renames", True),
    }


def get_review_bot_triage_config() -> dict:
    """Get review bot comment triage configuration from config.yaml.

    Controls whether /review triages inline comments from code-review bots
    (CodeRabbit, GitHub Copilot Review, Sourcery) and optionally replies.

    Config key: review_bot_triage::

        review_bot_triage:
          enabled: false
          bot_usernames:
            - coderabbitai
            - sourcery-ai

    Returns:
        Dict with keys: enabled (bool), bot_usernames (list of str).
    """
    config = _load_config()
    section = config.get("review_bot_triage", {}) or {}
    if not isinstance(section, dict):
        section = {}

    enabled = section.get("enabled", False)
    if not isinstance(enabled, bool):
        enabled = False

    usernames = section.get("bot_usernames", [])
    if not isinstance(usernames, list):
        usernames = []
    usernames = [str(u) for u in usernames]

    return {"enabled": enabled, "bot_usernames": usernames}


def get_review_issue_context_config() -> dict:
    """Get PR-review issue tracker enrichment configuration from config.yaml.

    When enabled, ``/review`` parses tracker references (Jira keys like
    ``PROJ-123`` or cross-repo GitHub refs like ``owner/repo#123``) out of the
    PR body and injects a short ticket summary into the review prompt. The
    backend is the project's configured ``issue_tracker`` provider in
    ``projects.yaml``; projects without a Jira mapping see no Jira fetches.

    Config key: review_issue_context::

        review_issue_context:
          enabled: true

    Returns:
        Dict with key: enabled (bool). Defaults to enabled — the fetch is
        gated on references actually appearing in the PR body and is
        best-effort, so projects without references see no behavioral change.
    """
    config = _load_config()
    section = config.get("review_issue_context", {}) or {}
    if not isinstance(section, dict):
        section = {}
    enabled = _safe_bool(section.get("enabled"), True)
    return {"enabled": enabled}


def get_review_verdict_config() -> dict:
    """Get review verdict configuration from config.yaml.

    Controls the formal APPROVE / REQUEST_CHANGES verdict submitted via
    the GitHub Pull Request Reviews API.

    Config key: review_verdict::

        review_verdict:
          approved: true
          body_enabled: true
          include_blockers: true

    Returns:
        Dict with keys: approved (bool), body_enabled (bool),
        include_blockers (bool).
    """
    config = _load_config()
    section = config.get("review_verdict", {})
    malformed = not isinstance(section, dict)
    if malformed:
        section = {}

    def _bool(key: str, default: bool) -> bool:
        val = section.get(key, default)
        if isinstance(val, bool):
            return val
        nonlocal malformed
        malformed = True
        return default

    result = {
        "approved": _bool("approved", True),
        "body_enabled": _bool("body_enabled", True),
        "include_blockers": _bool("include_blockers", True),
    }
    if malformed:
        result["approved"] = False
    return result


def get_review_inline_comments_config() -> dict:
    """Get inline-comment posting configuration for /review.

    When enabled, each structured finding is ALSO posted as an inline PR
    comment anchored to its code location, in addition to the single bucketed
    summary comment. Disabled by default (opt-in).

    Config key: review_inline_comments::

        review_inline_comments:
          enabled: false
          max_comments: 25

    Returns:
        Dict with keys: enabled (bool), max_comments (int, >= 0).
    """
    config = _load_config()
    section = config.get("review_inline_comments", {})
    if not isinstance(section, dict):
        section = {}

    enabled = section.get("enabled", False)
    if not isinstance(enabled, bool):
        enabled = False

    max_comments = section.get("max_comments", 25)
    if not isinstance(max_comments, int) or isinstance(max_comments, bool) or max_comments < 0:
        max_comments = 25

    return {"enabled": enabled, "max_comments": max_comments}


def is_caveman_mode() -> bool:
    """Check if caveman output optimization is enabled.

    When enabled, the agent prompt includes instructions to minimize
    output tokens — short sentences, no filler, direct answers only.

    Reads ``optimizations.caveman.enabled`` from ``config.yaml``::

        optimizations:
          caveman:
            enabled: true
            include: [rebase, fix]     # opt these skills in (skills are
                                       # opt-in by default; the agent loop
                                       # is governed by ``enabled`` alone)

    Default: True (the agent loop receives caveman; skills only do so when
    they opt in via SKILL.md ``caveman: true`` or this ``include`` list).
    """
    enabled = _get_caveman_dict().get("enabled", True)
    return bool(enabled) if isinstance(enabled, bool) else True


def _get_caveman_dict() -> dict:
    """Return the ``optimizations.caveman`` mapping (or an empty dict).

    Normalises away every malformed shape — missing parent, non-dict
    optimizations block, scalar caveman value — so callers can treat the
    result as a plain dict.  Misshapen config falls back to defaults.
    """
    config = _load_config()
    optimizations = config.get("optimizations", {})
    if not isinstance(optimizations, dict):
        return {}
    caveman = optimizations.get("caveman", {})
    return caveman if isinstance(caveman, dict) else {}


def get_caveman_include_list() -> set:
    """Return canonical skill names that opt in to caveman via ``config.yaml``.

    Reads ``optimizations.caveman.include``.  Resolves aliases via
    ``app.skill_dispatch._COMMAND_ALIASES`` so callers can match on the
    canonical name regardless of which alias the user wrote.

    Skills are opt-in: if neither this list nor the skill's SKILL.md
    ``caveman: true`` flag mentions a skill, caveman does not fire for it.
    """
    raw = _get_caveman_dict().get("include", []) or []
    if not isinstance(raw, list):
        return set()

    from app.skill_dispatch import _resolve_canonical
    result = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        name = entry.strip().lstrip("/")
        if not name:
            continue
        result.add(_resolve_canonical(name))
    return result


def _get_ponytail_dict() -> dict:
    """Return the ``optimizations.ponytail`` mapping (or an empty dict).

    Normalises away every malformed shape — missing parent, non-dict
    optimizations block, scalar ponytail value — so callers can treat the
    result as a plain dict.  Misshapen config falls back to defaults.
    """
    config = _load_config()
    optimizations = config.get("optimizations", {})
    if not isinstance(optimizations, dict):
        return {}
    ponytail = optimizations.get("ponytail", {})
    if isinstance(ponytail, bool):
        return {"enabled": ponytail}
    return ponytail if isinstance(ponytail, dict) else {}


def is_ponytail_mode() -> bool:
    """Check if ponytail code minimalism optimization is enabled.

    When enabled, the agent prompt includes a six-gate decision ladder
    instructing Claude to minimise generated code quantity.

    Reads ``optimizations.ponytail.enabled`` from ``config.yaml``::

        optimizations:
          ponytail:
            enabled: true

    Default: True.
    """
    enabled = _get_ponytail_dict().get("enabled", True)
    return bool(enabled)


def _get_review_compressor_dict() -> dict:
    """Return the ``optimizations.review_compressor`` mapping (or empty dict).

    Mirrors :func:`_get_caveman_dict` — normalises away missing parents,
    non-dict optimizations blocks, and scalar values.
    """
    config = _load_config()
    optimizations = config.get("optimizations", {})
    if not isinstance(optimizations, dict):
        return {}
    rc = optimizations.get("review_compressor", {})
    return rc if isinstance(rc, dict) else {}


def is_review_compressor_enabled() -> bool:
    """Check if review diff compression optimization is enabled.

    When enabled, large PR diffs are compressed before being sent to Claude
    for review — files are sorted by language priority and fitted within a
    token budget.

    Reads ``optimizations.review_compressor.enabled`` from ``config.yaml``::

        optimizations:
          review_compressor:
            enabled: true

    Default: True.
    """
    enabled = _get_review_compressor_dict().get("enabled", True)
    return bool(enabled) if isinstance(enabled, bool) else True


def _get_rtk_dict() -> dict:
    """Return the ``optimizations.rtk`` mapping (or an empty dict).

    Mirrors :func:`_get_caveman_dict` — normalises away missing parents,
    non-dict optimizations blocks, and scalar rtk values so callers can treat
    the result as a plain dict.
    """
    config = _load_config()
    optimizations = config.get("optimizations", {})
    if not isinstance(optimizations, dict):
        return {}
    rtk = optimizations.get("rtk", {})
    return rtk if isinstance(rtk, dict) else {}


# Canonical accepted values for ``optimizations.rtk.enabled`` and the
# per-project ``rtk:`` knob.  Single source of truth — :mod:`app.config_validator`
# imports these so the doc-time validation and runtime parsing never drift.
RTK_ENABLED_TRUE = frozenset({"true", "yes", "1", "on"})
RTK_ENABLED_FALSE = frozenset({"false", "no", "0", "off"})
RTK_ENABLED_AUTO = frozenset({"auto", ""})
RTK_ENABLED_VALID = RTK_ENABLED_TRUE | RTK_ENABLED_FALSE | RTK_ENABLED_AUTO


def coerce_rtk_enabled(raw: object) -> Optional[bool]:
    """Coerce a config value into ``True`` / ``False`` / ``None`` (= auto).

    Used by both :func:`is_rtk_mode` and
    :func:`app.projects_config.get_project_rtk_enabled` so the global and
    per-project knobs accept exactly the same shapes.

    Returns:
        ``True`` / ``False`` for explicit values, ``None`` to defer to the
        next layer (binary detection for the global knob, global resolution
        for the per-project knob).
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in RTK_ENABLED_TRUE:
            return True
        if value in RTK_ENABLED_FALSE:
            return False
    return None


def _rtk_runtime_override() -> Optional[bool]:
    """Read the runtime override written by ``/rtk on`` / ``/rtk off``.

    Returns ``True`` for any truthy value, ``False`` for any falsy value, or
    ``None`` when no override file is present or its content is unrecognised
    (i.e. defer to ``config.yaml``).  The override lives at
    ``instance/.koan-rtk-override`` so users can flip rtk awareness on the
    fly without editing config files.

    Accepts the same vocabulary as ``optimizations.rtk.enabled`` —
    :func:`coerce_rtk_enabled` is the single source of truth.  ``/rtk on``
    and ``/rtk off`` write ``"on"`` / ``"off"``, but a user who hand-writes
    ``true`` / ``false`` / ``yes`` / ``no`` gets the same behaviour.
    """
    koan_root = os.environ.get("KOAN_ROOT")
    if not koan_root:
        return None
    path = Path(koan_root) / "instance" / ".koan-rtk-override"
    try:
        value = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return coerce_rtk_enabled(value)


def is_rtk_mode() -> bool:
    """Check whether the rtk awareness section should be injected.

    Resolution order (highest priority first):

    1.  ``instance/.koan-rtk-override`` (written by ``/rtk on`` / ``/rtk off``).
    2.  ``optimizations.rtk.enabled`` in ``config.yaml``::

            optimizations:
              rtk:
                enabled: auto    # auto | true | false

        - ``auto`` (default): on iff the rtk binary is detected on the host.
          When the tool is installed the user almost certainly wants Claude
          to prefer it; when it's missing, the awareness blurb would just
          be dead context.
        - ``true``: always on (forces injection even if the binary is
          missing — useful when the user installs rtk after Kōan boots).
        - ``false``: always off.

    The detection probe is cached per-process by :mod:`app.rtk_detector`, so
    this function is safe to call from per-prompt code paths.
    """
    override = _rtk_runtime_override()
    if override is not None:
        return override
    explicit = coerce_rtk_enabled(_get_rtk_dict().get("enabled", "auto"))
    if explicit is not None:
        return explicit
    # "auto" (and any unrecognised value) → defer to binary detection.
    try:
        from app.rtk_detector import detect_rtk
        return detect_rtk().installed
    except Exception as e:
        print(f"[config] rtk detection failed: {e}", file=sys.stderr)
        return False


def is_rtk_awareness_enabled() -> bool:
    """Return ``True`` when the awareness section should ship in prompts.

    Two-stage gate: ``optimizations.rtk.enabled`` controls overall rtk
    integration; ``optimizations.rtk.awareness`` toggles the prompt-injection
    layer specifically.  Default: ``True`` — if rtk mode is on at all,
    awareness is part of it unless explicitly disabled.
    """
    if not is_rtk_mode():
        return False
    raw = _get_rtk_dict().get("awareness", True)
    return bool(raw) if isinstance(raw, bool) else True
