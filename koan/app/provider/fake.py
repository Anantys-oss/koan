"""Fake CLI provider — deterministic, fail-closed test/dev stub.

⚠️  **TEST/DEV ONLY.** This provider never invokes a real LLM. It exists so
that deterministic, offline end-to-end tests of the skill pipeline can select a
CLI flavor like any other provider without burning API quota or depending on a
network.

Fail-closed by construction: instantiating :class:`FakeProvider` raises
:class:`FakeProviderNotAllowed` unless ``KOAN_ALLOW_FAKE_PROVIDER=1`` (or an
equivalent truthy value) is set in the environment. Every registry entry point
(``get_provider``/``get_provider_by_name``/``get_provider_for_role``/
``get_fallback_provider``) constructs the provider through ``_PROVIDERS[name]()``,
so the guard fires on every selection path. Kōan therefore refuses to run —
loudly, with an actionable message — rather than silently falling back to a real
provider when ``fake`` is selected in a production-like environment without the
explicit opt-in.

Response routing (canned/scripted responses) is intentionally out of scope for
this foundation; ``build_command`` produces a harmless no-op invocation whose
output is empty. Smart routing lands in a follow-up issue.
"""

import os
from typing import List, Optional

from app.provider.base import CLIProvider

# Environment flag that must be truthy for the fake provider to be constructed.
# Kept as a module constant so tests and docs reference one source of truth.
ALLOW_ENV = "KOAN_ALLOW_FAKE_PROVIDER"

# Truthy spellings accepted for the allow flag. Mirrors common env-flag parsing
# so operators are not surprised by ``true``/``yes`` not working.
_TRUTHY = {"1", "true", "yes", "on"}


class FakeProviderNotAllowed(RuntimeError):
    """Raised when ``fake`` is selected without ``KOAN_ALLOW_FAKE_PROVIDER=1``.

    A ``RuntimeError`` subclass so existing broad ``except Exception`` guards
    (e.g. the provider-classification fallbacks in ``quota_handler`` and
    ``cli_errors``) degrade conservatively, while callers that want to detect
    this specific refusal can catch the narrow type.
    """


def fake_provider_allowed() -> bool:
    """True when the environment explicitly opts into the fake provider."""
    return os.environ.get(ALLOW_ENV, "").strip().lower() in _TRUTHY


class FakeProvider(CLIProvider):
    """Fail-closed, no-op CLI provider for deterministic tests and local dev.

    Configuration (test/dev only)::

        cli_provider: "fake"          # config.yaml
        KOAN_CLI_PROVIDER=fake        # env
        cli:                          # per-role (config.yaml)
            default:
                mission: fake

    In every case ``KOAN_ALLOW_FAKE_PROVIDER=1`` must also be set, or
    construction raises :class:`FakeProviderNotAllowed`.
    """

    name = "fake"

    def __init__(self, binary_path: str = ""):
        """Construct the fake provider, refusing unless explicitly allowed.

        The guard lives in the constructor (not at selection time) so that
        *every* path that resolves a provider instance — the global singleton,
        per-role selection, the fallback provider, and name-based lookup used by
        post-run error classification — fails closed identically.
        """
        super().__init__(binary_path)
        if not fake_provider_allowed():
            raise FakeProviderNotAllowed(
                "The 'fake' CLI provider was selected but "
                f"{ALLOW_ENV}=1 is not set. This provider is for tests/dev only "
                "and never invokes a real LLM. Refusing to run so Kōan does not "
                "silently fall back to a real provider. To enable it (test/dev "
                f"only), set {ALLOW_ENV}=1; otherwise select a real provider "
                "(claude/codex/copilot/cline/haze/ollama-launch) via "
                "KOAN_CLI_PROVIDER or the cli_provider config key."
            )

    def binary(self) -> str:
        """Return a harmless no-op binary, never a real Claude CLI.

        Defaults to ``true`` (the POSIX no-op that exits 0 with empty output) so
        that ``run_command``/``run_command_streaming`` can invoke the built
        command without crashing and without pretending to be a real provider.
        A per-instance ``cli.<role>: fake:/path`` override is honored for tests
        that want to point at a scripted stub.
        """
        if self._binary_override:
            return self._resolve_binary_path(self._binary_override)
        return "true"

    def is_available(self) -> bool:
        """Always available: the fake provider needs no external binary.

        Returning ``True`` unconditionally (rather than probing ``shutil.which``)
        keeps the provider selectable in minimal test environments while never
        claiming a real Claude CLI is present.
        """
        return True

    # --- Stub flag builders -------------------------------------------------
    # The command produced is intentionally a no-op; inputs are accepted and
    # dropped. Response routing lands in a follow-up issue.

    def build_prompt_args(self, prompt: str) -> List[str]:
        return []

    def supports_stdin_prompt_passing(self) -> bool:
        # No ``-p`` in the built command, so there is nothing to move to stdin.
        return False

    def build_tool_args(
        self,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        return []

    def build_model_args(self, model: str = "", fallback: str = "") -> List[str]:
        return []

    def build_output_args(self, fmt: str = "") -> List[str]:
        return []

    def build_max_turns_args(self, max_turns: int = 0) -> List[str]:
        return []

    def build_mcp_args(self, configs: Optional[List[str]] = None) -> List[str]:
        return []

    def has_api_quota(self) -> bool:
        """The fake provider consumes no metered API quota — disable budgeting."""
        return False
