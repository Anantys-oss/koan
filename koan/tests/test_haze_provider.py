"""Tests for Haze CLI provider (app.provider.haze)."""

import json
from unittest.mock import patch, MagicMock

import pytest

from app.provider.haze import HazeProvider
from app.cli_provider import (
    HazeProvider as FacadeHaze,
    get_provider,
    get_provider_name,
    reset_provider,
    build_full_command,
)


# ---------------------------------------------------------------------------
# Package structure
# ---------------------------------------------------------------------------

class TestHazePackageStructure:
    """Verify Haze provider is properly registered and re-exported."""

    def test_import_from_provider_package(self):
        from app.provider import HazeProvider
        assert HazeProvider.name == "haze"

    def test_import_from_haze_module(self):
        from app.provider.haze import HazeProvider
        assert HazeProvider().binary() == "node"

    def test_facade_reexports_haze(self):
        """cli_provider.py re-exports HazeProvider."""
        from app.provider import HazeProvider as Package
        assert FacadeHaze is Package

    def test_haze_in_provider_registry(self):
        from app.provider import _PROVIDERS
        assert "haze" in _PROVIDERS

    def test_registry_creates_haze_instance(self):
        from app.provider import _PROVIDERS
        provider = _PROVIDERS["haze"]()
        assert isinstance(provider, HazeProvider)
        assert provider.name == "haze"


# ---------------------------------------------------------------------------
# HazeProvider basics
# ---------------------------------------------------------------------------

class TestHazeProvider:
    """Tests for HazeProvider flag generation."""

    def setup_method(self):
        self.provider = HazeProvider()

    def test_binary(self):
        assert self.provider.binary() == "node"

    def test_shell_command(self):
        assert self.provider.shell_command() == "haze"

    def test_name(self):
        assert self.provider.name == "haze"

    def test_supports_stream_json(self):
        assert self.provider.supports_stream_json() is True

    def test_supports_last_message_file(self):
        assert self.provider.supports_last_message_file() is True

    def test_last_message_file_args(self):
        assert self.provider.build_last_message_file_args("/tmp/out.txt") == [
            "--last-message", "/tmp/out.txt",
        ]

    def test_last_message_file_args_empty(self):
        assert self.provider.build_last_message_file_args("") == []

    def test_add_last_message_file_args(self):
        cmd = ["node", "bridge.mjs", "--prompt", "hi"]
        result = self.provider.add_last_message_file_args(cmd, "/tmp/out.txt")
        assert "--last-message" in result
        assert "/tmp/out.txt" in result

    def test_add_last_message_file_args_empty_path(self):
        cmd = ["node", "bridge.mjs", "--prompt", "hi"]
        result = self.provider.add_last_message_file_args(cmd, "")
        assert result == cmd

    def test_does_not_support_stdin_prompt_passing(self):
        """The bridge reads --prompt, not stdin."""
        assert self.provider.supports_stdin_prompt_passing() is False

    def test_invocation_lock_name(self):
        assert self.provider.invocation_lock_name() == "haze-cli"

    # -- Tool args (no-op) --

    def test_tool_args_allowed_ignored(self):
        assert self.provider.build_tool_args(allowed_tools=["Bash", "Read"]) == []

    def test_tool_args_disallowed_ignored(self):
        assert self.provider.build_tool_args(disallowed_tools=["Bash", "Edit", "Write"]) == []

    def test_tool_args_empty(self):
        assert self.provider.build_tool_args() == []

    # -- Model args --

    def test_model_args(self):
        assert self.provider.build_model_args(model="glm-5.2") == ["--model", "glm-5.2"]

    def test_model_args_empty(self):
        assert self.provider.build_model_args() == []

    def test_model_args_fallback_ignored(self):
        result = self.provider.build_model_args(model="glm-5.2", fallback="gpt-4o")
        assert result == ["--model", "glm-5.2"]
        assert "fallback" not in str(result)

    # -- Output args (always empty, bridge always emits JSONL) --

    def test_output_args(self):
        assert self.provider.build_output_args("json") == []
        assert self.provider.build_output_args("stream-json") == []
        assert self.provider.build_output_args("") == []

    # -- Max turns (no-op) --

    def test_max_turns_args(self):
        assert self.provider.build_max_turns_args(3) == []
        assert self.provider.build_max_turns_args(0) == []

    # -- MCP args (no-op) --

    def test_mcp_args(self):
        assert self.provider.build_mcp_args(["config1.json"]) == []
        assert self.provider.build_mcp_args() == []

    # -- Plugin args (no-op) --

    def test_plugin_args_ignored(self):
        assert self.provider.build_plugin_args(["/tmp/plugins"]) == []

    # -- Effort args (no-op) --

    def test_effort_args_ignored(self):
        assert self.provider.build_effort_args("high") == []

    # -- Permission args (no-op) --

    def test_permission_args(self):
        assert self.provider.build_permission_args(True) == []
        assert self.provider.build_permission_args(False) == []


# ---------------------------------------------------------------------------
# build_command
# ---------------------------------------------------------------------------

class TestHazeBuildCommand:
    """Tests for HazeProvider.build_command() — full command assembly."""

    def setup_method(self):
        self.provider = HazeProvider()

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_minimal(self, _mock):
        cmd = self.provider.build_command(prompt="hello")
        assert cmd[0] == "node"
        assert "--haze-root" in cmd
        idx = cmd.index("--haze-root")
        assert cmd[idx + 1] == "/fake/haze"
        assert "--prompt" in cmd
        pidx = cmd.index("--prompt")
        assert cmd[pidx + 1] == "hello"

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_includes_bridge_script(self, _mock):
        cmd = self.provider.build_command(prompt="hello")
        assert any("haze_headless.mjs" in c for c in cmd)

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_with_model(self, _mock):
        cmd = self.provider.build_command(prompt="do stuff", model="glm-5.2")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "glm-5.2"

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_system_prompt_prepended(self, _mock):
        """System prompt is prepended to user prompt (no native flag)."""
        cmd = self.provider.build_command(
            prompt="do the thing",
            system_prompt="You are helpful.",
        )
        pidx = cmd.index("--prompt")
        prompt_text = cmd[pidx + 1]
        assert prompt_text.startswith("You are helpful.")
        assert "do the thing" in prompt_text

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_fallback_ignored(self, _mock):
        cmd = self.provider.build_command(prompt="hello", model="glm-5.2", fallback="gpt-4o")
        assert "--model" in cmd
        # fallback should not produce a second model arg
        assert cmd.count("--model") == 1

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_tools_ignored(self, _mock):
        cmd = self.provider.build_command(
            prompt="hello",
            allowed_tools=["Bash", "Read"],
            disallowed_tools=["Write"],
        )
        assert "--allowedTools" not in cmd
        assert "--allow-tool" not in cmd

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_mcp_ignored(self, _mock):
        cmd = self.provider.build_command(prompt="hello", mcp_configs=["mcp.json"])
        assert "--mcp-config" not in cmd

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_plugin_dirs_ignored(self, _mock):
        cmd = self.provider.build_command(prompt="hello", plugin_dirs=["/tmp/plugins"])
        assert "--plugin-dir" not in cmd

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_full_command_shape(self, _mock):
        cmd = self.provider.build_command(
            prompt="implement feature X",
            allowed_tools=["Bash", "Read", "Write"],
            disallowed_tools=["Edit"],
            model="glm-5.2",
            fallback="gpt-4o",
            output_format="json",
            max_turns=25,
            mcp_configs=["mcp.json"],
            plugin_dirs=["/tmp/plugins"],
            skip_permissions=True,
            system_prompt="Be concise.",
        )
        assert cmd[0] == "node"
        assert "--haze-root" in cmd
        assert "--prompt" in cmd
        assert "--model" in cmd
        # system prompt prepended into prompt
        pidx = cmd.index("--prompt")
        assert "Be concise." in cmd[pidx + 1]
        assert "implement feature X" in cmd[pidx + 1]

    @patch("app.provider.haze._resolve_haze_root", return_value=None)
    def test_build_command_when_not_installed(self, _mock):
        """build_command stays total (returns a placeholder) when haze is missing."""
        cmd = self.provider.build_command(prompt="hello")
        assert cmd[0] == "node"
        assert "--prompt" in cmd


# ---------------------------------------------------------------------------
# build_extra_flags
# ---------------------------------------------------------------------------

class TestHazeExtraFlags:
    """Tests for build_extra_flags()."""

    def setup_method(self):
        self.provider = HazeProvider()

    def test_with_model(self):
        result = self.provider.build_extra_flags(model="glm-5.2")
        assert result == ["--model", "glm-5.2"]

    def test_with_disallowed_tools(self):
        assert self.provider.build_extra_flags(disallowed_tools=["Bash"]) == []

    def test_combined(self):
        result = self.provider.build_extra_flags(
            model="glm-5.2", fallback="gpt-4o", disallowed_tools=["Bash"],
        )
        assert result == ["--model", "glm-5.2"]


# ---------------------------------------------------------------------------
# Provider selection via env var / config
# ---------------------------------------------------------------------------

class TestHazeProviderSelection:
    """Tests for selecting Haze via KOAN_CLI_PROVIDER."""

    def setup_method(self):
        reset_provider()

    def teardown_method(self):
        reset_provider()

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "haze", "KOAN_ROOT": "/tmp"})
    def test_env_var_selects_haze(self):
        assert get_provider_name() == "haze"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "haze", "KOAN_ROOT": "/tmp"})
    def test_get_provider_returns_haze(self):
        provider = get_provider()
        assert isinstance(provider, HazeProvider)
        assert provider.name == "haze"

    @patch.dict("os.environ", {"KOAN_CLI_PROVIDER": "haze", "KOAN_ROOT": "/tmp"})
    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_build_full_command_uses_haze(self, _mock):
        cmd = build_full_command(prompt="hello")
        assert cmd[0] == "node"
        assert "--haze-root" in cmd


# ---------------------------------------------------------------------------
# _resolve_haze_root
# ---------------------------------------------------------------------------

class TestResolveHazeRoot:
    """Tests for _resolve_haze_root()."""

    def test_override_env_var(self, tmp_path, monkeypatch):
        dist = tmp_path / "dist"
        dist.mkdir()
        monkeypatch.setenv("KOAN_HAZE_PKG_PATH", str(tmp_path))
        from app.provider.haze import _resolve_haze_root
        assert _resolve_haze_root() == str(tmp_path)

    def test_override_env_var_invalid_path_returns_none(self, monkeypatch):
        monkeypatch.setenv("KOAN_HAZE_PKG_PATH", "/nonexistent/path/xyz")
        from app.provider.haze import _resolve_haze_root
        with patch("app.provider.haze.shutil.which", return_value=None):
            assert _resolve_haze_root() is None

    @patch("app.provider.haze.shutil.which", return_value=None)
    def test_returns_none_when_not_installed(self, _mock):
        from app.provider.haze import _resolve_haze_root
        os_env = {}
        with patch.dict("os.environ", os_env, clear=False):
            import os
            os.environ.pop("KOAN_HAZE_PKG_PATH", None)
            assert _resolve_haze_root() is None


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestHazeIsAvailable:
    """Tests for HazeProvider.is_available()."""

    def setup_method(self):
        self.provider = HazeProvider()

    @patch("app.provider.haze.shutil.which")
    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_available(self, _mock_root, mock_which):
        mock_which.return_value = "/usr/bin/node"
        assert self.provider.is_available() is True

    @patch("app.provider.haze.shutil.which", return_value=None)
    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    def test_not_available_no_node(self, _mock_root, _mock_which):
        assert self.provider.is_available() is False

    @patch("app.provider.haze._resolve_haze_root", return_value=None)
    def test_not_available_no_haze(self, _mock):
        assert self.provider.is_available() is False


# ---------------------------------------------------------------------------
# detect_quota_exhaustion
# ---------------------------------------------------------------------------

class TestHazeQuotaDetection:
    """Tests for HazeProvider.detect_quota_exhaustion()."""

    def setup_method(self):
        self.provider = HazeProvider()

    def test_detects_quota_in_stderr(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="",
            stderr_text="HTTP 429 insufficient_quota",
            exit_code=1,
        ) is True

    def test_detects_rate_limit_in_stderr(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="",
            stderr_text="rate limit exceeded",
            exit_code=1,
        ) is True

    def test_detects_quota_in_result_error_event(self):
        stdout = json.dumps({
            "type": "result",
            "subtype": "error",
            "error": "rate limit exceeded, retry after 60s",
        })
        assert self.provider.detect_quota_exhaustion(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is True

    def test_does_not_flag_success_result(self):
        stdout = json.dumps({
            "type": "result",
            "subtype": "success",
            "result": "done",
        })
        assert self.provider.detect_quota_exhaustion(
            stdout_text=stdout,
            stderr_text="",
            exit_code=0,
        ) is False

    def test_ignores_plain_quota_words_on_success_stdout(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="discussion: keep quota low and handle retries",
            stderr_text="",
            exit_code=0,
        ) is False

    def test_ignores_benign_prose_on_failed_stdout(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="plan: respect the quota during the rollout",
            stderr_text="",
            exit_code=1,
        ) is False


# ---------------------------------------------------------------------------
# detect_auth_failure
# ---------------------------------------------------------------------------

class TestHazeAuthDetection:
    """Tests for HazeProvider.detect_auth_failure()."""

    def setup_method(self):
        self.provider = HazeProvider()

    def test_detects_401_in_stderr(self):
        assert self.provider.detect_auth_failure(
            stdout_text="",
            stderr_text="unexpected status 401 Unauthorized",
            exit_code=1,
        ) is True

    def test_detects_invalid_api_key_in_stderr(self):
        assert self.provider.detect_auth_failure(
            stdout_text="",
            stderr_text="invalid api key",
            exit_code=1,
        ) is True

    def test_detects_no_provider_configured_in_result(self):
        stdout = json.dumps({
            "type": "result",
            "subtype": "error",
            "error": "No model provider configured. Run /provider",
        })
        assert self.provider.detect_auth_failure(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is True

    def test_detects_auth_in_assistant_text(self):
        stdout = json.dumps({
            "type": "assistant",
            "text": "Model call failed: invalid api key provided",
        })
        assert self.provider.detect_auth_failure(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is True

    def test_returns_false_on_success_exit(self):
        assert self.provider.detect_auth_failure(
            stdout_text="invalid api key mentioned",
            stderr_text="",
            exit_code=0,
        ) is False

    def test_returns_false_for_unrelated_error(self):
        stdout = json.dumps({
            "type": "result",
            "subtype": "error",
            "error": "file not found",
        })
        assert self.provider.detect_auth_failure(
            stdout_text=stdout,
            stderr_text="",
            exit_code=1,
        ) is False


# ---------------------------------------------------------------------------
# check_quota_available
# ---------------------------------------------------------------------------

class TestHazeQuotaCheck:
    """Tests for HazeProvider.check_quota_available()."""

    def setup_method(self):
        self.provider = HazeProvider()

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    @patch("app.cli_exec.run_cli")
    def test_success(self, mock_run, _mock_root):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"type":"result","subtype":"success","result":"ok"}',
            stderr="",
        )
        available, detail = self.provider.check_quota_available("/tmp/project")
        assert available is True
        assert detail == ""

    @patch("app.provider.haze._resolve_haze_root", return_value=None)
    def test_not_installed(self, _mock_root):
        available, detail = self.provider.check_quota_available("/tmp/project")
        assert available is False
        assert "not found" in detail or "install" in detail

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    @patch("app.cli_exec.run_cli")
    def test_timeout_proceeds_optimistically(self, mock_run, _mock_root):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="node", timeout=15)
        available, detail = self.provider.check_quota_available("/tmp/project")
        assert available is True

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    @patch("app.cli_exec.run_cli")
    def test_generic_error_proceeds_optimistically(self, mock_run, _mock_root):
        mock_run.side_effect = OSError("node not found")
        available, detail = self.provider.check_quota_available("/tmp/project")
        assert available is True

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    @patch("app.cli_exec.run_cli")
    def test_auth_failure_blocks_preflight(self, mock_run, _mock_root):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({
                "type": "result",
                "subtype": "error",
                "error": "No model provider configured. Run /provider to choose.",
            }),
            stderr="",
        )
        available, detail = self.provider.check_quota_available("/tmp/project")
        assert available is False
        assert "provider configured" in detail or "401" in detail or detail.strip()

    @patch("app.provider.haze._resolve_haze_root", return_value="/fake/haze")
    @patch("app.cli_exec.run_cli")
    def test_quota_failure_blocks_preflight(self, mock_run, _mock_root):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=json.dumps({
                "type": "result",
                "subtype": "error",
                "error": "rate limit exceeded",
            }),
            stderr="",
        )
        available, detail = self.provider.check_quota_available("/tmp/project")
        assert available is False
