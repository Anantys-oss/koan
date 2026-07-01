"""
CLI provider abstraction for Kōan.

Allows switching between Claude Code CLI, GitHub Copilot CLI,
OpenAI Codex CLI, Cline CLI, or Ollama Launch as the underlying AI agent
binary. Each provider knows how to translate Kōan's generic command
spec into provider-specific flags.

Configuration:
    config.yaml:  cli_provider: "claude"   (default)
    env var:      KOAN_CLI_PROVIDER=codex  (overrides config.yaml)

Package structure:
    provider/base.py         — CLIProvider base class + tool constants
    provider/claude.py       — ClaudeProvider implementation
    provider/cline.py        — ClineProvider implementation
    provider/codex.py        — CodexProvider implementation
    provider/copilot.py      — CopilotProvider implementation
    provider/ollama_launch.py — OllamaLaunchProvider (ollama launch claude)
    provider/__init__.py     — Registry, resolution, convenience functions
"""

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Re-export base class and constants for convenience
from app.provider.base import (  # noqa: F401
    CLIProvider,
    CLAUDE_TOOLS,
    PROVIDER_ERROR_EVENT_TYPES,
    TOOL_NAME_MAP,
)

# Import concrete providers
from app.provider.claude import ClaudeProvider  # noqa: F401
from app.provider.cline import ClineProvider  # noqa: F401
from app.provider.codex import CodexProvider  # noqa: F401
from app.provider.copilot import CopilotProvider  # noqa: F401
from app.provider.ollama_launch import OllamaLaunchProvider  # noqa: F401


def _extract_provider_error_preview(stdout: str) -> str:
    """Return the most useful direct provider error from JSONL stdout."""
    previews: List[str] = []
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = str(event.get("type") or "")
        if etype not in PROVIDER_ERROR_EVENT_TYPES:
            continue
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            previews.append(message.strip())
            continue
        error = event.get("error")
        if isinstance(error, dict):
            err_message = error.get("message")
            if isinstance(err_message, str) and err_message.strip():
                previews.append(err_message.strip())
    return previews[-1] if previews else ""


def _format_cli_error(returncode: int, stdout: str, stderr: str) -> str:
    """Build a diagnostic message for non-zero CLI exits.

    Includes exit code, stderr (truncated), and stdout (truncated) when
    stderr is empty — Claude CLI sometimes prints fatal errors to stdout.
    """
    parts = [f"exit={returncode}"]
    err = (stderr or "").strip()
    out = (stdout or "").strip()
    if err:
        parts.append(f"stderr={err[:300]}")
    if out and not err:
        preview = _extract_provider_error_preview(out) or out
        parts.append(f"stdout={preview[:300]}")
    return "CLI invocation failed: " + " | ".join(parts)


# ---------------------------------------------------------------------------
# Provider registry & resolution
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "claude": ClaudeProvider,
    "cline": ClineProvider,
    "codex": CodexProvider,
    "copilot": CopilotProvider,
    "ollama-launch": OllamaLaunchProvider,
}

# Cached provider instance (reset with reset_provider() in tests)
_cached_provider: Optional[CLIProvider] = None
_cached_provider_name: str = ""


def reset_provider():
    """Reset the cached provider (for testing)."""
    global _cached_provider, _cached_provider_name
    _cached_provider = None
    _cached_provider_name = ""


def is_known_provider(name: str) -> bool:
    """True if *name* is a registered provider flavor (claude, codex, ...)."""
    return str(name or "").strip().lower() in _PROVIDERS


def get_provider_name() -> str:
    """Determine which CLI provider to use.

    Resolution order:
    1. KOAN_CLI_PROVIDER env var (with CLI_PROVIDER fallback; highest priority)
    2. config.yaml cli_provider key
    3. Default: "claude"
    """
    # Lazy import to avoid circular dependency
    from app.utils import get_cli_provider_env, load_config

    env_val = get_cli_provider_env()
    if env_val and env_val in _PROVIDERS:
        return env_val

    try:
        config = load_config()
        config_val = str(config.get("cli_provider", "")).strip().lower()
        if config_val and config_val in _PROVIDERS:
            return config_val
    except Exception as e:
        print(f"[provider] Config loading failed: {e}", file=sys.stderr)

    return "claude"


def get_provider() -> CLIProvider:
    """Get the configured CLI provider instance (cached singleton)."""
    global _cached_provider, _cached_provider_name
    name = get_provider_name()
    if _cached_provider is None or name != _cached_provider_name:
        _cached_provider = _PROVIDERS[name]()
        _cached_provider_name = name
    return _cached_provider


def get_provider_by_name(name: str) -> CLIProvider:
    """Return a fresh provider instance by name.

    Used by provider-aware code paths that need to classify historical output
    with the provider that produced it, without mutating the configured cached
    provider for the current process.
    """
    provider_name = str(name or "").strip().lower()
    if provider_name not in _PROVIDERS:
        raise KeyError(f"Unknown CLI provider: {name}")
    return _PROVIDERS[provider_name]()


def get_provider_for_role(role: str, project_name: str = "") -> CLIProvider:
    """Return the provider instance for a mission role (the ``cli:`` section).

    When the role resolves to the global provider with no custom binary path
    (the parity case — including when no ``cli:`` section is configured), the
    GLOBAL cached singleton is returned, so role-less behavior is byte-for-byte
    unchanged. When the role names a different flavor and/or a ``flavor:path``,
    a FRESH instance of that flavor is constructed carrying the path as a
    per-instance binary override.

    Role-bearing instances are NEVER written to ``_cached_provider`` — the
    global singleton's identity must not be poisoned by a path-bearing instance.
    """
    from app.config import get_cli_config

    try:
        flavor, path = get_cli_config(project_name).get(role, ("", ""))
    except Exception as e:  # never let config resolution break execution
        print(f"[provider] cli role resolution failed for {role!r}: {e}", file=sys.stderr)
        return get_provider()

    if not flavor or flavor not in _PROVIDERS:
        return get_provider()
    # Same flavor as the global default and no custom path → reuse the singleton.
    if flavor == get_provider_name() and not path:
        return get_provider()
    return _PROVIDERS[flavor](binary_path=path)


def get_fallback_provider(project_name: str = "") -> Optional[CLIProvider]:
    """Return the configured ``cli.fallback`` provider instance, or ``None``.

    Used only for launch/auth-failure recovery (see ``mission_executor`` and the
    ``run_command*`` helpers). Returns ``None`` when no fallback is configured.
    Like :func:`get_provider_for_role`, never writes the global cache.
    """
    from app.config import get_cli_fallback

    try:
        flavor, path = get_cli_fallback(project_name)
    except Exception as e:
        print(f"[provider] cli fallback resolution failed: {e}", file=sys.stderr)
        return None
    if not flavor or flavor not in _PROVIDERS:
        return None
    return _PROVIDERS[flavor](binary_path=path)


def resolve_role_provider(model_key: str, project_name: str = "") -> CLIProvider:
    """Provider for a role, pre-flight-swapped to ``cli.fallback`` if unavailable.

    Used by the stateless ``run_command*`` helpers: when the role's CLI binary
    is not installed/resolvable (the dominant "cli not working" case — e.g. a
    wrong ``flavor:path``), and a different, available ``cli.fallback`` is
    configured, return the fallback up front. When no fallback applies, returns
    the role provider unchanged so the call fails normally. (The stateful
    mission path additionally recovers from auth failures post-run via
    ``mission_executor._maybe_fallback_provider_rerun``.)
    """
    provider = get_provider_for_role(model_key, project_name)
    if provider.is_available():
        return provider
    fb = get_fallback_provider(project_name)
    if fb is not None and fb.binary() != provider.binary() and fb.is_available():
        print(
            f"[provider] role {model_key!r} CLI {provider.name!r} "
            f"({provider.binary()}) unavailable — using fallback {fb.name!r}",
            file=sys.stderr,
        )
        return fb
    return provider


def _resolve_role_provider_and_models(model_key: str, project_name: str):
    """Resolve a role's provider (with launch-fallback) plus its model dict.

    Shared by the stateless ``run_command`` / ``run_command_streaming`` helpers:
    returns ``(provider, models)`` where ``models`` is resolved against that
    provider's section for ``model_key`` and the fallback model.
    """
    from app.config import get_model_config

    provider = resolve_role_provider(model_key, project_name)
    models = get_model_config(
        project_name,
        role_providers={model_key: provider.name, "fallback": provider.name},
    )
    return provider, models


def get_cli_binary() -> str:
    """Get the CLI binary command for the configured provider.

    For shell scripts: returns the full command prefix needed to invoke
    the provider (e.g., "claude" or "copilot" or "gh copilot").
    """
    return get_provider().shell_command()


def get_cli_binary_name() -> str:
    """Return the binary basename from ``KOAN_CLAUDE_CLI_PATH``, or '' if unset.

    The Claude provider honors ``KOAN_CLAUDE_CLI_PATH`` to point at an
    alternate CLI binary (e.g. an ollama-wrapping shim). Surfacing its
    basename lets banners and ``/status`` advertise which flavor is in use.
    """
    path = os.environ.get("KOAN_CLAUDE_CLI_PATH", "").strip()
    return path.rstrip("/").rsplit("/", 1)[-1] if path else ""


def get_provider_display(name: str = "") -> str:
    """Provider name for display, with the custom CLI binary flavor appended.

    Returns ``"<name>"`` or ``"<name> (<binary>)"`` when ``KOAN_CLAUDE_CLI_PATH``
    points at a binary whose basename differs from the provider name (e.g.
    ``claude (ollama-claude)``). Suppressed when unset or identical, so this is
    a no-op for non-Claude providers. When *name* is empty the configured
    provider is resolved via :func:`get_provider_name`. Single source of truth
    for the global provider line shown by the startup banner and ``/status``.

    Per-role provider overrides (the ``cli:`` config section) are summarized
    separately by :func:`describe_cli_roles`.
    """
    if not name:
        name = get_provider_name()
    parts: List[str] = []
    binary = get_cli_binary_name()
    if binary and binary != name:
        parts.append(binary)
    if parts:
        return f"{name} ({', '.join(parts)})"
    return name


def describe_cli_roles(project_name: str = "") -> str:
    """Compact summary of per-role provider overrides for ``/status`` and the banner.

    Returns e.g. ``"mission→codex, review_mode→deep-claude, fallback→claude"`` listing
    only roles whose resolved provider/binary differs from the global default, plus
    the ``cli.fallback`` provider if set. Empty string when no ``cli:`` section is
    configured, so it stays a no-op for the default setup.
    """
    try:
        from app.config import get_cli_config, get_cli_fallback

        resolved = get_cli_config(project_name)
    except Exception:
        return ""

    def _label(flavor: str, path: str) -> str:
        if not path:
            return flavor
        return f"{flavor}({path.rstrip('/').rsplit('/', 1)[-1]})"

    global_name = get_provider_name()
    parts: List[str] = []
    for role in ("mission", "chat", "lightweight", "review_mode", "reflect"):
        flavor, path = resolved.get(role, (global_name, ""))
        if flavor == global_name and not path:
            continue
        parts.append(f"{role}→{_label(flavor, path)}")
    try:
        fb_flavor, fb_path = get_cli_fallback(project_name)
    except Exception:
        fb_flavor, fb_path = "", ""
    if fb_flavor:
        parts.append(f"fallback→{_label(fb_flavor, fb_path)}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

def build_cli_flags(
    model: str = "",
    fallback: str = "",
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build extra CLI flags for the configured provider.

    Drop-in replacement for utils.build_claude_flags() that respects
    the configured CLI provider.
    """
    return get_provider().build_extra_flags(model, fallback, disallowed_tools)


def build_tool_flags(
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
) -> List[str]:
    """Build tool access flags for the configured provider.

    Translates Claude-style tool names (Bash, Read, Write, etc.) into
    provider-specific flags.
    """
    return get_provider().build_tool_args(allowed_tools, disallowed_tools)


def build_prompt_flags(prompt: str) -> List[str]:
    """Build prompt flags for the configured provider.

    Returns ["-p", prompt] for Claude, or ["copilot", "-p", prompt] for gh mode.
    """
    return get_provider().build_prompt_args(prompt)


def build_output_flags(fmt: str = "") -> List[str]:
    """Build output format flags for the configured provider."""
    return get_provider().build_output_args(fmt)


def build_max_turns_flags(max_turns: int = 0) -> List[str]:
    """Build max-turns flags for the configured provider."""
    return get_provider().build_max_turns_args(max_turns)


def build_full_command(
    prompt: str,
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
    model: str = "",
    fallback: str = "",
    output_format: str = "",
    max_turns: int = 0,
    mcp_configs: Optional[List[str]] = None,
    plugin_dirs: Optional[List[str]] = None,
    system_prompt: str = "",
    system_prompt_file: str = "",
    effort: str = "",
    resume_session_id: str = "",
    provider: Optional[CLIProvider] = None,
) -> List[str]:
    """Build a complete CLI command for the configured provider.

    This is the high-level API: pass generic parameters, get back a
    provider-specific command list ready for subprocess.run().

    Args:
        system_prompt: Optional system prompt text. When the provider
            supports it (e.g., Claude ``--append-system-prompt``), sent
            as a dedicated system prompt for better prompt caching.
            Otherwise prepended to the user prompt transparently.
        effort: Reasoning effort level (e.g. "low", "medium", "high", "max").
            Empty string means no override.
        resume_session_id: When set and the provider supports session
            resumption, continues the given session instead of starting
            fresh.
        provider: Explicit provider instance to build for. ``None`` (default)
            uses the global :func:`get_provider`. Pass a per-role instance
            (from :func:`get_provider_for_role`) to build a command for a
            specific mission role's CLI / custom binary.

    Automatically reads ``skip_permissions`` from config.yaml so all
    callers get the flag without needing changes.
    """
    from app.config import get_skip_permissions

    return (provider or get_provider()).build_command(
        prompt=prompt,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        model=model,
        fallback=fallback,
        output_format=output_format,
        max_turns=max_turns,
        mcp_configs=mcp_configs,
        plugin_dirs=plugin_dirs,
        skip_permissions=get_skip_permissions(),
        system_prompt=system_prompt,
        system_prompt_file=system_prompt_file,
        effort=effort,
        resume_session_id=resume_session_id,
    )


def _write_system_prompt_file(
    content: str,
    host_dir: Optional[str] = None,
    container_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Write a system prompt to a 0600 temp file and return ``(host_path, cmd_path)``.

    ``host_path`` is always the real filesystem path used for cleanup.
    ``cmd_path`` is the path embedded in the CLI command — equal to
    ``host_path`` normally, or ``container_dir/<filename>`` in devcontainer
    mode so the container can open the bind-mounted file.

    The file is intentionally not auto-deleted — the caller is responsible
    for unlinking ``host_path`` after the subprocess has finished. Use
    :func:`build_full_command_managed`, which pairs this with cleanup.

    Args:
        host_dir: Directory on the host where the file is written. In
            devcontainer mode, pass the host side of the koan-tmp bind-mount.
        container_dir: When set, ``cmd_path`` is ``container_dir/<filename>``
            so the CLI command embeds the container-accessible path.
    """
    from app.utils import koan_tmp_dir

    # NamedTemporaryFile creates with 0600 on POSIX (same as mkstemp).
    # delete=False so the subprocess can open the path after we close it.
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix="koan-sysprompt-",
            suffix=".txt",
            delete=False,
            dir=host_dir or koan_tmp_dir(),
            encoding="utf-8",
        ) as f:
            host_path = f.name
            f.write(content)
    except Exception:
        # If NamedTemporaryFile raised after creating the file, unlink it.
        with contextlib.suppress(OSError, NameError):
            os.unlink(host_path)  # type: ignore[possibly-undefined]
        raise
    cmd_path = str(Path(container_dir) / Path(host_path).name) if container_dir else host_path
    return host_path, cmd_path


def build_full_command_managed(
    prompt: str,
    allowed_tools: Optional[List[str]] = None,
    disallowed_tools: Optional[List[str]] = None,
    model: str = "",
    fallback: str = "",
    output_format: str = "",
    max_turns: int = 0,
    mcp_configs: Optional[List[str]] = None,
    plugin_dirs: Optional[List[str]] = None,
    system_prompt: str = "",
    effort: str = "",
    resume_session_id: str = "",
    system_prompt_dir: Optional[str] = None,
    system_prompt_container_dir: Optional[str] = None,
    provider: Optional[CLIProvider] = None,
) -> Tuple[List[str], List[str]]:
    """Build a CLI command, routing large system prompts through a temp file.

    Same parameters as :func:`build_full_command`, but when ``system_prompt``
    is non-empty AND the configured provider supports
    ``--append-system-prompt-file`` (or its equivalent), the prompt is
    written to a 0600 temp file and the file path is passed instead of the
    content.  This keeps the prompt out of ``argv`` so it doesn't show up
    in ``ps`` listings or process supervisors.

    Returns:
        ``(cmd, cleanup_paths)`` — the caller MUST unlink each path in
        ``cleanup_paths`` after the subprocess exits, typically from a
        ``finally`` block alongside its other temp-file cleanup.
    """
    cleanup_paths: List[str] = []

    kwargs = dict(
        prompt=prompt,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        model=model,
        fallback=fallback,
        output_format=output_format,
        max_turns=max_turns,
        mcp_configs=mcp_configs,
        plugin_dirs=plugin_dirs,
        effort=effort,
        resume_session_id=resume_session_id,
        provider=provider,
    )
    if system_prompt and (provider or get_provider()).supports_system_prompt_file():
        host_path, cmd_path = _write_system_prompt_file(
            system_prompt,
            host_dir=system_prompt_dir,
            container_dir=system_prompt_container_dir,
        )
        cleanup_paths.append(host_path)
        kwargs.update(system_prompt="", system_prompt_file=cmd_path)
    else:
        kwargs["system_prompt"] = system_prompt
    return build_full_command(**kwargs), cleanup_paths


def cleanup_managed_paths(paths: List[str]) -> None:
    """Unlink each path in *paths*, ignoring missing files.

    Companion to :func:`build_full_command_managed`. Safe to call from
    a ``finally`` block; never raises.
    """
    for p in paths:
        with contextlib.suppress(OSError):
            os.unlink(p)


_MAX_TURNS_RE = re.compile(r"Reached max turns", re.IGNORECASE)


def _is_max_turns_error(stdout: str) -> bool:
    """Return True if the CLI output indicates a max-turns limit was hit."""
    return bool(_MAX_TURNS_RE.search(stdout))


def _warn_max_turns(max_turns: int, config_key: Optional[str] = "skill_max_turns") -> None:
    """Print a user-visible warning about max turns being hit.

    ``config_key`` names the ``instance/config.yaml`` setting that controls
    this call site's max_turns, when one exists. Pass ``None`` for callers
    that hardcode max_turns (chat replies, intent classification, spec
    review subagents) so the user is not pointed at an unrelated config key.
    """
    hint = (
        f"   To increase: set {config_key} in instance/config.yaml "
        f"(current: {max_turns}).\n"
        if config_key
        else "   This call uses a hardcoded limit and is not configurable.\n"
    )
    print(
        f"\n⚠️  Claude hit the max turns limit ({max_turns}). "
        f"The output may be incomplete.\n{hint}",
        file=sys.stderr,
        flush=True,
    )


def run_command(
    prompt: str,
    project_path: str,
    allowed_tools: List[str],
    model_key: str = "chat",
    max_turns: int = 10,
    timeout: int = 300,
    max_turns_source: Optional[str] = "skill_max_turns",
    project_name: str = "",
) -> str:
    """Build and run a CLI command, returning stripped stdout.

    Higher-level helper for runner modules that need to invoke the
    configured CLI provider with a prompt and get back text output.
    Combines build_full_command + subprocess execution + error handling.

    The provider is resolved per role: ``model_key`` selects both the model and
    the CLI provider (the ``cli:`` section). Pass ``project_name`` to honor
    per-project ``cli:`` overrides; omitting it uses the section/global
    resolution (which matches the historical behavior for these helpers).

    When the CLI hits its max-turns limit, the partial output is returned
    instead of raising — the caller can still extract useful results from
    an incomplete session.

    Raises:
        RuntimeError: If the command exits with non-zero code (except
            max-turns, which returns partial output).
    """
    provider, models = _resolve_role_provider_and_models(model_key, project_name)
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=allowed_tools,
        model=models.get(model_key, ""),
        fallback=models.get("fallback", ""),
        max_turns=max_turns,
        provider=provider,
    )

    from app.cli_exec import run_cli_with_retry

    result = run_cli_with_retry(
        cmd,
        provider=provider,
        capture_output=True, text=True, timeout=timeout,
        cwd=project_path,
    )

    if result.returncode != 0:
        # Max-turns is a graceful limit, not a hard error — return
        # whatever Claude produced so callers can extract partial results.
        if _is_max_turns_error(result.stdout or ""):
            _warn_max_turns(max_turns, max_turns_source)
            from app.claude_step import strip_cli_noise
            return strip_cli_noise(result.stdout.strip())
        raise RuntimeError(
            _format_cli_error(result.returncode, result.stdout, result.stderr)
        )

    from app.claude_step import strip_cli_noise
    return strip_cli_noise(result.stdout.strip())


def _content_text(content: Any) -> str:
    """Extract text from common provider content shapes."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, (list, dict)):
                    nested = _content_text(text)
                    if nested:
                        parts.append(nested)
        return "\n".join(parts)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text
    return ""


def _summarize_stream_event(event: Dict[str, Any]) -> str:
    """Render a provider JSONL event as a single human-readable line.

    Returned strings are short and self-contained so the skill-runner's
    parent (run.py liveness watchdog) sees per-event activity instead of
    raw JSON. Unknown event shapes fall back to a generic type tag.
    """
    etype = event.get("type", "")

    if etype == "system":
        subtype = event.get("subtype", "")
        model = event.get("model", "")
        if subtype == "init" and model:
            return f"[cli] session init (model={model})"
        return f"[cli] system: {subtype or '?'}"

    if etype == "assistant":
        msg = event.get("message") or {}
        blocks = msg.get("content") or []
        parts: List[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                parts.append(f"tool_use: {block.get('name', '?')}")
            elif btype == "text":
                text = (block.get("text") or "").strip()
                if text:
                    preview = text.splitlines()[0][:80]
                    parts.append(f"text: {preview}")
                else:
                    parts.append("text")
            elif btype == "thinking":
                parts.append("thinking")
        return "[cli] assistant — " + (", ".join(parts) if parts else "(empty)")

    if etype == "user":
        msg = event.get("message") or {}
        blocks = msg.get("content") or []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = str(block.get("tool_use_id") or "")[:12]
                err = " (error)" if block.get("is_error") else ""
                return f"[cli] tool_result {tid}{err}"
        return "[cli] user turn"

    if etype == "result":
        subtype = event.get("subtype", "")
        duration_ms = event.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            return f"[cli] result: {subtype or '?'} ({int(duration_ms) // 1000}s)"
        return f"[cli] result: {subtype or '?'}"

    if etype == "rate_limit_event":
        # The new CLI emits these informationally (status "allowed") on every
        # session, plus on genuine exhaustion (status "rejected"). Only the
        # latter must pause Koan. Collapse to a status-aware summary line so the
        # quota detector — which sees only this summary, not the raw JSON — can
        # tell them apart. See quota_handler._rate_limit_exhausted.
        info = event.get("rate_limit_info") or {}
        status = str(info.get("status", "")).strip().lower()
        rtype = str(info.get("rateLimitType") or "").strip()
        label = f" ({rtype})" if rtype else ""
        if status in {"rejected", "exceeded", "blocked", "throttled"}:
            resets = info.get("resetsAt")
            suffix = f" resetsAt {resets}" if resets else ""
            return f"[cli] rate_limit_rejected{label}{suffix}"
        # NOTE: underscored ``rate_limit_ok`` (not "rate limit ok") — the
        # space-separated form collides with the loose ``rate limit`` quota
        # pattern, so a summary that leaks into a stderr-trusted buffer would
        # falsely pause Koan. Mirror the underscored ``rate_limit_rejected``
        # marker above. See quota_handler._rate_limit_exhausted.
        return f"[cli] rate_limit_ok: {status or 'unknown'}{label}"

    item = event.get("item")
    if isinstance(item, dict):
        item_type = item.get("type", "")
        status = event.get("status") or item.get("status") or ""
        if item_type == "message" or item.get("role") == "assistant":
            text = _content_text(item.get("content")).strip()
            if text:
                return f"[cli] assistant — text: {text.splitlines()[0][:80]}"
            return "[cli] assistant — message"
        if item_type:
            suffix = f" ({status})" if status else ""
            return f"[cli] {item_type}{suffix}"

    message = event.get("message")
    if isinstance(message, str) and message.strip():
        return f"[cli] {etype or 'message'}: {message.strip().splitlines()[0][:80]}"

    delta = event.get("delta")
    if isinstance(delta, str) and delta.strip():
        return f"[cli] {etype or 'delta'}: {delta.strip().splitlines()[0][:80]}"

    last_agent_message = event.get("last_agent_message")
    if isinstance(last_agent_message, str) and last_agent_message.strip():
        return f"[cli] {etype or 'result'}: {last_agent_message.strip().splitlines()[0][:80]}"

    for key in ("name", "status", "subtype"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return f"[cli] {etype or 'event'}: {value}"

    return f"[cli] event: {etype or '?'}"


def _extract_assistant_text_chunks(event: Dict[str, Any]) -> List[str]:
    """Pull raw assistant text out of common provider event shapes.

    Used as a partial-stream fallback: if the CLI dies before emitting a
    final ``result`` event, accumulated text chunks still surface to the
    caller instead of an empty string.
    """
    chunks: List[str] = []
    if event.get("type") == "assistant":
        msg = event.get("message") or {}
        blocks = msg.get("content") or []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)

    item = event.get("item")
    if isinstance(item, dict) and (
        item.get("role") == "assistant" or item.get("type") == "message"
    ):
        text = _content_text(item.get("content"))
        if text:
            chunks.append(text)

    message = event.get("message")
    if isinstance(message, str) and event.get("type") in {
        "agent_message",
        "agent_message_content_delta",
        "assistant_message",
        "message",
    }:
        chunks.append(message)

    for key in ("output_text", "text", "delta"):
        text = event.get(key)
        if isinstance(text, str) and text and event.get("type") in {
            "agent_message",
            "agent_message_content_delta",
            "assistant_message",
            "message",
            "response.output_text.delta",
            "response.output_text.done",
        }:
            chunks.append(text)

    return chunks


def _extract_result_text(event: Dict[str, Any]) -> Optional[str]:
    """Pull the final assistant text out of a provider result event.

    Returns ``None`` when *event* is not a result event, when its
    ``result`` field is missing or not a string, or when it is an empty
    string — in any of these cases the caller falls back to accumulated
    assistant text blocks instead of pinning the return value to ``""``.
    The Claude CLI stuffs the same string a plain text-mode run would
    have printed into ``event["result"]``; we forward it verbatim so
    callers see the same return value they did before stream-json was on.
    """
    etype = str(event.get("type") or "")
    if etype != "result":
        if not (
            etype.endswith(".completed")
            or etype.endswith(".done")
            or etype in {
                "turn.completed",
                "response.completed",
                "task.completed",
                "turn_complete",
                "task_complete",
            }
        ):
            return None
        for key in ("output_text", "last_agent_message"):
            result = event.get(key)
            if isinstance(result, str) and result:
                return result
        return None
    for key in ("result", "output_text", "last_agent_message"):
        result = event.get(key)
        if isinstance(result, str) and result:
            return result
    return None


# Known stream-json ``result.subtype`` values that mean "max turns hit".
# Update when the Claude CLI ships new subtypes; the legacy regex
# fallback in ``_is_max_turns_error`` covers textual output.
_STREAM_JSON_MAX_TURNS_SUBTYPES = frozenset({
    "error_max_turns",
    "max_turns",
})


def _is_stream_json_max_turns(event: Dict[str, Any]) -> bool:
    """Detect the stream-json equivalent of the legacy 'Reached max turns' line."""
    if event.get("type") != "result":
        return False
    subtype = str(event.get("subtype", "") or "").lower()
    return subtype in _STREAM_JSON_MAX_TURNS_SUBTYPES


def _usage_snapshot_from_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract token usage snapshot from a stream event when present."""
    if not isinstance(event, dict):
        return None

    usage = event.get("usage")
    if isinstance(usage, dict):
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_input = int(usage.get("cached_input_tokens", 0) or 0)
        if cached_input > 0:
            input_tokens = max(0, input_tokens - cached_input)
        if input_tokens or output_tokens or cached_input:
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cached_input,
                "cache_creation_input_tokens": 0,
                "model": str(event.get("model") or "unknown"),
            }

    payload = event.get("payload")
    if (
        isinstance(payload, dict)
        and event.get("type") == "event_msg"
        and payload.get("type") == "token_count"
    ):
        info = payload.get("info")
        if isinstance(info, dict):
            total = info.get("total_token_usage")
            if isinstance(total, dict):
                input_tokens = int(total.get("input_tokens", 0) or 0)
                output_tokens = int(total.get("output_tokens", 0) or 0)
                cached_input = int(total.get("cached_input_tokens", 0) or 0)
                if cached_input > 0:
                    input_tokens = max(0, input_tokens - cached_input)
                if input_tokens or output_tokens or cached_input:
                    return {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_input_tokens": cached_input,
                        "cache_creation_input_tokens": 0,
                        "model": str(info.get("model") or event.get("model") or "unknown"),
                    }

    return None


_STREAM_USAGE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _persist_stream_usage_snapshot(snapshot: Optional[Dict[str, Any]]) -> None:
    """Accumulate a usage snapshot for skill-dispatch post-mission accounting.

    A single skill subprocess may make several provider calls (e.g. the main
    work plus a backend review/fix gate). The sidecar must hold the SUM of all
    of them so the mission's post-mission accounting reflects real consumption
    — overwriting would attribute only the last call (typically a small gate
    review) and silently drop the rest.
    """
    if not snapshot:
        return
    target = os.environ.get("KOAN_STREAM_USAGE_FILE", "").strip()
    if not target:
        return
    try:
        merged = dict(snapshot)
        existing_raw = ""
        try:
            existing_raw = Path(target).read_text().strip()
        except OSError:
            existing_raw = ""
        if existing_raw:
            try:
                prev = json.loads(existing_raw)
            except (json.JSONDecodeError, ValueError):
                prev = None
            if isinstance(prev, dict):
                for key in _STREAM_USAGE_TOKEN_KEYS:
                    merged[key] = (
                        int(prev.get(key, 0) or 0)
                        + int(snapshot.get(key, 0) or 0)
                    )
                if prev.get("model") and not merged.get("model"):
                    merged["model"] = prev["model"]
        Path(target).write_text(json.dumps(merged, separators=(",", ":")))
    except OSError as exc:
        print(f"[provider] WARNING: stream usage sidecar write failed: {exc}", file=sys.stderr)


def run_command_streaming(
    prompt: str,
    project_path: str,
    allowed_tools: List[str],
    model_key: str = "chat",
    model: str = "",
    max_turns: int = 10,
    timeout: int = 300,
    max_turns_source: Optional[str] = "skill_max_turns",
    project_name: str = "",
) -> str:
    """Build and run a CLI command, streaming progress to stdout in real time.

    Some CLIs buffer rendered text until the session ends. For high-effort
    skills that can mean tens of minutes of silent tool use, which the
    skill-runner liveness watchdog in run.py reads as a hang and kills.

    Providers that support JSONL progress events opt in here: Claude uses
    ``--output-format stream-json --verbose`` and Codex uses ``--json``.
    Each event is rendered into a short human-readable line printed to the
    runner's stdout, so the parent watchdog sees real activity and
    ``/live`` shows what the provider is doing. The final assistant text is
    extracted from provider-specific result/message events so callers'
    return-value contract stays unchanged.

    Providers that don't support JSONL progress fall through to the
    original raw text path; lines that fail to parse as JSON are still
    printed and contribute to the return value.

    Raises:
        RuntimeError: If the command exits with non-zero code (except
            max-turns, which returns partial output).
    """
    # Resolve the CLI provider for this role (cli: section), swapping to the
    # cli.fallback up front if the role's binary is unavailable. An explicit
    # `model` arg still wins over the role-derived model.
    provider, models = _resolve_role_provider_and_models(model_key, project_name)
    use_stream_json = provider.supports_stream_json()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=allowed_tools,
        model=model or models.get(model_key, ""),
        fallback=models.get("fallback", ""),
        max_turns=max_turns,
        output_format="stream-json" if use_stream_json else "",
        provider=provider,
    )
    last_message_path: Optional[str] = None
    if provider.supports_last_message_file():
        from app.utils import koan_tmp_dir

        fd, last_message_path = tempfile.mkstemp(
            prefix="koan-last-message-",
            suffix=".txt",
            dir=koan_tmp_dir(),
        )
        os.close(fd)
        cmd = provider.add_last_message_file_args(cmd, last_message_path)

    print(f"[cli] Starting {provider.name or 'provider'} CLI session", flush=True)

    from app.cli_exec import popen_cli

    # raw_lines is scanned only for error/max-turns detection, never returned,
    # so a bounded tail is safe and caps RAM on long provider streams. 2000 is
    # deliberately generous so terminal _format_cli_error context and the
    # non-stream-json max-turns regex fallback keep working.
    raw_lines = deque(maxlen=2000)  # for error reporting (terminal lines)
    # text_lines IS the fallback return value when no result event arrives —
    # it must stay unbounded or long sessions would silently lose output.
    text_lines: List[str] = []  # fallback return value when no result event
    final_result: Optional[str] = None
    usage_snapshot: Optional[Dict[str, Any]] = None
    saw_max_turns_event = False
    stderr_text = ""
    try:
        proc, cleanup = popen_cli(
            cmd,
            provider=provider,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            cwd=project_path,
        )
        # Every print() in this loop is the load-bearing watchdog signal —
        # run.py's skill-runner liveness watchdog (600s) resets on each line
        # emitted to stdout. Do not silence these prints; doing so reintroduces
        # the silent-CLI hang this PR fixes (see PR #1372).
        try:
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                raw_lines.append(stripped)
                if not stripped:
                    continue
                event: Optional[Dict[str, Any]] = None
                if use_stream_json:
                    try:
                        parsed = json.loads(stripped)
                        if isinstance(parsed, dict):
                            event = parsed
                    except (json.JSONDecodeError, ValueError):
                        event = None
                if event is not None:
                    print(_summarize_stream_event(event), flush=True)
                    event_usage = _usage_snapshot_from_event(event)
                    if event_usage is not None:
                        usage_snapshot = event_usage
                    # Accumulate assistant text blocks so a stream that dies
                    # before the final ``result`` event (timeout, watchdog
                    # kill, SIGPIPE) still returns whatever the provider managed
                    # to print, instead of silently returning "".
                    text_lines.extend(_extract_assistant_text_chunks(event))
                    result_text = _extract_result_text(event)
                    if result_text is not None:
                        final_result = result_text
                    if _is_stream_json_max_turns(event):
                        saw_max_turns_event = True
                else:
                    # Non-JSON: provider doesn't speak stream-json or a stray
                    # warning slipped in. Print and remember for the fallback.
                    print(stripped, flush=True)
                    text_lines.append(stripped)
            stderr_text = proc.stderr.read() if proc.stderr else ""
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"CLI invocation timed out after {timeout}s") from e
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
            cleanup()

        raw_stdout = "\n".join(raw_lines)
        # The legacy regex still fires on non-stream-json output (codex,
        # warnings printed before the stream begins) and on stream-json
        # results whose subtype encodes the limit.
        hit_max_turns = saw_max_turns_event or _is_max_turns_error(raw_stdout)
        last_message_text = ""
        if last_message_path:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                last_message_text = Path(last_message_path).read_text()
        if last_message_text.strip():
            return_text = last_message_text
        elif final_result is not None:
            return_text = final_result
        else:
            return_text = "\n".join(text_lines)

        if proc.returncode != 0:
            # Max-turns is a graceful limit — return partial output so callers
            # can extract useful results from an incomplete session.
            if hit_max_turns:
                _warn_max_turns(max_turns, max_turns_source)
                from app.claude_step import strip_cli_noise
                _persist_stream_usage_snapshot(usage_snapshot)
                return strip_cli_noise(return_text.strip())
            raise RuntimeError(
                _format_cli_error(proc.returncode, raw_stdout, stderr_text)
            )

        if hit_max_turns:
            _warn_max_turns(max_turns, max_turns_source)

        from app.claude_step import strip_cli_noise
        _persist_stream_usage_snapshot(usage_snapshot)
        return strip_cli_noise(return_text.strip())
    finally:
        if last_message_path:
            with contextlib.suppress(OSError):
                os.unlink(last_message_path)
