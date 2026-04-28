"""Claude Code CLI provider implementation."""

from typing import List, Optional, Tuple

from app.provider.base import CLIProvider


class ClaudeProvider(CLIProvider):
    """Claude Code CLI provider."""

    name = "claude"

    def binary(self) -> str:
        return "claude"

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        if skip_permissions:
            return ["--dangerously-skip-permissions"]
        return []

    def build_system_prompt_args(self, system_prompt: str) -> List[str]:
        if system_prompt:
            return ["--append-system-prompt", system_prompt]
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
            flags.extend(["--disallowedTools"] + disallowed_tools)
        return flags

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        if fallback and fallback != model:
            flags.extend(["--fallback-model", fallback])
        return flags

    def build_output_args(self, fmt: str = "") -> List[str]:
        if fmt:
            return ["--output-format", fmt]
        return []

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

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if not configs:
            return []
        flags = ["--mcp-config"]
        flags.extend(configs)
        return flags

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
