"""Haze CLI provider implementation."""

import re
import shutil
import subprocess
from typing import List, Optional, Sequence, Tuple

from app.provider.base import CLIProvider
from app.run_log import log_safe

# Haze is multi-backend (-m provider:model over OpenAI, OpenRouter, local
# endpoints), so quota/auth detection uses generic patterns that work across
# backends — same rationale as cline.py.
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
    r"authentication\s+failed",
    r"invalid\s+api\s+key",
    r"api\s+key.*(?:invalid|missing|expired)",
]
_HAZE_AUTH_RE = re.compile("|".join(_HAZE_AUTH_PATTERNS), re.IGNORECASE)

# Substrings marking a stdout line as a likely provider/CLI error, passed to
# the inherited CLIProvider._line_has_error_marker() gate so benign assistant
# prose is never scanned for quota text.
_STDOUT_ERROR_MARKERS = ("error", "rate", "limit", "quota", "http", "status", "api")

# Unsupported inputs are never silently accepted (durable contract), but the
# notice level follows a two-tier rule, deduped once per process:
# - "info"    → static capabilities driven by Koan's OWN defaults (per-tool
#               allow/deny, max turns, fallback model). The loop passes these
#               unconditionally; the operator did nothing wrong and cannot
#               act, so per-mission "warning" lines would be pure noise.
# - "warning" → operator-actionable config (MCP, plugins, effort, resume,
#               system-prompt file — removable from config) and the
#               safety-relevant no-permission-gates notice.
_WARNED_UNSUPPORTED: set = set()


class HazeProvider(CLIProvider):
    """Haze CLI provider (https://github.com/DenizOkcu/haze).

    Targets haze >= 0.7.0 one-shot headless mode:

    - Prompt: piped via stdin (haze reads stdin only when ``-p`` is absent);
      :meth:`build_prompt_args` still emits ``-p <prompt>`` for callers that
      do not use stdin passing (e.g. the quota probe).
    - Model: ``-m <provider:model>`` per-run override; no fallback model.
    - Output: ``--output stream-json`` — NDJSON progress events terminated by
      a ``{type, status, result, usage}`` envelope with camelCase usage
      fields; exit code 0 <=> status ``complete``.
    - Headless runs are one-shot: no session resume, no per-tool / MCP /
      plugin / max-turns / effort flags — unsupported inputs are warned and
      skipped, never silently accepted.

    Durable contract: specs/components/providers.md ("Haze headless contract").
    Configuration: ``cli_provider: "haze"`` or ``KOAN_CLI_PROVIDER=haze``.
    """

    name = "haze"

    def binary(self) -> str:
        if self._binary_override:
            return self._resolve_binary_path(self._binary_override)
        return "haze"

    def is_available(self) -> bool:
        return shutil.which(self.binary()) is not None

    def invocation_lock_name(self) -> str:
        # Haze invocations share ~/.haze/settings.json auth/session state.
        return "haze-cli"

    def supports_stream_json(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Prompt delivery
    # ------------------------------------------------------------------

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def supports_stdin_prompt_passing(self) -> bool:
        # Target design is stdin delivery (rewrite_prompt_for_stdin below),
        # but haze's stdin fallback is broken upstream: its gate checks
        # `process.stdin.isTTY === false`, and Node sets isTTY to
        # *undefined* (not false) for pipes/files, so piped runs fall into
        # the interactive Ink UI and crash (verified live 2026-07-10 against
        # haze 0.7.0; upstream main has the same gate). Flip to True once
        # upstream reads stdin for any non-TTY input.
        return False

    def rewrite_prompt_for_stdin(
        self,
        cmd: Sequence[str],
        stdin_marker: str,
    ) -> Tuple[List[str], Optional[str]]:
        """Move the prompt from argv to stdin by REMOVING ``-p <prompt>``.

        Haze reads the prompt from stdin only when ``-p`` is absent, so the
        base marker substitution (``-p @stdin``) would send the marker as the
        literal prompt. *stdin_marker* is therefore unused here.

        Currently dormant: only consulted when
        :meth:`supports_stdin_prompt_passing` returns True (see the upstream
        isTTY bug note there). Kept implemented and tested so enabling stdin
        delivery is a one-line change once upstream ships the fix.
        """
        cmd_list = list(cmd)
        try:
            flag_idx = cmd_list.index("-p")
        except ValueError:
            return cmd_list, None
        prompt_idx = flag_idx + 1
        if prompt_idx >= len(cmd_list):
            return cmd_list, None
        prompt = cmd_list[prompt_idx]
        return cmd_list[:flag_idx] + cmd_list[prompt_idx + 1:], prompt

    # ------------------------------------------------------------------
    # Flag builders
    # ------------------------------------------------------------------

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        if fallback:
            # Usually inherited from Koan's global models defaults, not the
            # haze block — info tier, or it would recur every mission.
            self._warn_unsupported_once(
                "fallback",
                "fallback model is not supported by haze; ignored",
                level="info",
            )
        return ["-m", model] if model else []

    def build_output_args(self, fmt: str = "") -> List[str]:
        # stream-json is the primary harness mode (haze >= 0.7.0); plain json
        # (single terminal envelope) is used by the quota probe.
        if fmt in {"json", "stream-json"}:
            return ["--output", fmt]
        return []

    def _warn_unsupported_once(
        self, feature: str, message: str, level: str = "warning",
    ) -> None:
        if feature in _WARNED_UNSUPPORTED:
            return
        _WARNED_UNSUPPORTED.add(feature)
        log_safe(level, f"[{self.name}] {message}")

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        if allowed_tools or disallowed_tools:
            self._warn_unsupported_once(
                "tools",
                "per-tool allow/deny is not supported by haze "
                "(fixed built-in toolset); tool restrictions ignored",
                level="info",
            )
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns:
            self._warn_unsupported_once(
                "max_turns",
                "max turns is not supported by haze; one-shot runs to completion",
                level="info",
            )
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if configs:
            self._warn_unsupported_once(
                "mcp",
                "MCP config is not supported via CLI flags; "
                "configure servers inside haze (/mcp) instead",
            )
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if plugin_dirs:
            self._warn_unsupported_once(
                "plugins",
                "plugin directories are not supported; ignored",
            )
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        if effort:
            self._warn_unsupported_once(
                "effort",
                "reasoning effort control is not supported; ignored",
            )
        return []

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
        """Build ``haze [-m <sel>] [--output <fmt>] -p <prompt>``.

        Prompt args stay last so :meth:`rewrite_prompt_for_stdin` and human
        readers find them in a fixed position. Unsupported inputs are routed
        through their builders (which warn) rather than dropped here.
        """
        if system_prompt_file:
            self._warn_unsupported_once(
                "system_prompt_file",
                "system prompt file is not supported; "
                "falling back to inline system prompt",
            )
        if system_prompt:
            # No dedicated system-prompt flag: prepend (base fallback shape).
            prompt = system_prompt + "\n\n" + prompt
        if resume_session_id:
            self._warn_unsupported_once(
                "resume",
                "session resume is not supported "
                "(headless haze is one-shot); starting fresh",
            )
        if not skip_permissions:
            # Haze has no confirmation gates by design — every run is
            # effectively full-auto. An operator who configured permission
            # gating must not get silent unattended tool access.
            self._warn_unsupported_once(
                "permissions",
                "permission gating is not supported by haze "
                "(no confirmation gates); runs execute with full tool access",
            )

        cmd = [self.binary()]
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
        """Detect quota/rate-limit failures from haze output.

        Stderr is trusted for the full pattern set (backends report errors
        there before the agent runs). Stdout — where the result envelope's
        error text lands — is scanned only when the CLI failed AND the line
        resembles a provider error, so benign assistant prose on successful
        runs can never trigger a quota pause.
        """
        if _HAZE_QUOTA_RE.search(stderr_text or ""):
            return True
        if exit_code == 0:
            return False
        for line in (stdout_text or "").splitlines():
            stripped = line.strip()
            if not stripped or not self._line_has_error_marker(
                stripped, _STDOUT_ERROR_MARKERS
            ):
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
        """Detect authentication failures (401 / invalid or missing key)."""
        if exit_code == 0:
            return False
        if _HAZE_AUTH_RE.search(stderr_text or ""):
            return True
        return any(
            _HAZE_AUTH_RE.search(line)
            for line in (stdout_text or "").splitlines()
            if line.strip()
        )

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Best-effort quota/auth probe via a minimal one-shot 'ok' run.

        Haze exposes no free usage introspection, so the probe is a real
        (tiny) run — same precedent as cline. NOTE: consumes a small number
        of tokens per call. Any probe error or timeout reports available so
        a flaky probe never blocks real work.

        The probe runs from a fresh EMPTY directory, not *project_path*:
        haze ingests CLAUDE.md/AGENTS.md context files from its cwd, which
        inflates a "tiny" probe to ~12K input tokens inside a real project.
        Quota/auth state is global to the operator's haze setup, so the cwd
        is irrelevant to what the probe measures.
        """
        import tempfile

        from app.cli_exec import run_cli
        from app.utils import koan_tmp_dir

        cmd = [self.binary(), "--output", "json", "-p", "ok"]
        probe_dir = tempfile.mkdtemp(prefix="haze-probe-", dir=koan_tmp_dir())
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
