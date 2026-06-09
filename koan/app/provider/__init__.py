"""
CLI provider abstraction for Kōan.

Allows switching between Claude Code CLI, GitHub Copilot CLI,
OpenAI Codex CLI, Cline CLI, or a local LLM server as the underlying AI agent
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
    provider/local.py        — LocalLLMProvider implementation
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
from app.provider.local import LocalLLMProvider  # noqa: F401
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
    "local": LocalLLMProvider,
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


def get_cli_binary() -> str:
    """Get the CLI binary command for the configured provider.

    For shell scripts: returns the full command prefix needed to invoke
    the provider (e.g., "claude" or "copilot" or "gh copilot").
    """
    return get_provider().shell_command()


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

    Automatically reads ``skip_permissions`` from config.yaml so all
    callers get the flag without needing changes.
    """
    from app.config import get_skip_permissions

    return get_provider().build_command(
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


def _write_system_prompt_file(content: str) -> str:
    """Write a system prompt to a 0600 temp file and return its absolute path.

    The file is intentionally not auto-deleted — the caller is responsible
    for unlinking it after the subprocess has finished consuming it. Use
    :func:`build_full_command_managed`, which pairs this with cleanup.
    """
    # NamedTemporaryFile creates with 0600 on POSIX (same as mkstemp).
    # delete=False so the subprocess can open the path after we close it.
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix="koan-sysprompt-",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
        ) as f:
            path = f.name
            f.write(content)
    except Exception:
        # If NamedTemporaryFile raised after creating the file, unlink it.
        with contextlib.suppress(OSError, NameError):
            os.unlink(path)  # type: ignore[possibly-undefined]
        raise
    return path


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
    )
    if system_prompt and get_provider().supports_system_prompt_file():
        path = _write_system_prompt_file(system_prompt)
        cleanup_paths.append(path)
        kwargs.update(system_prompt="", system_prompt_file=path)
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
) -> str:
    """Build and run a CLI command, returning stripped stdout.

    Higher-level helper for runner modules that need to invoke the
    configured CLI provider with a prompt and get back text output.
    Combines build_full_command + subprocess execution + error handling.

    When the CLI hits its max-turns limit, the partial output is returned
    instead of raising — the caller can still extract useful results from
    an incomplete session.

    Raises:
        RuntimeError: If the command exits with non-zero code (except
            max-turns, which returns partial output).
    """
    from app.config import get_model_config

    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=allowed_tools,
        model=models.get(model_key, ""),
        fallback=models.get("fallback", ""),
        max_turns=max_turns,
    )

    from app.cli_exec import run_cli_with_retry

    result = run_cli_with_retry(
        cmd,
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


def _persist_stream_usage_snapshot(snapshot: Optional[Dict[str, Any]]) -> None:
    """Persist usage snapshot for skill-dispatch post-mission accounting."""
    if not snapshot:
        return
    target = os.environ.get("KOAN_STREAM_USAGE_FILE", "").strip()
    if not target:
        return
    try:
        Path(target).write_text(json.dumps(snapshot, separators=(",", ":")))
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
    from app.config import get_model_config

    models = get_model_config()
    provider = get_provider()
    use_stream_json = provider.supports_stream_json()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=allowed_tools,
        model=model or models.get(model_key, ""),
        fallback=models.get("fallback", ""),
        max_turns=max_turns,
        output_format="stream-json" if use_stream_json else "",
    )
    last_message_path: Optional[str] = None
    if provider.supports_last_message_file():
        fd, last_message_path = tempfile.mkstemp(
            prefix="koan-last-message-",
            suffix=".txt",
        )
        os.close(fd)
        cmd = provider.add_last_message_file_args(cmd, last_message_path)

    print(f"[cli] Starting {provider.name or 'provider'} CLI session", flush=True)

    from app.cli_exec import popen_cli

    raw_lines: List[str] = []  # for error reporting (full transcript)
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
