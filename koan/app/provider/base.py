"""Base class and constants for CLI provider abstraction."""

import os
import shutil
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Tool name mapping: Kōan canonical -> provider-specific
# ---------------------------------------------------------------------------

# Claude Code tool names (canonical, used throughout koan codebase)
CLAUDE_TOOLS = {"Bash", "Read", "Write", "Glob", "Grep", "Edit", "Skill"}

# Mapping from Kōan canonical tool names to OpenAI-style function names.
# Used by Copilot provider (--allow-tool) and local LLM runner (function calling).
TOOL_NAME_MAP = {
    "Bash": "shell",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "glob",
    "Grep": "grep",
    "Skill": "skill",
}

# JSONL ``type`` values that signal a provider-level error event in streamed
# CLI output. Shared by error-preview extraction, runtime auth detection, and
# the Codex provider so the recognized set stays in sync across modules.
PROVIDER_ERROR_EVENT_TYPES = {
    "error",
    "turn.failed",
    "response.failed",
    "task.failed",
}


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class CLIProvider:
    """Base class for CLI provider abstraction.

    A provider knows:
    - What binary to invoke
    - How to translate generic flags into provider-specific CLI args
    """

    name: str = ""

    def __init__(self, binary_path: str = ""):
        """Optional per-instance binary path override.

        When set, ``binary()`` resolves and returns this path instead of the
        provider's default. Used by per-role provider selection (the ``cli:``
        config section) to pin a specific CLI binary for one mission role
        without touching the global provider. The path is resolved by
        :meth:`_resolve_binary_path` (absolute as-is, bare name left for PATH
        lookup, relative rooted at ``KOAN_ROOT``).
        """
        self._binary_override = (binary_path or "").strip()

    def _resolve_binary_path(self, raw: str) -> str:
        """Resolve a configured binary path the same way for every provider.

        Absolute path → as-is. Bare command name (no directory component) →
        left for PATH lookup. Relative path → resolved against ``KOAN_ROOT`` so
        config stays portable (the agent runs from ``KOAN_ROOT/koan``, not
        ``KOAN_ROOT``, so a naive relative path would resolve wrong). Single
        source of truth for ``KOAN_CLAUDE_CLI_PATH`` and ``cli: flavor:path``.
        """
        raw = (raw or "").strip()
        if not raw:
            return ""
        if os.path.isabs(raw) or not os.path.dirname(raw):
            return raw
        root = os.environ.get("KOAN_ROOT", "").strip()
        if root:
            return os.path.normpath(os.path.join(root, raw))
        return raw

    def binary(self) -> str:
        """Return the CLI binary name or path."""
        raise NotImplementedError

    def shell_command(self) -> str:
        """Return the full command prefix for shell scripts.

        Defaults to binary(), but providers that need a multi-word command
        (e.g., "gh copilot") should override this.
        """
        return self.binary()

    def is_available(self) -> bool:
        """Check if the binary is installed and accessible."""
        return shutil.which(self.binary()) is not None

    def build_prompt_args(self, prompt: str) -> List[str]:
        """Build args for passing a prompt to the CLI."""
        raise NotImplementedError

    def supports_stdin_prompt_passing(self) -> bool:
        """Return True if Kōan may move the prompt from argv to stdin.

        Providers that need stdin for their own tool calls should return False.
        """
        return True

    def rewrite_prompt_for_stdin(
        self,
        cmd: Sequence[str],
        stdin_marker: str,
    ) -> Tuple[List[str], Optional[str]]:
        """Rewrite *cmd* to read the prompt from stdin.

        Returns ``(rewritten_cmd, prompt_text)``.  ``prompt_text`` is ``None``
        when no rewrite is possible or needed.

        The default supports Claude-style commands where ``-p`` is followed by
        the prompt argument.
        """
        cmd_list = list(cmd)
        try:
            prompt_idx = cmd_list.index("-p") + 1
        except ValueError:
            return cmd_list, None
        if prompt_idx >= len(cmd_list):
            return cmd_list, None
        prompt = cmd_list[prompt_idx]
        if prompt == stdin_marker:
            return cmd_list, None
        rewritten = cmd_list.copy()
        rewritten[prompt_idx] = stdin_marker
        return rewritten, prompt

    def invocation_lock_name(self) -> str:
        """Return a process-wide lock name for serialized CLI invocations.

        Empty string means invocations can run concurrently.
        """
        return ""

    def build_system_prompt_args(self, system_prompt: str) -> List[str]:
        """Build args for passing a system prompt to the CLI.

        Base implementation prepends system prompt to the user prompt by
        returning empty — callers must handle the fallback. Providers that
        support a dedicated system prompt flag should override this.
        """
        return []

    def supports_system_prompt_file(self) -> bool:
        """Return True if the provider accepts a system prompt via file path.

        File-based delivery keeps large prompts out of ``argv`` — they no
        longer appear in ``ps`` listings or process supervisors, and they
        sidestep ``ARG_MAX``.  Providers that opt in must also override
        :meth:`build_system_prompt_file_args`.
        """
        return False

    def build_system_prompt_file_args(self, path: str) -> List[str]:
        """Build args for passing a system prompt via an on-disk file.

        Only consulted when :meth:`supports_system_prompt_file` returns
        True. Base implementation returns empty.
        """
        return []

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        """Build args for tool access control.

        Args:
            allowed_tools: Explicit list of allowed tools (Claude names).
            disallowed_tools: Tools to block (Claude names).
        """
        raise NotImplementedError

    def build_model_args(
        self,
        model: str = "",
        fallback: str = "",
    ) -> List[str]:
        """Build args for model selection."""
        raise NotImplementedError

    def build_output_args(self, fmt: str = "") -> List[str]:
        """Build args for output format (e.g., 'json')."""
        raise NotImplementedError

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        """Build args for conversation turn limit."""
        raise NotImplementedError

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        """Build args for MCP server configuration."""
        raise NotImplementedError

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        """Build args for plugin directory loading.

        Args:
            plugin_dirs: Paths to plugin directories to load.

        Returns:
            CLI flags list. Base implementation returns empty (not supported).
        """
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        """Build args for reasoning effort control.

        Args:
            effort: Effort level (e.g. "low", "medium", "high", "max").
                Empty string means no override (use provider default).

        Returns:
            CLI flags list. Base implementation returns empty (no-op).
        """
        return []

    def supports_session_resume(self) -> bool:
        """Return True if the provider supports resuming a previous session.

        When True, ``build_resume_args`` produces valid CLI flags.
        """
        return False

    def build_resume_args(self, session_id: str) -> List[str]:
        """Build args to resume a previous CLI session.

        Base implementation returns empty (not supported).
        """
        return []

    def supports_stream_json(self) -> bool:
        """Return True if the provider supports ``--output-format stream-json``.

        When True, :func:`run_command_streaming` uses structured JSON events
        for real-time progress and result extraction. When False, it falls
        back to raw text output.
        """
        return False

    def supports_last_message_file(self) -> bool:
        """Return True if the provider can write its final assistant text to a file."""
        return False

    def build_last_message_file_args(self, path: str) -> List[str]:
        """Build args that ask the provider to write its final assistant text."""
        return []

    def add_last_message_file_args(self, cmd: List[str], path: str) -> List[str]:
        """Insert final-message-file args into an already-built command."""
        args = self.build_last_message_file_args(path)
        if not args:
            return cmd
        return [*cmd, *args]

    def build_thinking_args(
        self, enabled: bool = False, budget_tokens: int = 0,
    ) -> List[str]:
        """Build args for extended thinking / reasoning controls.

        When *enabled* is True the provider should emit whatever flags
        activate its extended-thinking mode.  *budget_tokens* is an
        optional soft cap on thinking tokens (ignored by providers that
        do not support token budgets).

        Base implementation returns empty (no-op).
        """
        return []

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        """Build args for permission skipping.

        Base implementation returns empty — only Claude provider supports this.
        """
        return []

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        model: str = "",
        fallback: str = "",
        output_format: str = "",
        max_turns: int = 0,
        mcp_configs: Optional[List[str]] = None,
        plugin_dirs: Optional[List[str]] = None,
        skip_permissions: bool = False,
        system_prompt: str = "",
        system_prompt_file: str = "",
        effort: str = "",
        resume_session_id: str = "",
    ) -> List[str]:
        """Build a complete CLI command from generic parameters.

        Args:
            prompt: User prompt text.
            system_prompt: Optional system prompt text. When provided and the
                provider supports it, sent via a dedicated flag (e.g.,
                ``--append-system-prompt``). Otherwise prepended to *prompt*.
            system_prompt_file: Optional path to a file containing the system
                prompt. When set and the provider supports it (see
                :meth:`supports_system_prompt_file`), takes precedence over
                ``system_prompt`` and is sent via a file-based flag (e.g.,
                ``--append-system-prompt-file``).  Keeps large prompts out
                of argv so they don't leak via ``ps``.  Empty string falls
                back to the in-argv path.
            effort: Reasoning effort level (e.g. "low", "medium", "high", "max").
                Empty string means no override.
            resume_session_id: When set and the provider supports session
                resumption, continues the given session instead of starting
                fresh. Saves tokens by reusing the prior conversation context.

        Returns a list of strings suitable for subprocess.run().
        """
        # File-mode system prompt takes precedence over inline content.
        sys_args: List[str] = []
        if system_prompt_file and self.supports_system_prompt_file():
            sys_args = self.build_system_prompt_file_args(system_prompt_file)
        elif system_prompt:
            sys_args = self.build_system_prompt_args(system_prompt)
            if not sys_args:
                # Provider doesn't support a dedicated flag — prepend to user prompt.
                prompt = system_prompt + "\n\n" + prompt

        cmd = [self.binary()]
        if resume_session_id and self.supports_session_resume():
            cmd.extend(self.build_resume_args(resume_session_id))
        cmd.extend(self.build_permission_args(skip_permissions))
        cmd.extend(sys_args)
        cmd.extend(self.build_prompt_args(prompt))
        cmd.extend(self.build_tool_args(allowed_tools, disallowed_tools))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_max_turns_args(max_turns))
        cmd.extend(self.build_mcp_args(mcp_configs))
        cmd.extend(self.build_plugin_args(plugin_dirs))
        cmd.extend(self.build_effort_args(effort))
        return cmd

    def get_session_data(self, project_path: str) -> Optional[Dict[str, Any]]:
        """Extract post-mission session data (cost, tokens, last action).

        Only providers that produce local session artifacts implement this.
        Base returns ``None`` (no session data available).
        """
        return None

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Probe real API quota with a minimal CLI call.

        Returns (available: bool, error_detail: str).
        Base implementation returns (True, '') — no check needed
        (e.g. local/ollama providers have no quota).
        """
        return True, ""

    def has_api_quota(self) -> bool:
        """Return True when this provider consumes metered API quota.

        When False the usage tracker disables budget gating because
        local/self-hosted providers have no API-enforced token limit.
        """
        return True

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Return True when provider output is a quota/rate-limit failure.

        Providers own this because quota wording and output structure differ:
        Claude emits CLI/provider text, Codex emits JSONL events, Copilot emits
        GitHub-style 429 messages. The base provider has no quota concept.
        """
        return False

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Return True when provider output is an auth/session failure.

        Providers own this because auth wording and output structure differ.
        Generic classifier code handles shared auth text; providers can add
        structured or provider-specific cases without leaking patterns upward.
        """
        return False

    @staticmethod
    def _line_has_error_marker(line: str, markers: tuple) -> bool:
        """Return True when ``line`` contains at least one marker (case-insensitive).

        Used by providers that scan stdout for quota text but want to ignore
        normal assistant prose. A "marker" is a short substring like ``"error"``
        or ``"http"`` that signals the line is a provider/CLI error.
        """
        lowered = line.lower()
        return any(marker in lowered for marker in markers)

    def build_extra_flags(
        self,
        model: str = "",
        fallback: str = "",
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        """Build extra flags (model + tool restrictions) for appending to a command.

        This is the provider-aware replacement for utils.build_claude_flags().
        """
        flags: List[str] = []
        flags.extend(self.build_model_args(model, fallback))
        flags.extend(self.build_tool_args(disallowed_tools=disallowed_tools))
        return flags
