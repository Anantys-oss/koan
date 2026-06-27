"""Haze CLI provider implementation.

Haze (https://github.com/DenizOkcu/haze) is an interactive terminal agent.
Unlike Claude/Codex/Cline, Haze ships as an Ink/React TUI with **no native
non-interactive / print mode**: its CLI has no ``-p``, ``--model``,
``--output-format``, or ``--json`` flags.

To drive Haze headlessly from Kōan, :class:`HazeProvider` invokes a small
Node.js bridge script (:mod:`app.provider.haze_headless`) that imports Haze's
installed package internals and runs the agent core (``runAgentTurn``)
directly. The bridge reads the prompt from a temp file, emits Koan-compatible
JSONL progress events to stdout, and writes the final assistant text to a
``--last-message`` file.

Because Haze is model-agnostic (any OpenAI-compatible endpoint configured via
``/provider``), model selection, quota detection, and authentication are
handled at the backend level Haze is configured against. The provider uses
generic quota/auth patterns (like the Cline provider) since the underlying
backend is user-configured.

Configuration (config.yaml):
    cli_provider: "haze"

Environment:
    KOAN_CLI_PROVIDER=haze
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.provider.base import CLIProvider
from app.run_log import log_safe

# Path to the headless bridge script that drives Haze's agent core.
_BRIDGE_SCRIPT = str(Path(__file__).resolve().parent / "haze_headless.mjs")


# Generic quota patterns — Haze is multi-backend (OpenRouter, OpenAI, Z.ai,
# local endpoints, etc.), so we reuse the same generic patterns as the Cline
# provider. They cover the common rate-limit / quota-exhaustion phrasing.
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
    r"no\s+model\s+provider\s+configured",
]

_HAZE_AUTH_RE = re.compile("|".join(_HAZE_AUTH_PATTERNS), re.IGNORECASE)


def _resolve_haze_root() -> Optional[str]:
    """Resolve the installed @denizokcu/haze package root directory.

    The ``haze`` binary is a symlink into the global (or local) node_modules.
    Following that symlink yields the package directory containing ``dist/``
    and ``package.json``. An explicit ``KOAN_HAZE_PKG_PATH`` override takes
    precedence for non-standard installs.

    Returns ``None`` when Haze is not installed.
    """
    override = os.environ.get("KOAN_HAZE_PKG_PATH", "").strip()
    if override:
        p = Path(override).expanduser()
        if (p / "dist").is_dir():
            return str(p)
    binary = shutil.which("haze")
    if not binary:
        return None
    try:
        # Follow the symlink chain to the real file, then walk up to the
        # package root (bin/haze.js -> <pkg-root>/bin/haze.js).
        real = str(Path(binary).resolve())
        root = Path(real).parents[1]
        if (root / "dist").is_dir():
            return str(root)
    except OSError:
        return None
    return None


class HazeProvider(CLIProvider):
    """Haze CLI provider.

    Translates Kōan's generic command spec into an invocation of the
    :mod:`haze_headless` bridge, which runs Haze's agent core headlessly.

    Key differences from Claude CLI:
    - Binary: ``node`` + bridge script (Haze has no print mode)
    - Prompt: written to a temp file, passed via ``--prompt-file``
    - Model: no ``--model`` CLI flag; applied via settings override in the bridge
    - No per-tool allow/disallow flags
    - Output: JSONL events on stdout (bridge produces Koan-compatible events)
    - System prompt: prepended to user prompt (no native flag)
    - Permissions: Haze has no approval gates; concept N/A

    Configuration (config.yaml):
        cli_provider: "haze"

    Environment:
        KOAN_CLI_PROVIDER=haze
        KOAN_HAZE_PKG_PATH=/path/to/@denizokcu/haze  (override pkg location)
    """

    name = "haze"

    def binary(self) -> str:
        # The actual command is ``node <bridge>``, but is_available() checks
        # for the ``haze`` CLI. shell_command() returns the user-facing name.
        return "node"

    def shell_command(self) -> str:
        return "haze"

    def is_available(self) -> bool:
        # The bridge requires both ``node`` and the ``haze`` package.
        if shutil.which("node") is None:
            return False
        return _resolve_haze_root() is not None

    def supports_stdin_prompt_passing(self) -> bool:
        # The bridge reads the prompt from a dedicated --prompt-file, not
        # stdin. We handle prompt passing ourselves in build_command().
        return False

    def invocation_lock_name(self) -> str:
        # Haze sessions and settings live under ~/.haze per-cwd; concurrent
        # headless turns in the same workspace could interleave session
        # writes. Serialize to keep session state coherent.
        return "haze-cli"

    def build_prompt_args(self, prompt: str) -> List[str]:
        # Not used — HazeProvider.build_command() overrides the full assembly
        # and routes the prompt through --prompt-file.
        return []

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        # Haze exposes a fixed built-in toolset with no per-tool flags.
        if (allowed_tools or disallowed_tools):
            log_safe("debug", f"[{self.name}] per-tool allow/disallow is not supported by Haze; ignored")
        return []

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        # No --model CLI flag; passed to the bridge as --model which applies a
        # one-shot settings overlay. Fallback is not supported.
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        if fallback:
            log_safe("debug", f"[{self.name}] fallback model is not supported by Haze; ignored")
        return flags

    def supports_stream_json(self) -> bool:
        # The bridge emits Koan-compatible JSONL progress events.
        return True

    def build_output_args(self, fmt: str = "") -> List[str]:
        # The bridge always emits JSONL; no flag needed.
        return []

    def supports_last_message_file(self) -> bool:
        # The bridge writes the final assistant text to --last-message.
        return True

    def build_last_message_file_args(self, path: str) -> List[str]:
        if path:
            return ["--last-message", path]
        return []

    def add_last_message_file_args(self, cmd: List[str], path: str) -> List[str]:
        args = self.build_last_message_file_args(path)
        if not args or not cmd:
            return cmd
        return [*cmd, *args]

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        # Haze runs its tool-loop to completion (bounded by its own idle
        # timeout and loop-detection guardrails).
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        # MCP servers are configured inside Haze via /mcp (persisted in
        # ~/.haze/settings.json), not CLI flags.
        if configs:
            log_safe("debug", f"[{self.name}] MCP config is not supported via CLI flags; configure via /mcp inside Haze")
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        # Haze uses Markdown skills (~/​.haze/skills), not plugin dirs.
        if plugin_dirs:
            log_safe("debug", f"[{self.name}] plugin directories are not supported; ignored")
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        # Haze has no reasoning-effort CLI control.
        if effort:
            log_safe("debug", f"[{self.name}] reasoning effort control is not supported; ignored")
        return []

    def build_permission_args(self, skip_permissions: bool = False) -> List[str]:
        # Haze has no approval gates by design (expert-oriented tool). The
        # concept of skip_permissions does not map to a flag.
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
        """Build a complete ``node <bridge>`` command.

        The bridge takes the prompt via ``--prompt-file`` (written by
        :func:`app.cli_exec.prepare_prompt_file` is NOT used here because the
        bridge reads a file path, not stdin). Instead the prompt is placed in
        the command as a placeholder that Koan's streaming runner replaces —
        but since the streaming runner calls ``build_full_command`` and then
        ``add_last_message_file_args``, we keep the prompt inline using the
        bridge's ``--prompt``/``--prompt-file`` contract.

        To keep the prompt out of argv (no ``ps`` leak, no ``ARG_MAX``), we
        pass a placeholder path here. The actual prompt file is created and
        substituted by the streaming/execution layer. However, because the
        generic execution layer does not know the bridge's ``--prompt-file``
        contract, we write the prompt into the command as a single
        ``--prompt`` argument and rely on the bridge accepting it.

        In practice the high-level callers route through
        ``run_command``/``run_command_streaming`` which build the command and
        run it. We therefore pass the prompt via ``--prompt`` inline; for very
        large prompts the bridge could be extended to read stdin. Haze prompts
        are typically mission-sized (under ARG_MAX).
        """
        # System prompt: Haze has no native system-prompt flag. Prepend to the
        # user prompt (same fallback as Cline/Codex).
        if system_prompt_file:
            log_safe("debug", f"[{self.name}] system prompt file is not supported; falling back to inline system prompt")
        if system_prompt:
            prompt = system_prompt + "\n\n" + prompt

        haze_root = _resolve_haze_root()
        if not haze_root:
            # Return a best-effort command; the run will fail with a clear
            # error from is_available()/the bridge. This keeps build_command
            # total (never raises) like the other providers.
            haze_root = "<haze-not-found>"

        cmd: List[str] = [self.binary(), _BRIDGE_SCRIPT]
        cmd.extend(["--haze-root", haze_root])
        cmd.extend(["--prompt", prompt])
        cmd.extend(self.build_model_args(model, fallback))

        return cmd

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Check Haze backend quota via a minimal bridge probe.

        Sends a tiny prompt through the bridge to surface rate-limit or
        auth errors before a full mission is attempted. Haze is
        multi-backend, so generic patterns are used.

        NOTE: This probe consumes a small number of tokens on each call.
        """
        haze_root = _resolve_haze_root()
        if not haze_root:
            return False, "Haze package not found (install with: npm install -g @denizokcu/haze)"

        from app.cli_exec import run_cli

        cmd = [
            self.binary(),
            _BRIDGE_SCRIPT,
            "--haze-root",
            haze_root,
            "--prompt",
            "ok",
        ]

        try:
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
            log_safe("error", f"[{self.name}] quota probe error: {e}")
            return True, ""

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Haze backend quota/rate-limit failures.

        The bridge emits JSONL events on stdout and error text on stderr.
        Stderr is trusted for the full pattern set. JSONL stdout is scanned
        only for structured error/result events. Plain stdout lines are
        scanned only when the CLI failed AND the line resembles a provider
        error (mirrors the Cline/Codex approach to avoid false positives from
        assistant prose).
        """
        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""

        if _HAZE_QUOTA_RE.search(stderr_text):
            return True

        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Structured JSONL event path
            try:
                event = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                if self._plain_stdout_quota_line(stripped, exit_code):
                    return True
                continue

            if isinstance(event, dict) and self._event_has_quota_error(event):
                return True

        return False

    _STDOUT_ERROR_MARKERS = ("error", "rate", "limit", "quota", "http", "status", "api")

    def _plain_stdout_quota_line(self, line: str, exit_code: int) -> bool:
        """Check non-JSON stdout only when it resembles a provider error."""
        if exit_code == 0:
            return False
        if not self._line_has_error_marker(line, self._STDOUT_ERROR_MARKERS):
            return False
        return bool(_HAZE_QUOTA_RE.search(line))

    def _event_has_quota_error(self, event: Dict[str, Any]) -> bool:
        """Return True when a bridge JSONL event signals a quota failure."""
        etype = str(event.get("type") or "").lower()
        subtype = str(event.get("subtype") or "").lower()
        if etype == "result" and subtype == "error":
            return bool(_HAZE_QUOTA_RE.search(str(event.get("error") or "")))
        if etype == "retry":
            return bool(_HAZE_QUOTA_RE.search(str(event.get("error") or "")))
        if etype == "error":
            return bool(_HAZE_QUOTA_RE.search(str(event.get("message") or "")))
        return False

    def detect_auth_failure(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Haze authentication/provider failures.

        Haze auth failures may appear in stderr (bridge errors) or stdout
        (JSONL events). Includes Haze's own "No model provider configured"
        message so Kōan pauses for configuration instead of failing.
        """
        if exit_code == 0:
            return False

        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""

        if _HAZE_AUTH_RE.search(stderr_text):
            return True

        for line in stdout_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                if _HAZE_AUTH_RE.search(stripped):
                    return True
                continue
            if isinstance(event, dict):
                etype = str(event.get("type") or "").lower()
                subtype = str(event.get("subtype") or "").lower()
                if etype == "result" and subtype == "error":
                    if _HAZE_AUTH_RE.search(str(event.get("error") or "")):
                        return True
                if etype == "assistant":
                    text = str(event.get("text") or "")
                    if _HAZE_AUTH_RE.search(text):
                        return True

        return False
