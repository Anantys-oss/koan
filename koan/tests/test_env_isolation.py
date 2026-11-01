"""Regression: the autouse ``isolate_env`` fixture must strip host CLI binary pins.

On deployment hosts ``KOAN_CLAUDE_CLI_PATH`` is set (e.g. a gateway shim
such as ``~/.local/bin/ai-claude``) and ``ClaudeProvider.binary()`` honors
it. When the autouse ``isolate_env`` fixture (conftest.py) only cleared
``CLI_PROVIDER``/``KOAN_CLI_PROVIDER``, the host value leaked through and
every test asserting the default binary ``"claude"`` failed.

A module-scope fixture sets the variable *before* the function-scope
``isolate_env`` runs, mimicking a real host. The test then observes the
provider's behavior: if the fixture isolates, the provider sees the
default; if it does not, the host path wins.
"""

import os

import pytest

_HOST_PATH = "/nonexistent/koan-host-shim/ai-claude"


@pytest.fixture(scope="module", autouse=True)
def _simulate_host_cli_path():
    old = os.environ.get("KOAN_CLAUDE_CLI_PATH")
    os.environ["KOAN_CLAUDE_CLI_PATH"] = _HOST_PATH
    yield
    if old is None:
        os.environ.pop("KOAN_CLAUDE_CLI_PATH", None)
    else:
        os.environ["KOAN_CLAUDE_CLI_PATH"] = old


def test_isolate_env_strips_koan_claude_cli_path():
    """Host-set KOAN_CLAUDE_CLI_PATH must not leak past isolate_env."""
    from app.provider.claude import ClaudeProvider

    provider = ClaudeProvider()
    assert provider.binary() == "claude"
    assert provider.custom_binary_name() == ""
