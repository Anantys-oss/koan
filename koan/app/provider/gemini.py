"""Google Gemini CLI provider implementation."""

import re
import shutil
import subprocess
from typing import List, Optional, Sequence, Tuple

from app.provider.base import CLIProvider
from app.run_log import log_safe

# Gemini CLI fronts the Gemini API (OAuth "Login with Google", Gemini API key,
# or Vertex AI). Quota wording is Google-API-ish: RESOURCE_EXHAUSTED / 429 plus
# free-tier daily caps. Patterns stay backend-agnostic where possible.
_GEMINI_QUOTA_PATTERNS = [
    r"rate[_\s-]?limit(?:ed|_error| exceeded)?",
    r"resource[_\s-]?exhausted",
    r"\bquota\b.*(?:exceeded|reached|exhausted|insufficient)",
    r"(?:exceeded|reached|exhausted|insufficient).*\bquota\b",
    r"usage.*(?:limit|cap).*(?:reached|exceeded|hit)",
    r"billing.*(?:limit|quota|credit)",
    r"HTTP\s*429",
    r"status[\s:]+429",
    r"too many requests",
    r"retry[\s-]+after",
]
_GEMINI_QUOTA_RE = re.compile("|".join(_GEMINI_QUOTA_PATTERNS), re.IGNORECASE)

_GEMINI_AUTH_PATTERNS = [
    r"\b401\s+Unauthorized\b",
    r"unauthenticated",
    r"authentication\s+failed",
    r"api\s+key\s+not\s+valid",
    r"invalid\s+api\s+key",
    r"api\s+key.*(?:invalid|missing|expired)",
    r"please\s+set\s+an\s+auth\s+method",
    r"GEMINI_API_KEY",
    r"GOOGLE_API_KEY",
]
_GEMINI_AUTH_RE = re.compile("|".join(_GEMINI_AUTH_PATTERNS), re.IGNORECASE)

_STDOUT_ERROR_MARKERS = ("error", "rate", "limit", "quota", "http", "status", "api", "auth")

# Unsupported inputs: warn once per process (Haze/Grok two-tier precedent).
# - "info"    → Koan-default static capabilities the operator cannot act on
# - "warning" → operator-actionable config
_WARNED_UNSUPPORTED: set = set()

# Claude tier aliases (and Claude Code model ids) that Gemini rejects. They leak
# in via models.default; omit ``-m`` so Gemini's own ``auto`` alias applies.
_CLAUDE_MODEL_ALIASES = frozenset({
    "haiku", "sonnet", "opus",
    "claude-haiku", "claude-sonnet", "claude-opus",
})
_CLAUDE_MODEL_PREFIXES = ("claude-", "claude ")


class GeminiProvider(CLIProvider):
    """Google Gemini CLI provider (https://github.com/google-gemini/gemini-cli).

    Targets documented headless mode:

    - Prompt: ``gemini -p <prompt>`` (argv only — see
      :meth:`supports_stdin_prompt_passing`)
    - Model: ``-m <model>`` (``auto``/``pro``/``flash``/``flash-lite`` or a
      concrete id; Claude aliases refused — omit for Gemini's default)
    - Output: ``--output-format stream-json`` (NDJSON:
      init/message/tool_use/tool_result/error/result) or ``json`` (single
      object with ``response`` + ``stats``)
    - Permissions: ``--approval-mode yolo`` only when ``skip_permissions``
    - Session: ``--resume <id>``

    Per-tool allow/deny, max turns, MCP config paths, plugin dirs and reasoning
    effort have no headless equivalent — they warn once and are skipped.

    Durable contract: specs/components/providers.md ("Gemini CLI headless
    contract"). Stream samples: ``koan/tests/gemini_samples.py``.
    Configuration: ``cli_provider: "gemini"`` or ``KOAN_CLI_PROVIDER=gemini``.
    Auth: ``gemini`` OAuth login, or ``GEMINI_API_KEY``.
    """

    name = "gemini"

    def binary(self) -> str:
        if self._binary_override:
            return self._resolve_binary_path(self._binary_override)
        return "gemini"

    def is_available(self) -> bool:
        return shutil.which(self.binary()) is not None

    def invocation_lock_name(self) -> str:
        # Auth, sessions and settings live under ~/.gemini/; serialize
        # concurrent invokes so those files do not race.
        return "gemini-cli"

    def supports_stream_json(self) -> bool:
        return True

    def supports_session_resume(self) -> bool:
        return True

    def build_resume_args(self, session_id: str) -> List[str]:
        return ["--resume", session_id] if session_id else []

    # ------------------------------------------------------------------
    # Prompt delivery
    # ------------------------------------------------------------------

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def supports_stdin_prompt_passing(self) -> bool:
        # Gemini *appends* ``-p`` to piped stdin rather than replacing it, so
        # the base marker rewrite would send "<prompt>@stdin". A flag-removal
        # rewrite would work in principle but is unverified against a real
        # binary; until then the prompt rides argv.
        return False

    # ------------------------------------------------------------------
    # Flag builders
    # ------------------------------------------------------------------

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        if fallback:
            self._warn_unsupported_once(
                "fallback",
                "fallback model is not supported by Gemini CLI; ignored",
                level="info",
            )
        if not model:
            return []
        if self._is_claude_model_alias(model):
            self._warn_unsupported_once(
                f"claude_model:{model.strip().lower()}",
                f"model {model!r} is a Claude alias unknown to Gemini CLI; "
                "omitting -m (using Gemini default). Set models.gemini.* in "
                "config.yaml to a Gemini model id or alias "
                "(auto/pro/flash/flash-lite).",
                level="warning",
            )
            return []
        return ["-m", model]

    @staticmethod
    def _is_claude_model_alias(model: str) -> bool:
        normalized = model.strip().lower()
        if not normalized:
            return False
        if normalized in _CLAUDE_MODEL_ALIASES:
            return True
        return any(normalized.startswith(p) for p in _CLAUDE_MODEL_PREFIXES)

    def build_output_args(self, fmt: str = "") -> List[str]:
        if fmt in {"json", "stream-json", "text"}:
            return ["--output-format", fmt]
        return []

    def build_permission_args(
        self, skip_permissions: bool = False, read_only: bool = False,
    ) -> List[str]:
        """Emit ``--approval-mode yolo`` only when the operator opted in.

        Deliberately NOT an unconditional auto-approve: headless Gemini cannot
        answer a confirmation prompt, but silently escalating past the
        operator's configured permission posture is worse than a tool call that
        fails. *read_only* is accepted for API parity — the provider declares no
        read-only capability, so ``build_full_command`` refuses such an
        invocation before it reaches here.
        """
        if skip_permissions:
            return ["--approval-mode", "yolo"]
        self._warn_unsupported_once(
            "headless_permissions",
            "headless Gemini cannot answer tool confirmation prompts; tool "
            "calls needing approval may fail. Set skip_permissions: true in "
            "config.yaml to run with --approval-mode yolo.",
        )
        return []

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        restrict_tools: Optional[Sequence[str]] = None,
    ) -> List[str]:
        # Gemini's ``--allowed-tools`` is deprecated upstream in favour of the
        # Policy Engine, and is a PRE-APPROVAL list, not a withholding one — the
        # exact shape READ_ONLY_ROLES exists to distrust. Emit nothing rather
        # than a deprecated flag that would not enforce anything.
        if allowed_tools or disallowed_tools:
            self._warn_unsupported_once(
                "tools",
                "per-tool allow/deny is not wired for Gemini CLI "
                "(--allowed-tools is deprecated upstream); ignored",
                level="info",
            )
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns:
            self._warn_unsupported_once(
                "max_turns",
                "max turns is not supported by Gemini CLI; ignored",
                level="info",
            )
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if configs:
            self._warn_unsupported_once(
                "mcp",
                "MCP config files are not supported via CLI flags; register "
                "servers with `gemini mcp add` instead",
            )
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if plugin_dirs:
            self._warn_unsupported_once(
                "plugins",
                "plugin directories are not supported; use `gemini extensions` "
                "instead. Ignored",
            )
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        if effort:
            self._warn_unsupported_once(
                "effort",
                "reasoning effort control is not supported by Gemini CLI; ignored",
            )
        return []

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
        project_context: bool = True,
        read_only: bool = False,
    ) -> List[str]:
        """Build ``gemini [flags] -p <prompt>``.

        Gemini has no append-system-prompt flag (its ``GEMINI_SYSTEM_MD`` hook
        *replaces* the built-in prompt), so a system prompt is prepended to the
        user prompt — the base fallback shape. Prompt args stay last for
        readability. *project_context* is accepted for API parity (base no-op).
        """
        if system_prompt_file:
            self._warn_unsupported_once(
                "system_prompt_file",
                "system prompt file is not supported; "
                "falling back to inline system prompt",
            )
        if system_prompt:
            prompt = system_prompt + "\n\n" + prompt

        cmd = [self.binary()]
        if resume_session_id:
            cmd.extend(self.build_resume_args(resume_session_id))
        cmd.extend(self.build_project_context_args(project_context))
        cmd.extend(self.build_permission_args(skip_permissions, read_only=read_only))
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
        """Detect quota/rate-limit failures from Gemini CLI output.

        Stderr is trusted for the full pattern set. Stdout is scanned only when
        the CLI failed AND the line resembles a provider error, so assistant
        prose about rate limits on a successful run cannot pause Kōan.
        """
        if _GEMINI_QUOTA_RE.search(stderr_text or ""):
            return True
        if exit_code == 0:
            return False
        for line in (stdout_text or "").splitlines():
            stripped = line.strip()
            if not stripped or not self._line_has_error_marker(
                stripped, _STDOUT_ERROR_MARKERS
            ):
                continue
            if _GEMINI_QUOTA_RE.search(stripped):
                return True
        return False

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect authentication failures (missing login / invalid API key)."""
        if exit_code == 0:
            return False
        if _GEMINI_AUTH_RE.search(stderr_text or ""):
            return True
        return any(
            _GEMINI_AUTH_RE.search(line)
            for line in (stdout_text or "").splitlines()
            if line.strip()
        )

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Best-effort quota/auth probe via a minimal headless 'ok' run.

        Gemini exposes no free usage introspection, so the probe is a real
        (tiny) run — Haze/Grok precedent. It runs from a fresh EMPTY directory,
        never *project_path*: Gemini ingests ``GEMINI.md`` context files from
        its cwd, which would inflate a "tiny" probe. Any probe error or timeout
        reports available so a flaky probe never blocks real work.
        """
        import tempfile

        from app.cli_exec import run_cli
        from app.utils import koan_tmp_dir

        cmd = [self.binary(), "--output-format", "json", "-p", "ok"]
        probe_dir = tempfile.mkdtemp(prefix="gemini-probe-", dir=koan_tmp_dir())
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
