"""Haze CLI provider implementation."""

import shutil

from app.provider.base import CLIProvider


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
