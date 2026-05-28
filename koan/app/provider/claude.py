"""Claude Code CLI provider implementation."""

from typing import List, Optional, Tuple

from app.provider.base import CLIProvider


class ClaudeProvider(CLIProvider):
    """Claude Code CLI provider."""

    name = "claude"

    def binary(self) -> str:
        return "claude"

    def supports_session_resume(self) -> bool:
        return True

    def build_resume_args(self, session_id: str) -> List[str]:
        if session_id:
            return ["--resume", session_id]
        return []

    def supports_stream_json(self) -> bool:
        return True

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        if skip_permissions:
            return ["--dangerously-skip-permissions"]
        return []

    def build_system_prompt_args(self, system_prompt: str) -> List[str]:
        if system_prompt:
            return ["--append-system-prompt", system_prompt]
        return []

    def supports_system_prompt_file(self) -> bool:
        # Claude Code CLI supports --append-system-prompt-file in print mode
        # (-p), which is the only mode Kōan uses.  See
        # docs/providers/claude-cli-commands-official.md.
        return True

    def build_system_prompt_file_args(self, path: str) -> List[str]:
        if path:
            return ["--append-system-prompt-file", path]
        return []

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        flags: List[str] = []
        if allowed_tools:
            flags.extend(["--allowedTools", ",".join(allowed_tools)])
        if disallowed_tools:
            flags.extend(["--disallowedTools", ",".join(disallowed_tools)])
        return flags

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        if fallback and fallback != model:
            flags.extend(["--fallback-model", fallback])
        return flags

    def build_output_args(self, fmt: str = "") -> List[str]:
        if not fmt:
            return []
        # Claude CLI requires --verbose alongside --output-format stream-json
        # in print mode; the events are otherwise suppressed.
        if fmt == "stream-json":
            return ["--output-format", fmt, "--verbose"]
        return ["--output-format", fmt]

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns > 0:
            return ["--max-turns", str(max_turns)]
        return []

    # Valid effort levels for Claude Code CLI --effort flag.
    _EFFORT_LEVELS = {"low", "medium", "high", "max"}

    def build_effort_args(self, effort: str = "") -> List[str]:
        if effort and effort in self._EFFORT_LEVELS:
            return ["--effort", effort]
        return []

    def build_thinking_args(
        self, enabled: bool = False, budget_tokens: int = 0,
    ) -> List[str]:
        if not enabled:
            return []
        # Claude Code CLI activates extended thinking via --effort max.
        # budget_tokens is not directly supported by the CLI — the API-level
        # token budget is managed by the Claude backend, not the CLI flag.
        return ["--effort", "max"]

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if not configs:
            return []
        flags = ["--mcp-config"]
        flags.extend(configs)
        return flags

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Claude/Anthropic quota failures.

        Preserve the legacy split behavior: stderr is trusted for all quota
        patterns, while stdout only matches strict provider error phrases so
        normal assistant discussion of rate limits does not pause Koan.
        """
        from app.quota_handler import (
            _QUOTA_RE,
            _rate_limit_exhausted,
            _strict_quota_match,
        )

        return (
            bool(_QUOTA_RE.search(stderr_text or ""))
            or _rate_limit_exhausted(stderr_text or "")
            or _strict_quota_match(stdout_text or "")
        )

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if not plugin_dirs:
            return []
        flags: List[str] = []
        for d in plugin_dirs:
            flags.extend(["--plugin-dir", d])
        return flags

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Check Claude API quota availability.

        Note: ``claude usage`` is not a real subcommand — it would be
        interpreted as a prompt and hang.  Instead, we always return
        True and rely on quota_handler.py to detect exhaustion from
        the actual CLI output after each run.
        """
        # No lightweight zero-cost probe exists in the Claude CLI.
        # Quota exhaustion is detected post-run by quota_handler.py.
        return True, ""
