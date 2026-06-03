"""OpenAI Codex CLI provider implementation."""

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, List, Optional, Sequence, Tuple

from app.provider.base import CLIProvider, PROVIDER_ERROR_EVENT_TYPES


_CODEX_QUOTA_PATTERNS = [
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

_CODEX_QUOTA_RE = re.compile("|".join(_CODEX_QUOTA_PATTERNS), re.IGNORECASE)

_CODEX_ERROR_KEYS = {
    "code",
    "error",
    "error_code",
    "error_type",
    "message",
    "status",
    "status_code",
    "type",
}

_CODEX_AUTH_PATTERNS = [
    r"\b401\s+Unauthorized\b",
    r"unexpected\s+status\s+401",
    r"access\s+token\s+could\s+not\s+be\s+refreshed",
    r"refresh\s+token\s+was\s+already\s+used",
]

_CODEX_AUTH_RE = re.compile("|".join(_CODEX_AUTH_PATTERNS), re.IGNORECASE)


class CodexProvider(CLIProvider):
    """OpenAI Codex CLI provider.

    Translates Kōan's generic command spec into Codex CLI equivalents.
    Uses ``codex exec`` for non-interactive (scripted/autonomous) execution.

    Key differences from Claude CLI:
    - Binary: 'codex'
    - Non-interactive: 'codex exec "prompt"' (prompt is positional)
    - Tool control: No per-tool allow/disallow flags; uses sandbox policies
    - Model: --model flag (same as Claude)
    - No --fallback-model equivalent
    - No --append-system-prompt (falls back to prepend via base class)
    - No --max-turns (runs to completion)
    - Output: --json flag for JSONL events (not --output-format)
    - Permissions: --dangerously-bypass-approvals-and-sandbox for full access
    - MCP: configured via config.toml, not CLI flags

    Configuration (config.yaml):
        cli_provider: "codex"

    Environment:
        KOAN_CLI_PROVIDER=codex
    """

    name = "codex"

    def binary(self) -> str:
        return "codex"

    def is_available(self) -> bool:
        return shutil.which("codex") is not None

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        # Codex equivalent: bypass approvals and sandbox entirely.
        #
        # When skip_permissions=False we use --sandbox workspace-write
        # (replaces deprecated --full-auto) because Kōan runs headless
        # (codex exec) where interactive approval prompts would block
        # forever.  workspace-write is the least-privilege sandbox mode
        # that still works unattended.
        #
        # TODO: for read-only contexts (chat, review mode) a future
        # enhancement could pass --sandbox read-only instead.
        if skip_permissions:
            return ["--dangerously-bypass-approvals-and-sandbox"]
        return ["--sandbox", "workspace-write"]

    def build_prompt_args(self, prompt: str) -> List[str]:
        # Codex non-interactive mode: codex exec "prompt"
        return ["exec", prompt]

    def rewrite_prompt_for_stdin(
        self,
        cmd: Sequence[str],
        stdin_marker: str,
    ) -> Tuple[List[str], Optional[str]]:
        cmd_list = list(cmd)
        if not (
            len(cmd_list) >= 3
            and os.path.basename(cmd_list[0]) == self.binary()
            and cmd_list[1] == "exec"
            and cmd_list[-1] != "-"
            and not cmd_list[-1].startswith("-")
        ):
            return cmd_list, None
        prompt = cmd_list[-1]
        rewritten = cmd_list.copy()
        rewritten[-1] = "-"
        return rewritten, prompt

    def invocation_lock_name(self) -> str:
        return "codex-cli"

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        # Codex CLI does not support per-tool allow/disallow flags.
        # Tool access is controlled via sandbox policies (--sandbox flag)
        # and approval modes (--ask-for-approval).
        # We silently ignore tool specifications — the sandbox policy
        # set via build_permission_args controls what Codex can do.
        return []

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        # Codex has no --fallback-model; ignored silently
        return flags

    def supports_stream_json(self) -> bool:
        # Codex ``exec --json`` emits JSONL progress events.  Kōan asks
        # for this only from run_command_streaming(), where those events
        # are summarized back into human-readable progress lines.
        return True

    def build_output_args(self, fmt: str = "") -> List[str]:
        # Codex uses --json for machine-readable JSONL output.  We keep
        # plain text as the default and opt into JSONL only for callers
        # that explicitly request a streaming/event format.
        if fmt in {"json", "stream-json"}:
            return ["--json"]
        return []

    def supports_last_message_file(self) -> bool:
        return True

    def build_last_message_file_args(self, path: str) -> List[str]:
        return ["--output-last-message", path]

    def add_last_message_file_args(self, cmd: List[str], path: str) -> List[str]:
        args = self.build_last_message_file_args(path)
        if not args or not cmd:
            return cmd
        return [*cmd[:-1], *args, cmd[-1]]

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        # Codex CLI does not support --max-turns.
        # codex exec runs to completion.
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        # Codex configures MCP servers via config.toml, not CLI flags.
        # Users should configure MCP in ~/.codex/config.toml [mcp_servers].
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        # Codex uses skills (stored in ~/.codex/skills/ or .codex/skills/),
        # not plugin directories. Silently ignored.
        return []

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Codex/OpenAI quota failures without scanning tool output.

        Codex JSONL stdout can contain command ``aggregated_output`` with large
        source snippets. Scanning that text with broad quota regexes causes
        false positives. Trust stderr, explicit provider error events, and only
        plain stdout lines that look like direct Codex/OpenAI errors.
        """
        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""

        if _CODEX_QUOTA_RE.search(stderr_text):
            return True

        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                if self._plain_stdout_quota_line(stripped, exit_code):
                    return True
                continue

            if isinstance(event, dict) and self._event_has_quota_error(event):
                return True

        return False

    _STDOUT_ERROR_MARKERS = ("error", "openai", "codex", "api")

    def _plain_stdout_quota_line(self, line: str, exit_code: int) -> bool:
        """Check non-JSON stdout only when it resembles a provider error."""
        if exit_code == 0:
            return False
        if not self._line_has_error_marker(line, self._STDOUT_ERROR_MARKERS):
            return False
        return bool(_CODEX_QUOTA_RE.search(line))

    def _event_has_quota_error(self, event: dict[str, Any]) -> bool:
        event_type = str(event.get("type") or "").lower()
        if event_type not in PROVIDER_ERROR_EVENT_TYPES:
            return False
        return _CODEX_QUOTA_RE.search(self._error_event_text(event)) is not None

    def _error_event_text(self, value: Any) -> str:
        """Extract only provider-error fields, never command output fields."""
        parts: list[str] = []

        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"aggregated_output", "command", "item", "items", "output"}:
                    continue
                if key in _CODEX_ERROR_KEYS or isinstance(item, dict):
                    parts.append(self._error_event_text(item))
                elif isinstance(item, (str, int, float)):
                    parts.append(str(item))
        elif isinstance(value, list):
            parts.extend(
                self._error_event_text(item)
                for item in value
                if isinstance(item, dict)
            )
        elif isinstance(value, (str, int, float)):
            parts.append(str(value))

        return "\n".join(p for p in parts if p)

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Codex authentication/session failures.

        Codex may emit auth failures as JSONL stdout events during stream
        reconnects rather than plain stderr. For JSON events only direct
        provider error fields are inspected, so command output cannot create
        false positives.

        Non-JSON stdout lines, however, are scanned raw with ``_CODEX_AUTH_RE``.
        Callers must pre-filter stdout to trusted runtime lines before passing
        it here — ``run._cli_runtime_auth_signal`` does this (only ``[cli]``
        summaries, ``CLI invocation failed:`` lines, and structured error
        events reach us). The exit-code path in ``cli_errors`` passes unfiltered
        stdout, but that runs only after ``classify_cli_error``'s generic
        ``_AUTH_RE`` check, so the marginal false-positive surface is limited to
        Codex-specific phrases (e.g. refresh-token reuse) that benign logs are
        very unlikely to contain.
        """
        if exit_code == 0:
            return False

        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""

        if _CODEX_AUTH_RE.search(stderr_text):
            return True

        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                if _CODEX_AUTH_RE.search(stripped):
                    return True
                continue

            if isinstance(event, dict) and _CODEX_AUTH_RE.search(
                self._error_event_text(event)
            ):
                return True

        return False

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
        """Build a complete Codex CLI command.

        Codex exec command structure::

            codex exec [exec-flags] "prompt"

        Permission flags (``--sandbox workspace-write``,
        ``--dangerously-bypass-approvals-and-sandbox``) and ``--model``
        are ``exec`` subcommand flags in current Codex CLI (>= 0.1),
        so they must come *after* the ``exec`` keyword.  The prompt is
        the final positional argument.
        """
        # Handle system prompt: Codex has no --append-system-prompt or
        # file-mode equivalent, so prepend to user prompt (base class
        # fallback behavior). system_prompt_file is silently ignored —
        # supports_system_prompt_file() returns False on this provider.
        if system_prompt:
            prompt = system_prompt + "\n\n" + prompt

        cmd = [self.binary(), "exec"]

        # Exec-level flags (permission, model) come after 'exec'
        cmd.extend(self.build_permission_args(skip_permissions))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))

        # Prompt is the final positional argument
        cmd.append(prompt)

        return cmd

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Check Codex API quota via a minimal exec probe.

        Sends a tiny prompt ("ok") to surface rate-limit or subscription
        errors before a full mission is attempted.

        NOTE: Unlike Claude's zero-cost ``claude usage``, this probe
        consumes a small number of tokens on each call.  Kōan's main
        loop calls this before every mission, so the cost is real but
        negligible compared to the mission itself.
        """
        cmd = [self.binary(), "exec", "--sandbox", "workspace-write", "ok"]

        try:
            from app.cli_exec import run_cli

            result = run_cli(
                cmd,
                provider=self,
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
            print(f"[codex] quota probe error: {e}", file=sys.stderr)
            return True, ""
