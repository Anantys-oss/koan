"""
CLI provider abstraction for Kōan.

Allows switching between Claude Code CLI, GitHub Copilot CLI,
OpenAI Codex CLI, or a local LLM server as the underlying AI agent
binary. Each provider knows how to translate Kōan's generic command
spec into provider-specific flags.

Configuration:
    config.yaml:  cli_provider: "claude"   (default)
    env var:      KOAN_CLI_PROVIDER=codex  (overrides config.yaml)

Package structure:
    provider/base.py         — CLIProvider base class + tool constants
    provider/claude.py       — ClaudeProvider implementation
    provider/codex.py        — CodexProvider implementation
    provider/copilot.py      — CopilotProvider implementation
    provider/local.py        — LocalLLMProvider implementation
    provider/ollama_launch.py — OllamaLaunchProvider (ollama launch claude)
    provider/__init__.py     — Registry, resolution, convenience functions
"""

import os
import re
import subprocess
import sys
from typing import List, Optional

# Re-export base class and constants for convenience
from app.provider.base import (  # noqa: F401
    CLIProvider,
    CLAUDE_TOOLS,
    TOOL_NAME_MAP,
)

# Import concrete providers
from app.provider.claude import ClaudeProvider  # noqa: F401
from app.provider.codex import CodexProvider  # noqa: F401
from app.provider.copilot import CopilotProvider  # noqa: F401
from app.provider.local import LocalLLMProvider  # noqa: F401
from app.provider.ollama_launch import OllamaLaunchProvider  # noqa: F401


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
        parts.append(f"stdout={out[:300]}")
    return "CLI invocation failed: " + " | ".join(parts)


# ---------------------------------------------------------------------------
# Provider registry & resolution
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "claude": ClaudeProvider,
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
    effort: str = "",
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
        effort=effort,
    )


_MAX_TURNS_RE = re.compile(r"Reached max turns", re.IGNORECASE)


def _is_max_turns_error(stdout: str) -> bool:
    """Return True if the CLI output indicates a max-turns limit was hit."""
    return bool(_MAX_TURNS_RE.search(stdout))


def _warn_max_turns(max_turns: int, config_key: str = "skill_max_turns") -> None:
    """Print a user-visible warning about max turns being hit."""
    print(
        f"\n⚠️  Claude hit the max turns limit ({max_turns}). "
        f"The output may be incomplete.\n"
        f"   To increase: set {config_key} in instance/config.yaml "
        f"(current: {max_turns}).\n",
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
            _warn_max_turns(max_turns)
            from app.claude_step import strip_cli_noise
            return strip_cli_noise(result.stdout.strip())
        raise RuntimeError(
            _format_cli_error(result.returncode, result.stdout, result.stderr)
        )

    from app.claude_step import strip_cli_noise
    return strip_cli_noise(result.stdout.strip())


def run_command_streaming(
    prompt: str,
    project_path: str,
    allowed_tools: List[str],
    model_key: str = "chat",
    max_turns: int = 10,
    timeout: int = 300,
) -> str:
    """Build and run a CLI command, streaming output to stdout in real time.

    Like :func:`run_command`, but uses Popen to tee CLI output to
    ``sys.stdout`` line by line while also capturing the full text.
    This enables the skill dispatch layer in run.py to pipe the output
    into ``pending.md``, making it visible via ``/live``.

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

    from app.cli_exec import popen_cli

    proc, cleanup = popen_cli(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=project_path,
    )

    lines = []
    stderr_text = ""
    try:
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            lines.append(stripped)
            print(stripped, flush=True)
        stderr_text = proc.stderr.read() if proc.stderr else ""
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"CLI invocation timed out after {timeout}s")
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
        cleanup()

    stdout_text = "\n".join(lines)
    if proc.returncode != 0:
        # Max-turns is a graceful limit — return partial output so callers
        # can extract useful results from an incomplete session.
        if _is_max_turns_error(stdout_text):
            _warn_max_turns(max_turns)
            from app.claude_step import strip_cli_noise
            return strip_cli_noise(stdout_text.strip())
        raise RuntimeError(
            _format_cli_error(proc.returncode, stdout_text, stderr_text)
        )

    # Warn on max-turns even when exit code is 0 (edge case: Claude
    # completed its last allowed turn successfully)
    if _is_max_turns_error(stdout_text):
        _warn_max_turns(max_turns)

    from app.claude_step import strip_cli_noise
    return strip_cli_noise(stdout_text.strip())
