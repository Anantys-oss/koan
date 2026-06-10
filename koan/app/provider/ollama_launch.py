"""Ollama Launch provider — delegates to 'ollama launch claude'.

Uses Ollama v0.16.0+ ``ollama launch claude`` integration to run Claude
Code CLI through a local Ollama server.  This is simpler than manual
env-var configuration: Ollama handles ``ANTHROPIC_BASE_URL`` setup and
server lifecycle internally.

Command structure::

    ollama launch claude --model <model> -- -p <prompt> --allowedTools ...

Everything before ``--`` is Ollama's responsibility (model selection,
server management).  Everything after ``--`` is passed through to the
Claude Code CLI verbatim.
"""

import os
import re
import shutil
import sys
from typing import Dict, List, Optional, Tuple

from app.provider.claude import ClaudeProvider


class OllamaLaunchProvider(ClaudeProvider):
    """Provider that uses ``ollama launch claude`` to run Claude Code.

    Advantages over manual OllamaClaudeProvider:
    - No manual env-var setup (ANTHROPIC_BASE_URL, etc.)
    - Ollama auto-starts the server if needed
    - Native integration maintained by Ollama upstream
    - Model validated by Ollama before launch

    Because everything after ``--`` is forwarded to the Claude Code CLI,
    this provider inherits from :class:`ClaudeProvider` and reuses all
    Claude-specific flag builders (permissions, system prompts, session
    resume, streaming, effort, thinking, quota detection, etc.).

    Configuration (config.yaml)::

        cli_provider: "ollama-launch"
        ollama_launch:
            model: "qwen2.5-coder:14b"

    Or via environment::

        KOAN_CLI_PROVIDER=ollama-launch
        KOAN_OLLAMA_LAUNCH_MODEL=qwen2.5-coder:14b
    """

    name = "ollama-launch"

    def _get_config(self) -> dict:
        """Get ollama_launch config section from config.yaml."""
        try:
            from app.utils import load_config
            config = load_config()
            return config.get("ollama_launch", {})
        except Exception as e:
            print(f"[ollama-launch] config loading failed: {e}", file=sys.stderr)
            return {}

    def _get_setting(self, env_key: str, config_key: str, default: str = "") -> str:
        """Resolve a setting: env var > config.yaml > default."""
        env_val = os.environ.get(env_key, "")
        if env_val:
            return env_val
        return self._get_config().get(config_key, default)

    def _get_default_model(self) -> str:
        return self._get_setting(
            "KOAN_OLLAMA_LAUNCH_MODEL", "model", ""
        )

    def binary(self) -> str:
        return "ollama"

    def shell_command(self) -> str:
        return "ollama launch claude"

    def is_available(self) -> bool:
        """Check that ollama binary exists and is v0.16.0+."""
        return shutil.which("ollama") is not None

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        # Model is handled by ollama --model flag (before --), not Claude --model.
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
        """Build: ollama launch claude --model X -- <claude-flags>.

        The ``--`` separator divides Ollama args from Claude Code args.
        Everything after ``--`` uses the same flag builders as
        :class:`ClaudeProvider` so feature parity is maintained
        (permissions, system prompts, resume, output format, max turns,
        MCP, plugins, effort).
        """
        # Ollama part: binary + launch subcommand + model
        cmd = ["ollama", "launch", "claude"]
        effective_model = model or self._get_default_model()
        if effective_model:
            cmd.extend(["--model", effective_model])

        # Separator between ollama args and Claude Code args
        cmd.append("--")

        # Claude Code part — same ordering as base CLIProvider.build_command()
        if resume_session_id and self.supports_session_resume():
            cmd.extend(self.build_resume_args(resume_session_id))
        cmd.extend(self.build_permission_args(skip_permissions))

        # System prompt: file mode takes precedence over inline content.
        if system_prompt_file and self.supports_system_prompt_file():
            cmd.extend(self.build_system_prompt_file_args(system_prompt_file))
        elif system_prompt:
            sys_args = self.build_system_prompt_args(system_prompt)
            if sys_args:
                cmd.extend(sys_args)
            else:
                prompt = system_prompt + "\n\n" + prompt

        cmd.extend(self.build_prompt_args(prompt))
        cmd.extend(self.build_tool_args(allowed_tools, disallowed_tools))
        cmd.extend(self.build_model_args(model, fallback))
        cmd.extend(self.build_output_args(output_format))
        cmd.extend(self.build_max_turns_args(max_turns))
        cmd.extend(self.build_mcp_args(mcp_configs))
        cmd.extend(self.build_plugin_args(plugin_dirs))
        cmd.extend(self.build_effort_args(effort))
        return cmd

    def get_env(self) -> Dict[str, str]:
        """No extra env vars needed — ollama handles everything."""
        return {}

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Local models have no API quota — always available."""
        return True, ""

    _OLLAMA_QUOTA_PATTERNS = (
        r"Request rejected \(429\)",
        r"reached your session usage limit",
        r"ollama\.com/upgrade",
    )

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Ollama-specific quota failures, then fall back to Claude patterns.

        Stderr is trusted unconditionally. Stdout is only scanned when
        exit_code != 0 to avoid false-pausing successful runs whose
        transcript quotes Ollama quota text.
        """
        stderr_text = stderr_text or ""
        stdout_text = stdout_text or ""
        for pattern in self._OLLAMA_QUOTA_PATTERNS:
            if re.search(pattern, stderr_text, re.IGNORECASE):
                return True
            if exit_code != 0 and re.search(pattern, stdout_text, re.IGNORECASE):
                return True
        return super().detect_quota_exhaustion(stdout_text, stderr_text, exit_code)

    def has_api_quota(self) -> bool:
        """Ollama launch uses local or cloud Ollama — no metered API quota."""
        return False
