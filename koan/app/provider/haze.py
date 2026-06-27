"""Haze CLI provider implementation."""

import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from app.provider.base import CLIProvider
from app.run_log import log_safe

# Haze is multi-backend (-m provider:model), so use generic patterns that
# work across Anthropic, OpenAI, OpenRouter, etc. — mirrors cline.py.
_HAZE_QUOTA_PATTERNS = [
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
_HAZE_QUOTA_RE = re.compile("|".join(_HAZE_QUOTA_PATTERNS), re.IGNORECASE)

_HAZE_AUTH_PATTERNS = [
    r"\b401\s+Unauthorized\b",
    r"unexpected\s+status\s+401",
    r"access\s+token",
    r"authentication\s+failed",
    r"invalid\s+api\s+key",
    r"api\s+key.*(?:invalid|missing|expired)",
]
_HAZE_AUTH_RE = re.compile("|".join(_HAZE_AUTH_PATTERNS), re.IGNORECASE)


class HazeProvider(CLIProvider):
    """Haze CLI provider (https://github.com/DenizOkcu/haze).

    One-shot headless interface (haze#9):
    - Binary: 'haze'
    - Prompt: -p/--prompt "<text>" (final flag; non-interactive one-shot)
    - Model: -m/--model "<provider:model>" (per-run override, no fallback)
    - Output: --output text|json; json envelope is {type,status,result,usage}
    - No per-tool flags, no --max-turns, no MCP flags, no system-prompt flag
    - Exit code 0 = success, non-zero = failure

    Configuration (config.yaml): cli_provider: "haze"
    Environment: KOAN_CLI_PROVIDER=haze
    """

    name = "haze"

    def binary(self) -> str:
        return "haze"

    def is_available(self) -> bool:
        return shutil.which("haze") is not None

    def emits_incremental_progress(self) -> bool:
        # Haze --output json prints a single final envelope, not a JSONL
        # stream, so the stdout-tail stagnation heuristic cannot apply.
        return False

    def invocation_lock_name(self) -> str:
        return "haze-cli"

    def supports_stdin_prompt_passing(self) -> bool:
        # Keep the documented `-p <prompt>` form; haze reads stdin only when
        # -p is absent, so the base `-p -` marker rewrite is unverified.
        return False

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["-m", model])
        if fallback:
            log_safe("warning", f"[{self.name}] fallback model is not supported by Haze; ignored")
        return flags

    def build_output_args(self, fmt: str = "") -> List[str]:
        # Haze uses --output json|text. Opt into json only when a structured
        # format is requested (mission path passes "json"); else haze text.
        if fmt in {"json", "stream-json"}:
            return ["--output", "json"]
        return []

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        return []  # Haze has no per-tool allow/disallow flags.

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        return []  # Haze one-shot runs to completion.

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if configs:
            log_safe("warning", f"[{self.name}] MCP config is not supported via CLI flags; ignored")
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if plugin_dirs:
            log_safe("warning", f"[{self.name}] plugin directories are not supported; ignored")
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        if effort:
            log_safe("warning", f"[{self.name}] reasoning effort control is not supported; ignored")
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
        """Build a complete Haze CLI command: haze [-m M] [--output json] -p "<prompt>"."""
        # Haze has no system-prompt flag; prepend (base fallback behavior).
        if system_prompt_file:
            log_safe("warning", f"[{self.name}] system prompt file is not supported; falling back to inline system prompt")
        if system_prompt:
            prompt = system_prompt + "\n\n" + prompt

        cmd = [self.binary()]
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_prompt_args(prompt))  # -p "<prompt>" last
        return cmd

    # Substrings that mark a stdout line as a likely provider/CLI error,
    # passed to the inherited CLIProvider._line_has_error_marker() helper
    # (provider/base.py) — same pattern as cline.py / codex.py.
    _STDOUT_ERROR_MARKERS = ("error", "rate", "limit", "quota", "http", "status", "api")

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Haze quota/rate-limit failures.

        Stderr is trusted for the full pattern set. Stdout (the JSON envelope's
        status/result/type fields) is only scanned when the CLI failed AND the
        line resembles a provider error — gating off benign assistant prose.
        """
        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""
        if _HAZE_QUOTA_RE.search(stderr_text):
            return True
        if exit_code == 0:
            return False
        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped or not self._line_has_error_marker(stripped, self._STDOUT_ERROR_MARKERS):
                continue
            if _HAZE_QUOTA_RE.search(stripped):
                return True
        return False

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Haze authentication failures (401 / invalid key)."""
        if exit_code == 0:
            return False
        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""
        if _HAZE_AUTH_RE.search(stderr_text):
            return True
        for line in stdout_text.splitlines():
            if line.strip() and _HAZE_AUTH_RE.search(line):
                return True
        return False

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Best-effort quota probe via a tiny one-shot 'ok' run.

        NOTE: consumes a small number of tokens. Returns (True, '') on any
        probe error/timeout so a flaky probe never blocks real work.
        """
        cmd = [self.binary(), "--output", "json", "-p", "ok"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=project_path,
            )
            for detect in (self.detect_quota_exhaustion, self.detect_auth_failure):
                if detect(stdout_text=result.stdout or "", stderr_text=result.stderr or "",
                          exit_code=result.returncode):
                    return False, (result.stderr or "") + "\n" + (result.stdout or "")
            return True, ""
        except subprocess.TimeoutExpired:
            return True, ""
        except Exception as e:
            log_safe("error", f"[{self.name}] quota probe error: {e}")
            return True, ""
