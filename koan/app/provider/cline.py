"""Cline CLI provider implementation."""

import re
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

from app.provider.base import CLIProvider


# Generic quota/auth patterns - Cline is multi-backend (OpenRouter, Anthropic, OpenAI, etc.)
_CLINE_QUOTA_PATTERNS = [
    r"rate[_\s-]?limit(?:ed|_error| exceeded)?",
    r"insufficient[_\s-]?quota",
    r"\bquota\b.*(?:exceeded|reached|exhausted|insufficient)",
    r"(?:exceeded|reached|exhausted|insufficient).*\bquota\b",
    r"usage.*(?:limit|cap).*(?:reached|exceeded|hit)",
    r"billing.*(?:limit|quota|credit)",
    r"HTTP\s*429",
    r"status[\s:]+429",
    r"too many requests",
    r"retry[\s-]+after",
]

_CLINE_QUOTA_RE = re.compile("|".join(_CLINE_QUOTA_PATTERNS), re.IGNORECASE)

_CLINE_AUTH_PATTERNS = [
    r"\b401\s+Unauthorized\b",
    r"unexpected\s+status\s+401",
    r"access\s+token",
    r"authentication\s+failed",
    r"invalid\s+api\s+key",
    r"api\s+key.*(?:invalid|missing|expired)",
]

_CLINE_AUTH_RE = re.compile("|".join(_CLINE_AUTH_PATTERNS), re.IGNORECASE)


class ClineProvider(CLIProvider):
    """Cline CLI provider.

    Translates Kōan's generic command spec into Cline CLI equivalents.
    Uses `--json` for JSONL headless output and `--auto-approve` for
    unattended tool execution.

    Key differences from Claude CLI:
    - Binary: 'cline'
    - Prompt: positional argument (final argument)
    - Tool control: No per-tool allow/disallow flags; uses CLINE_COMMAND_PERMISSIONS env
    - Model: -m/--model flag
    - No --fallback-model equivalent
    - No --append-system-prompt (falls back to prepend via base class)
    - No --max-turns (runs to completion)
    - Output: --json flag for JSONL events (not --output-format)
    - Permissions: --auto-approve true/false for unattended execution
    - Thinking: --thinking flag for extended thinking mode

    Configuration (config.yaml):
        cli_provider: "cline"

    Environment:
        KOAN_CLI_PROVIDER=cline
    """

    name = "cline"

    def binary(self) -> str:
        return "cline"

    def is_available(self) -> bool:
        return shutil.which("cline") is not None

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        # Cline uses --auto-approve for unattended execution.
        # When skip_permissions=True, pass --auto-approve true.
        # When False, pass --auto-approve false to prevent headless deadlock
        # (interactive approval prompts would block forever in non-interactive mode).
        if skip_permissions:
            return ["--auto-approve", "true"]
        return ["--auto-approve", "false"]

    def build_prompt_args(self, prompt: str) -> List[str]:
        # Cline takes the prompt as a positional argument at the end.
        # This is handled in build_command() override.
        return [prompt]

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ):
        # Cline CLI does not support per-tool allow/disallow flags.
        # Tool access is controlled via CLINE_COMMAND_PERMISSIONS environment variable.
        return []

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        # Cline has no --fallback-model; ignored silently
        return flags

    def supports_stream_json(self) -> bool:
        # Cline --json produces newline-delimited JSON messages in headless mode.
        return True

    def build_output_args(self, fmt: str = "") -> List[str]:
        # Cline uses --json for JSONL output.
        if fmt in {"json", "stream-json"}:
            return ["--json"]
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        # Cline CLI does not support --max-turns.
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        # Cline configures MCP servers via its own config, not CLI flags.
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        # Cline does not have plugin directories.
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        # Cline does not have reasoning effort controls.
        return []

    def build_thinking_args(
        self, enabled: bool = False, budget_tokens: int = 0
    ) -> List[str]:
        # Cline supports --thinking for extended thinking mode.
        # budget_tokens is ignored (Cline doesn't support token budgets).
        if enabled:
            return ["--thinking"]
        return []

    def invocation_lock_name(self) -> str:
        return "cline-cli"

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
        """Build a complete Cline CLI command.

        Cline command structure::

            cline [global-flags] "prompt"

        The prompt must be the final positional argument.
        Permission flags (--auto-approve), model (--model), and output
        (--json) are global flags that come before the prompt.
        """
        # Handle system prompt: Cline has no --append-system-prompt or
        # file-mode equivalent, so prepend to user prompt (base class
        # fallback behavior). system_prompt_file is silently ignored —
        # supports_system_prompt_file() returns False on this provider.
        if system_prompt:
            prompt = system_prompt + "\n\n" + prompt

        cmd = [self.binary()]

        # Global flags come before the positional prompt
        cmd.extend(self.build_permission_args(skip_permissions))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_max_turns_args(max_turns))
        cmd.extend(self.build_mcp_args(mcp_configs))
        cmd.extend(self.build_plugin_args(plugin_dirs))
        cmd.extend(self.build_effort_args(effort))
        cmd.extend(self.build_thinking_args())

        # Prompt is the final positional argument
        cmd.append(prompt)

        return cmd

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Check Cline API quota via a minimal prompt probe.

        Sends a tiny prompt ("ok") to surface rate-limit or subscription
        errors before a full mission is attempted. Cline is multi-backend
        (OpenRouter, Anthropic, OpenAI, etc.), so we use generic patterns.

        NOTE: This probe consumes a small number of tokens on each call.
        """
        cmd = [self.binary(), "--auto-approve", "true", "--json", "ok"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=project_path,
            )
            if self.detect_quota_exhaustion(
                stdout_text=result.stdout or "",
                stderr_text=result.stderr or "",
                exit_code=result.returncode,
            ):
                combined = (result.stderr or "") + "\n" + (result.stdout or "")
                return False, combined
            if self.detect_auth_failure(
                stdout_text=result.stdout or "",
                stderr_text=result.stderr or "",
                exit_code=result.returncode,
            ):
                combined = (result.stderr or "") + "\n" + (result.stdout or "")
                return False, combined

            return True, ""
        except subprocess.TimeoutExpired:
            return True, ""
        except Exception as e:
            print(f"[cline] quota probe error: {e}", file=sys.stderr)
            return True, ""

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Cline quota/rate-limit failures.

        Cline is multi-backend, so we use generic patterns that work across
        OpenRouter, Anthropic, OpenAI, etc. Stderr is trusted for the full
        pattern set. Stdout is only scanned when the CLI failed AND the
        matched line looks like a provider error message.
        """
        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""

        if _CLINE_QUOTA_RE.search(stderr_text):
            return True

        if exit_code == 0:
            return False

        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if not self._plain_stdout_quota_line(stripped):
                continue
            return True

        return False

    _STDOUT_ERROR_MARKERS = ("error", "rate", "limit", "quota", "http", "status", "api")

    def _plain_stdout_quota_line(self, line: str) -> bool:
        """Check stdout only when the line resembles a provider error."""
        if not self._line_has_error_marker(line, self._STDOUT_ERROR_MARKERS):
            return False
        return bool(_CLINE_QUOTA_RE.search(line))

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Cline authentication failures.

        Cline auth failures may appear in stderr or stdout depending on
        the backend provider. We scan both with generic auth patterns.
        """
        if exit_code == 0:
            return False

        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""

        if _CLINE_AUTH_RE.search(stderr_text):
            return True

        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _CLINE_AUTH_RE.search(stripped):
                return True

        return False