"""xAI Grok Build CLI provider implementation."""

import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from app.provider.base import CLIProvider
from app.run_log import log_safe

# Grok Build fronts the xAI API (and optional custom model endpoints). Quota
# and auth wording is API-ish; patterns stay backend-agnostic.
_GROK_QUOTA_PATTERNS = [
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
_GROK_QUOTA_RE = re.compile("|".join(_GROK_QUOTA_PATTERNS), re.IGNORECASE)

_GROK_AUTH_PATTERNS = [
    r"\b401\s+Unauthorized\b",
    r"unexpected\s+status\s+401",
    r"authentication\s+failed",
    r"invalid\s+api\s+key",
    r"api\s+key.*(?:invalid|missing|expired)",
    r"not\s+authenticated",
    r"run\s+`?grok\s+login`?",
    r"XAI_API_KEY",
]
_GROK_AUTH_RE = re.compile("|".join(_GROK_AUTH_PATTERNS), re.IGNORECASE)

_STDOUT_ERROR_MARKERS = ("error", "rate", "limit", "quota", "http", "status", "api", "auth")

# Unsupported inputs: warn once per process (Haze two-tier precedent).
# - "info"    → Koan-default static capabilities the operator cannot act on
# - "warning" → operator-actionable config
_WARNED_UNSUPPORTED: set = set()

# Reasoning effort values accepted by ``--reasoning-effort`` / ``--effort``.
# Unknown values are dropped with a notice rather than passed through.
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "max", "xhigh"})


class GrokProvider(CLIProvider):
    """xAI Grok Build CLI provider (https://x.ai/cli, https://docs.x.ai/build).

    Targets headless Grok Build (verified against 0.2.101):

    - Prompt: ``grok -p <prompt>`` (``--single``)
    - Model: ``-m <model>``
    - Output: ``--output-format streaming-json`` (NDJSON: thought/text/end)
      or ``json`` (single object with ``text`` + ``usage``)
    - Permissions: ``--always-approve`` when skip_permissions is set;
      otherwise ``--permission-mode acceptEdits`` so headless runs do not
      block on interactive prompts
    - Tools: ``--tools`` / ``--disallowed-tools`` (comma-separated)
    - Max turns: ``--max-turns``
    - System prompt: ``--rules`` (append) / ``--system-prompt-override``
    - Effort: ``--reasoning-effort``
    - Session: ``--resume`` when supported

    Stream samples: ``koan/tests/grok_samples.py``.
    Configuration: ``cli_provider: "grok"`` or ``KOAN_CLI_PROVIDER=grok``.
    Auth: prior ``grok`` login, or ``XAI_API_KEY``.
    """

    name = "grok"

    def binary(self) -> str:
        if self._binary_override:
            return self._resolve_binary_path(self._binary_override)
        return "grok"

    def is_available(self) -> bool:
        return shutil.which(self.binary()) is not None

    def invocation_lock_name(self) -> str:
        # Sessions and config live under ~/.grok/; serialize concurrent invokes
        # so auth/session files do not race.
        return "grok-cli"

    def supports_stream_json(self) -> bool:
        return True

    def supports_session_resume(self) -> bool:
        return True

    def build_resume_args(self, session_id: str) -> List[str]:
        if session_id:
            return ["--resume", session_id]
        return []

    # ------------------------------------------------------------------
    # Prompt delivery
    # ------------------------------------------------------------------

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def supports_stdin_prompt_passing(self) -> bool:
        # Headless path is ``-p`` / ``--prompt-file``; stdin is not a documented
        # prompt channel for Grok Build headless mode.
        return False

    def supports_system_prompt_file(self) -> bool:
        # No dedicated file flag; callers may still pass inline system_prompt
        # via --rules. File content is inlined in build_command when needed.
        return False

    def build_system_prompt_args(self, system_prompt: str) -> List[str]:
        # Append extra rules to Grok's system prompt (closest to Claude's
        # --append-system-prompt).
        if system_prompt:
            return ["--rules", system_prompt]
        return []

    # ------------------------------------------------------------------
    # Flag builders
    # ------------------------------------------------------------------

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        if fallback:
            self._warn_unsupported_once(
                "fallback",
                "fallback model is not supported by Grok Build; ignored",
                level="info",
            )
        return ["-m", model] if model else []

    def build_output_args(self, fmt: str = "") -> List[str]:
        # Koan internal name is stream-json; Grok CLI spelling is streaming-json.
        if fmt == "stream-json":
            return ["--output-format", "streaming-json"]
        if fmt == "json":
            return ["--output-format", "json"]
        if fmt == "plain":
            return ["--output-format", "plain"]
        return []

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        if skip_permissions:
            return ["--always-approve"]
        # Headless Koan cannot answer interactive permission prompts. Prefer
        # acceptEdits (workspace edits without full bypass) over hanging.
        return ["--permission-mode", "acceptEdits"]

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        flags: List[str] = []
        if allowed_tools:
            flags.extend(["--tools", ",".join(allowed_tools)])
        if disallowed_tools:
            flags.extend(["--disallowed-tools", ",".join(disallowed_tools)])
        return flags

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns > 0:
            return ["--max-turns", str(max_turns)]
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if configs:
            self._warn_unsupported_once(
                "mcp",
                "MCP config is not supported via CLI flags for Grok Build; ignored",
            )
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if plugin_dirs:
            self._warn_unsupported_once(
                "plugins",
                "plugin directories are not supported via CLI flags; ignored",
            )
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        if not effort:
            return []
        level = effort.strip().lower()
        if level not in _EFFORT_LEVELS:
            self._warn_unsupported_once(
                f"effort:{level}",
                f"unknown reasoning effort {effort!r}; ignored "
                f"(valid: {', '.join(sorted(_EFFORT_LEVELS))})",
            )
            return []
        return ["--reasoning-effort", level]

    def build_thinking_args(
        self, enabled: bool = False, budget_tokens: int = 0,
    ) -> List[str]:
        if not enabled:
            return []
        # Grok has no separate thinking toggle; map to high effort.
        return ["--reasoning-effort", "high"]

    def _warn_unsupported_once(
        self, feature: str, message: str, level: str = "warning",
    ) -> None:
        if feature in _WARNED_UNSUPPORTED:
            return
        _WARNED_UNSUPPORTED.add(feature)
        log_safe(level, f"[{self.name}] {message}")

    # ------------------------------------------------------------------
    # Command assembly
    # ------------------------------------------------------------------

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
        """Build ``grok [flags] -p <prompt>``.

        Prompt args stay last for readability and for any future stdin rewrite.
        When *system_prompt_file* is set, its contents are inlined via
        ``--rules`` (no dedicated file flag on Grok Build 0.2.x).
        """
        sys_args: List[str] = []
        if system_prompt_file:
            try:
                with open(system_prompt_file, encoding="utf-8") as fh:
                    file_prompt = fh.read().strip()
            except OSError as exc:
                self._warn_unsupported_once(
                    "system_prompt_file_read",
                    f"could not read system prompt file {system_prompt_file!r}: {exc}",
                )
                file_prompt = ""
            if file_prompt:
                # File path wins over inline when both are provided.
                sys_args = ["--rules", file_prompt]
            elif system_prompt:
                sys_args = self.build_system_prompt_args(system_prompt)
        elif system_prompt:
            sys_args = self.build_system_prompt_args(system_prompt)

        cmd = [self.binary()]
        if resume_session_id and self.supports_session_resume():
            cmd.extend(self.build_resume_args(resume_session_id))
        cmd.extend(self.build_permission_args(skip_permissions))
        cmd.extend(sys_args)
        cmd.extend(self.build_tool_args(allowed_tools, disallowed_tools))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_max_turns_args(max_turns))
        cmd.extend(self.build_mcp_args(mcp_configs))
        cmd.extend(self.build_plugin_args(plugin_dirs))
        cmd.extend(self.build_effort_args(effort))
        cmd.extend(self.build_prompt_args(prompt))
        return cmd

    # ------------------------------------------------------------------
    # Failure classification & quota probing
    # ------------------------------------------------------------------

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect quota/rate-limit failures from Grok Build output."""
        if _GROK_QUOTA_RE.search(stderr_text or ""):
            return True
        if exit_code == 0:
            return False
        for line in (stdout_text or "").splitlines():
            stripped = line.strip()
            if not stripped or not self._line_has_error_marker(
                stripped, _STDOUT_ERROR_MARKERS
            ):
                continue
            if _GROK_QUOTA_RE.search(stripped):
                return True
        return False

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect authentication failures (missing login / API key)."""
        if exit_code == 0:
            return False
        if _GROK_AUTH_RE.search(stderr_text or ""):
            return True
        return any(
            _GROK_AUTH_RE.search(line)
            for line in (stdout_text or "").splitlines()
            if line.strip()
        )

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Best-effort quota/auth probe via a minimal headless 'ok' run.

        Uses ``--output-format json`` and an empty scratch cwd so project
        context files do not inflate token cost (Haze/Cline precedent).
        Probe errors never block real work.
        """
        import tempfile

        from app.cli_exec import run_cli
        from app.utils import koan_tmp_dir

        cmd = [
            self.binary(),
            "--always-approve",
            "--max-turns", "1",
            "--output-format", "json",
            "-p", "ok",
        ]
        probe_dir = tempfile.mkdtemp(prefix="grok-probe-", dir=koan_tmp_dir())
        try:
            result = run_cli(
                cmd,
                provider=self,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=probe_dir,
            )
        except subprocess.TimeoutExpired:
            return True, ""
        except Exception as e:
            log_safe("error", f"[{self.name}] quota probe error: {e}")
            return True, ""
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)

        stdout_text = result.stdout or ""
        stderr_text = result.stderr or ""
        for detect in (self.detect_quota_exhaustion, self.detect_auth_failure):
            if detect(
                stdout_text=stdout_text,
                stderr_text=stderr_text,
                exit_code=result.returncode,
            ):
                return False, (stderr_text + "\n" + stdout_text).strip()
        return True, ""
