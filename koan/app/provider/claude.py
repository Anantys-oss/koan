"""Claude Code CLI provider implementation."""

import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.provider.base import CLIProvider

_ROOT_SKIP_PERMISSIONS_WARNED = False  # Module-level guard to warn once per process
_READ_ONLY_SKIP_PERMISSIONS_WARNED = False  # Same, for the read-only override

# Filename of the generated agent-settings payload that installs the read-only
# shell gate. Content is deterministic, so the file is written once per uid and
# reused rather than being a per-invocation temp file.
_REVIEW_SHELL_SETTINGS_NAME = "review-shell-guard.settings.json"


def _ensure_review_shell_settings() -> str:
    """Write (idempotently) the ``--settings`` payload and return its path.

    Rewritten whenever the content differs so an upgraded Kōan -- new
    interpreter path, relocated checkout -- does not keep pointing a stale hook
    at a script that has moved.
    """
    import json

    from app.review_bash_guard import hook_settings
    from app.utils import koan_tmp_dir

    payload = json.dumps(hook_settings(), indent=2, sort_keys=True)
    path = os.path.join(koan_tmp_dir(), _REVIEW_SHELL_SETTINGS_NAME)
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                if fh.read() == payload:
                    return path
    except OSError:
        pass  # unreadable -> fall through and rewrite

    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


class ClaudeProvider(CLIProvider):
    """Claude Code CLI provider."""

    name = "claude"

    def binary(self) -> str:
        # Per-role override (cli: flavor:path) wins when this instance was
        # constructed with one — see provider.get_provider_for_role().
        if self._binary_override:
            return self._resolve_binary_path(self._binary_override)
        # Otherwise the global custom binary, then the bare command. Both go
        # through the shared resolver so KOAN_ROOT-relative paths stay portable.
        raw = os.environ.get("KOAN_CLAUDE_CLI_PATH", "").strip()
        if not raw:
            return "claude"
        return self._resolve_binary_path(raw)

    def custom_binary_name(self) -> str:
        # Per-role path (cli.review_mode: claude:/path) wins; otherwise the
        # global KOAN_CLAUDE_CLI_PATH is the second override channel — both
        # are real user-pinned binaries worth surfacing in the signature.
        # Basename extraction + the flavor-collapse guard are shared with the
        # base via _override_basename so the rule lives in one place.
        raw = (self._binary_override or "").strip() or os.environ.get(
            "KOAN_CLAUDE_CLI_PATH", ""
        ).strip()
        return self._override_basename(raw) if raw else ""

    def supports_session_resume(self) -> bool:
        return True

    def build_resume_args(self, session_id: str) -> List[str]:
        if session_id:
            return ["--resume", session_id]
        return []

    def supports_stream_json(self) -> bool:
        return True

    def supports_tool_denial(self) -> bool:
        # --disallowedTools removes a tool from the model's context entirely.
        # Verified on CLI 2.1.234: a Bash call under --disallowedTools Bash is
        # blocked, whereas --allowedTools Read,Glob,Grep leaves Bash working.
        return True

    def build_permission_args(
        self, skip_permissions: bool = False, read_only: bool = False,
    ) -> List[str]:
        if read_only:
            # --dangerously-skip-permissions bypasses ALL permission checks,
            # which would defeat the --disallowedTools denial that makes a
            # read-only role read-only. A read-only invocation therefore never
            # honors skip_permissions, regardless of global config.
            if skip_permissions:
                global _READ_ONLY_SKIP_PERMISSIONS_WARNED
                if not _READ_ONLY_SKIP_PERMISSIONS_WARNED:
                    _READ_ONLY_SKIP_PERMISSIONS_WARNED = True
                    print(
                        f"[{self.name}] skip_permissions: true is not honored for "
                        "read-only roles (it would bypass the tool denial that "
                        "keeps reviews read-only); continuing without the flag.",
                        file=sys.stderr,
                    )
            return []
        if not skip_permissions:
            return []
        # The Claude CLI refuses --dangerously-skip-permissions under
        # root/sudo (a security boundary), so drop the flag and warn the
        # operator once that skip_permissions: true is not being honored.
        # Root handling lives here, not in config.get_skip_permissions():
        # the restriction is Claude-CLI-specific, and other providers must
        # keep honoring the setting when running as root.
        if os.geteuid() == 0:
            global _ROOT_SKIP_PERMISSIONS_WARNED
            if not _ROOT_SKIP_PERMISSIONS_WARNED:
                _ROOT_SKIP_PERMISSIONS_WARNED = True
                print(
                    f"[{self.name}] skip_permissions: true is ignored when "
                    "running as root (the Claude CLI refuses "
                    "--dangerously-skip-permissions under root/sudo); "
                    "continuing without the flag.",
                    file=sys.stderr,
                )
            return []
        return ["--dangerously-skip-permissions"]

    def build_system_prompt_args(self, system_prompt: str) -> List[str]:
        if system_prompt:
            return ["--append-system-prompt", system_prompt]
        return []

    def supports_system_prompt_file(self) -> bool:
        # Claude Code CLI supports --append-system-prompt-file in print mode
        # (-p), which is the only mode Kōan uses.  See
        # docs/providers/claude-cli-commands-official.md.
        return True

    def build_system_prompt_file_args(self, path: str) -> List[str]:
        if path:
            return ["--append-system-prompt-file", path]
        return []

    def build_prompt_args(self, prompt: str) -> List[str]:
        return ["-p", prompt]

    def supports_tool_restriction(self) -> bool:
        # --tools is a POSITIVE allowlist: "Specify the list of available tools
        # from the built-in set." Measured on CLI 2.1.235 -- under
        # --tools "Read,Glob,Grep" a Bash call and a Write call both leave no
        # filesystem trace. Checked against the filesystem rather than the
        # model's self-report, which disagreed with itself across runs.
        return True

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        restrict_tools: Optional[Sequence[str]] = None,
    ) -> List[str]:
        flags: List[str] = []
        if restrict_tools is not None:
            # `None` means "no restriction"; an empty sequence means "no built-in
            # tools at all" and must stay expressible -- collapsing the two is
            # exactly how a positive allowlist turns back into a fail-open.
            flags.extend(["--tools", ",".join(restrict_tools)])
        if allowed_tools:
            flags.extend(["--allowedTools", ",".join(allowed_tools)])
        if disallowed_tools:
            flags.extend(["--disallowedTools", ",".join(disallowed_tools)])
        return flags

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        flags: List[str] = []
        if model:
            flags.extend(["--model", model])
        if fallback and fallback != model:
            flags.extend(["--fallback-model", fallback])
        return flags

    def build_output_args(self, fmt: str = "") -> List[str]:
        if not fmt:
            return []
        # Claude CLI requires --verbose alongside --output-format stream-json
        # in print mode; the events are otherwise suppressed.
        if fmt == "stream-json":
            return ["--output-format", fmt, "--verbose"]
        return ["--output-format", fmt]

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        if max_turns > 0:
            return ["--max-turns", str(max_turns)]
        return []

    # Valid effort levels for Claude Code CLI --effort flag.
    _EFFORT_LEVELS = {"low", "medium", "high", "max"}

    def build_effort_args(self, effort: str = "") -> List[str]:
        if effort and effort in self._EFFORT_LEVELS:
            return ["--effort", effort]
        return []

    def build_project_context_args(self, project_context: bool = True) -> List[str]:
        """Suppress project CLAUDE.md / .claude skills when *project_context* is False.

        Claude Code loads user / project / local setting sources by default.
        ``--setting-sources user`` keeps user-level prefs but omits project
        and local sources so KOAN_ROOT runtime sessions do not auto-load
        contributor tooling (issue #2379).
        """
        if project_context:
            return []
        return ["--setting-sources", "user"]

    def build_thinking_args(
        self, enabled: bool = False, budget_tokens: int = 0,
    ) -> List[str]:
        if not enabled:
            return []
        # Claude Code CLI activates extended thinking via --effort max.
        # budget_tokens is not directly supported by the CLI — the API-level
        # token budget is managed by the Claude backend, not the CLI flag.
        return ["--effort", "max"]

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        if not configs:
            return []
        flags = ["--mcp-config"]
        flags.extend(configs)
        return flags

    def build_agent_settings_args(self, read_only: bool = False) -> List[str]:
        """Install the read-only shell gate via ``--settings``.

        ``READ_ONLY_TOOLS`` grants ``Bash``, but ``Bash`` is never added to
        ``--allowedTools``; this hook returning ``allow`` is the only thing that
        permits a command. If the file cannot be written we return no flag,
        which leaves Bash un-pre-approved and therefore denied -- the gate fails
        to "no shell", never to an unguarded one.

        The payload is deterministic (an interpreter path and this repo's guard
        script), so it is written once to a stable path under ``koan_tmp_dir()``
        rather than a per-invocation temp file with a cleanup lifecycle to get
        wrong. It is never written to ``~/.claude/settings.json``: Kōan does not
        mutate the operator's global agent configuration.
        """
        if not read_only:
            return []
        try:
            path = _ensure_review_shell_settings()
        except OSError as exc:
            print(
                f"[{self.name}] could not install the read-only shell gate "
                f"({exc}); continuing without a shell.",
                file=sys.stderr,
            )
            return []
        return ["--settings", path]

    def build_mcp_isolation_args(self, read_only: bool = False) -> List[str]:
        """Emit ``--strict-mcp-config`` for a read-only invocation.

        ``--tools`` restricts the BUILT-IN set only. Measured on CLI 2.1.235:
        under ``--tools "Read,Glob,Grep"`` every MCP tool from the operator's
        user-level configuration was still present -- including write-capable
        ones -- so the positive allowlist is not total without this. With
        ``--strict-mcp-config`` and no ``--mcp-config`` the exposed MCP tool
        count drops to zero.
        """
        return ["--strict-mcp-config"] if read_only else []

    def detect_quota_exhaustion(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        exit_code: int = 0,
    ) -> bool:
        """Detect Claude/Anthropic quota failures.

        Preserve the legacy split behavior: stderr is trusted for all quota
        patterns, while stdout only matches strict provider error phrases so
        normal assistant discussion of rate limits does not pause Koan.
        """
        from app.quota_handler import (
            _QUOTA_RE,
            _rate_limit_exhausted,
            _strict_quota_match,
        )

        return (
            bool(_QUOTA_RE.search(stderr_text or ""))
            or _rate_limit_exhausted(stderr_text or "")
            or _strict_quota_match(stdout_text or "")
        )

    def build_plugin_args(self, plugin_dirs: Optional[List[str]] = None) -> List[str]:
        if not plugin_dirs:
            return []
        flags: List[str] = []
        for d in plugin_dirs:
            flags.extend(["--plugin-dir", d])
        return flags

    def get_session_data(self, project_path: str) -> Optional[Dict[str, Any]]:
        from app.provider.claude_session import collect_jsonl_tokens
        return collect_jsonl_tokens(project_path)

    def check_quota_available(self, project_path: str, timeout: int = 15) -> Tuple[bool, str]:
        """Check Claude API quota availability.

        Note: ``claude usage`` is not a real subcommand — it would be
        interpreted as a prompt and hang.  Instead, we always return
        True and rely on quota_handler.py to detect exhaustion from
        the actual CLI output after each run.
        """
        # No lightweight zero-cost probe exists in the Claude CLI.
        # Quota exhaustion is detected post-run by quota_handler.py.
        return True, ""
