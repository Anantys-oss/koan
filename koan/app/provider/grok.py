"""xAI Grok Build CLI provider implementation."""

import re
import shutil
import subprocess
from typing import List, Optional, Sequence, Tuple

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
    # xAI billing exhaustion arrives as a 403 permission-denied with no
    # quota/usage/billing keyword: "used all available credits or reached
    # its monthly spending limit". Anchor on the exhaustion verb so mere
    # discussion of a spending-limit feature does not trip a false pause.
    r"used all (?:available )?credits?",
    r"(?:reached|hit|exceeded)[^\n]{0,25}spending limit",
    # xAI closes the same 403 with a fixed remediation clause: "please
    # purchase more credits or raise your spending limit". Anchoring on it too
    # keeps detection working if xAI rewords the leading exhaustion sentence.
    # "purchase more credits" is imperative remediation, not prose an agent
    # would emit while merely discussing billing, so it stays false-positive
    # safe in stdout.
    r"purchase more credits?",
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

# Claude tier aliases (and common Claude Code model ids) that Grok rejects.
# When these leak in via models.default / built-in haiku defaults, omit ``-m``
# so Grok uses its own default rather than failing with "unknown model id".
_CLAUDE_MODEL_ALIASES = frozenset({
    "haiku", "sonnet", "opus",
    "claude-haiku", "claude-sonnet", "claude-opus",
})
_CLAUDE_MODEL_PREFIXES = ("claude-", "claude ")

# Koan/Claude tool names → Grok Build internal tool IDs for ``--tools`` /
# ``--disallowed-tools`` (see Grok headless docs). Skill has no Grok peer.
_GROK_TOOL_NAME_MAP = {
    "Bash": "run_terminal_cmd",
    "Read": "read_file",
    "Write": "write",
    "Edit": "search_replace",
    "Glob": "list_dir",
    "Grep": "grep",
    "WebFetch": "web_fetch",
    "WebSearch": "web_search",
}
# Tools with no Grok allowlist equivalent — drop rather than pass a dead name.
_GROK_TOOLS_DROP = frozenset({"Skill"})

# Prompt longer than this rides ``--prompt-file`` instead of ``-p`` (ARG_MAX /
# ps hygiene). Implement plans routinely exceed tens of KB.
_PROMPT_FILE_THRESHOLD = 8_000


class GrokProvider(CLIProvider):
    """xAI Grok Build CLI provider (https://x.ai/cli, https://docs.x.ai/build).

    Targets headless Grok Build (verified against 0.2.101):

    - Prompt: ``grok -p <prompt>`` or ``--prompt-file`` for large prompts
    - Model: ``-m <model>`` (Claude aliases refused — omit for Grok default)
    - Output: ``--output-format streaming-json`` (NDJSON: thought/text/end)
      or ``json`` (single object with ``text`` + ``usage``)
    - Permissions: always ``--always-approve`` in headless Koan (Grok's CLI
      ``acceptEdits`` flag is a no-op; headless prompts cancel tools)
    - Tools: ``--tools`` / ``--disallowed-tools`` with Claude→Grok name map
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

    def supports_prompt_file_passing(self) -> bool:
        # Large prompts use ``--prompt-file`` via prepare_prompt_file.
        return True

    def rewrite_prompt_for_file(
        self,
        cmd: Sequence[str],
        prompt_path: str,
    ) -> Tuple[List[str], Optional[str]]:
        """Replace ``-p <prompt>`` with ``--prompt-file <path>`` when large."""
        cmd_list = list(cmd)
        try:
            prompt_idx = cmd_list.index("-p") + 1
        except ValueError:
            # Also accept long form.
            try:
                prompt_idx = cmd_list.index("--single") + 1
            except ValueError:
                return cmd_list, None
        if prompt_idx >= len(cmd_list):
            return cmd_list, None
        prompt = cmd_list[prompt_idx]
        if not isinstance(prompt, str) or not prompt:
            return cmd_list, None
        if len(prompt) < _PROMPT_FILE_THRESHOLD:
            return cmd_list, None
        rewritten = cmd_list[: prompt_idx - 1] + [
            "--prompt-file", prompt_path,
        ] + cmd_list[prompt_idx + 1 :]
        return rewritten, prompt

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
        if not model:
            return []
        if self._is_claude_model_alias(model):
            self._warn_unsupported_once(
                f"claude_model:{model.strip().lower()}",
                f"model {model!r} is a Claude alias unknown to Grok Build; "
                "omitting -m (using Grok default). Set models.grok.* in "
                "config.yaml to a real Grok model id (see `grok models`).",
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
        # Koan internal name is stream-json; Grok CLI spelling is streaming-json.
        if fmt == "stream-json":
            return ["--output-format", "streaming-json"]
        if fmt == "json":
            return ["--output-format", "json"]
        if fmt == "plain":
            return ["--output-format", "plain"]
        return []

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        # Grok headless cannot answer interactive permission prompts. The CLI
        # ``--permission-mode acceptEdits`` flag is a documented no-op (only
        # ``bypassPermissions`` / ``default`` apply via the flag); headless
        # then cancels tool calls that would prompt (``permission_cancelled``),
        # which kills /implement after shell tools. Always auto-approve.
        if not skip_permissions:
            self._warn_unsupported_once(
                "headless_always_approve",
                "Grok headless cannot prompt for tool permissions; using "
                "--always-approve. Set skip_permissions: true in config.yaml "
                "to silence this notice (recommended for autonomous Grok).",
                level="info",
            )
        return ["--always-approve"]

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        flags: List[str] = []
        if allowed_tools:
            mapped = self._map_tool_names(allowed_tools, side="allowed")
            if mapped:
                flags.extend(["--tools", ",".join(mapped)])
        if disallowed_tools:
            mapped = self._map_tool_names(disallowed_tools, side="disallowed")
            if mapped:
                flags.extend(["--disallowed-tools", ",".join(mapped)])
        return flags

    def _map_tool_names(self, tools: List[str], side: str) -> List[str]:
        """Map Claude/Koan tool names to Grok internal IDs (stable order)."""
        mapped: List[str] = []
        seen: set = set()
        for tool in tools:
            name = (tool or "").strip()
            if not name:
                continue
            if name in _GROK_TOOLS_DROP:
                self._warn_unsupported_once(
                    f"tool_drop:{name}",
                    f"tool {name!r} has no Grok Build allowlist id; "
                    f"omitted from {side} tools",
                    level="info",
                )
                continue
            grok_id = _GROK_TOOL_NAME_MAP.get(name)
            if grok_id is None:
                # Already a Grok id, or unknown — pass through with a notice
                # when it is not a known Grok target value.
                if name not in _GROK_TOOL_NAME_MAP.values():
                    self._warn_unsupported_once(
                        f"tool_passthrough:{name}",
                        f"tool {name!r} is not a known Claude→Grok mapping; "
                        "passing through as-is",
                        level="info",
                    )
                grok_id = name
            if grok_id not in seen:
                seen.add(grok_id)
                mapped.append(grok_id)
        return mapped

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

        Prompt args stay last for readability and for prompt-file rewrite.
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
