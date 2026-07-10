"""Haze CLI provider implementation."""

import shutil
from typing import List, Optional, Sequence, Tuple

from app.provider.base import CLIProvider
from app.run_log import log_safe

# Features the agent loop passes on every invocation (tool lists, max turns)
# would flood the journal if warned per call, but the durable contract says
# unsupported inputs are never silently accepted — so those warn once per
# process (same pattern as Claude's root permission warning). Operator-
# configured features (MCP, plugins, effort, fallback model, resume,
# system-prompt file) warn on every call, matching the Cline precedent.
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

    def rewrite_prompt_for_stdin(
        self,
        cmd: Sequence[str],
        stdin_marker: str,
    ) -> Tuple[List[str], Optional[str]]:
        """Move the prompt from argv to stdin by REMOVING ``-p <prompt>``.

        Haze reads the prompt from stdin only when ``-p`` is absent, so the
        base marker substitution (``-p @stdin``) would send the marker as the
        literal prompt. *stdin_marker* is therefore unused here.
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
            log_safe(
                "warning",
                f"[{self.name}] fallback model is not supported by haze; ignored",
            )
        return ["-m", model] if model else []

    def build_output_args(self, fmt: str = "") -> List[str]:
        # stream-json is the primary harness mode (haze >= 0.7.0); plain json
        # (single terminal envelope) is used by the quota probe.
        if fmt in {"json", "stream-json"}:
            return ["--output", fmt]
        return []

    def _warn_unsupported_once(self, feature: str, message: str) -> None:
        if feature in _WARNED_UNSUPPORTED:
            return
        _WARNED_UNSUPPORTED.add(feature)
        log_safe("warning", f"[{self.name}] {message}")

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
            )
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns:
            self._warn_unsupported_once(
                "max_turns",
                "max turns is not supported by haze; one-shot runs to completion",
            )
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if configs:
            log_safe(
                "warning",
                f"[{self.name}] MCP config is not supported via CLI flags; "
                "configure servers inside haze (/mcp) instead",
            )
        return []

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if plugin_dirs:
            log_safe(
                "warning",
                f"[{self.name}] plugin directories are not supported; ignored",
            )
        return []

    def build_effort_args(self, effort: str = "") -> List[str]:
        if effort:
            log_safe(
                "warning",
                f"[{self.name}] reasoning effort control is not supported; ignored",
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
            log_safe(
                "warning",
                f"[{self.name}] system prompt file is not supported; "
                "falling back to inline system prompt",
            )
        if system_prompt:
            # No dedicated system-prompt flag: prepend (base fallback shape).
            prompt = system_prompt + "\n\n" + prompt
        if resume_session_id:
            log_safe(
                "warning",
                f"[{self.name}] session resume is not supported "
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
