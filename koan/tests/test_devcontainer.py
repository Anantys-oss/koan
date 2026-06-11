"""Tests for koan/app/devcontainer.py."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# get_devcontainer_config
# ---------------------------------------------------------------------------

class TestGetDevcontainerConfig:
    def _run_result(self, returncode, stdout="", stderr=""):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_returns_present_and_workspace_path(self, tmp_path):
        from app.devcontainer import get_devcontainer_config
        cli_output = json.dumps({"configuration": {"workspaceFolder": "/cli/path"}})
        with patch("app.devcontainer.shutil.which", return_value="/usr/bin/devcontainer"), \
             patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, cli_output)):
            present, path = get_devcontainer_config(str(tmp_path))
        assert present is True
        assert path == "/cli/path"

    def test_returns_false_and_fallback_when_cli_unavailable(self, tmp_path):
        from app.devcontainer import get_devcontainer_config
        with patch("app.devcontainer.shutil.which", return_value=None):
            present, path = get_devcontainer_config(str(tmp_path))
        assert present is False
        assert path == f"/workspaces/{tmp_path.name}"

    def test_returns_false_and_fallback_when_cli_fails(self, tmp_path):
        from app.devcontainer import get_devcontainer_config
        with patch("app.devcontainer.shutil.which", return_value="/usr/bin/devcontainer"), \
             patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(1, "")):
            present, path = get_devcontainer_config(str(tmp_path))
        assert present is False
        assert path == f"/workspaces/{tmp_path.name}"

    def test_returns_false_and_fallback_when_cli_raises(self, tmp_path):
        from app.devcontainer import get_devcontainer_config
        with patch("app.devcontainer.shutil.which", return_value="/usr/bin/devcontainer"), \
             patch("app.devcontainer.subprocess.run", side_effect=OSError("boom")):
            present, path = get_devcontainer_config(str(tmp_path))
        assert present is False
        assert path == f"/workspaces/{tmp_path.name}"

    def test_returns_true_and_fallback_when_no_workspace_folder(self, tmp_path):
        from app.devcontainer import get_devcontainer_config
        cli_output = json.dumps({"configuration": {}})
        with patch("app.devcontainer.shutil.which", return_value="/usr/bin/devcontainer"), \
             patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, cli_output)):
            present, path = get_devcontainer_config(str(tmp_path))
        assert present is True
        assert path == f"/workspaces/{tmp_path.name}"


# ---------------------------------------------------------------------------
# ensure_container_up
# ---------------------------------------------------------------------------

class TestEnsureContainerUp:
    def _run_result(self, returncode, stdout, stderr=""):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_logs_started_on_outcome_started(self, tmp_path, capsys):
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started", "containerId": "abc123"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)):
            ensure_container_up(str(tmp_path))
        captured = capsys.readouterr()
        assert "started" in captured.out or "started" in captured.err

    def test_logs_reused_on_outcome_exists(self, tmp_path, capsys):
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "exists", "containerId": "abc123"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)):
            ensure_container_up(str(tmp_path))
        captured = capsys.readouterr()
        assert "reused" in captured.out or "reused" in captured.err

    def test_returns_container_id(self, tmp_path):
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started", "containerId": "abc123"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)):
            result = ensure_container_up(str(tmp_path))
        assert result == "abc123"

    def test_returns_empty_string_when_no_container_id(self, tmp_path):
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)):
            result = ensure_container_up(str(tmp_path))
        assert result == ""

    def test_passes_ghcr_features_for_claude(self, tmp_path):
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="claude")
        call_args = mock_run.call_args[0][0]
        assert "--additional-features" in call_args
        features_idx = call_args.index("--additional-features")
        features = json.loads(call_args[features_idx + 1])
        assert "ghcr.io/exciton/devcontainer-features/claude-code-config-bind-mount:latest" in features
        assert "ghcr.io/anthropics/devcontainer-features/claude-code:1" in features
        assert "ghcr.io/devcontainers/features/github-cli:1" in features
        assert "--remote-env" not in call_args or not any("CLAUDE_CONFIG_DIR" in a for a in call_args)

    def test_no_manual_mounts_beyond_instance_and_tmp(self, tmp_path):
        # ~/.claude is handled by the external feature — no --mount flag for it.
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="claude")
        call_args = mock_run.call_args[0][0]
        mounts = [call_args[i + 1] for i, a in enumerate(call_args) if a == "--mount"]
        # No mounts at all when instance_path and koan_tmp_path are not provided
        assert mounts == []

    def test_adds_instance_mount_when_provided(self, tmp_path):
        from app.devcontainer import ensure_container_up, CONTAINER_INSTANCE_DIR
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="claude",
                                instance_path="/host/instance")
        call_args = mock_run.call_args[0][0]
        mounts = [call_args[i + 1] for i, a in enumerate(call_args) if a == "--mount"]
        assert any("source=/host/instance" in m and f"target={CONTAINER_INSTANCE_DIR}" in m
                   for m in mounts)

    def test_no_instance_mount_when_empty(self, tmp_path):
        from app.devcontainer import ensure_container_up, CONTAINER_INSTANCE_DIR
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="claude", instance_path="")
        call_args = mock_run.call_args[0][0]
        mounts = [call_args[i + 1] for i, a in enumerate(call_args) if a == "--mount"]
        assert not any(CONTAINER_INSTANCE_DIR in m for m in mounts)

    def test_adds_koan_tmp_mount_when_provided(self, tmp_path):
        from app.devcontainer import ensure_container_up, CONTAINER_TMP_DIR
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="claude",
                                koan_tmp_path="/host/koan-tmp")
        call_args = mock_run.call_args[0][0]
        mounts = [call_args[i + 1] for i, a in enumerate(call_args) if a == "--mount"]
        assert any("source=/host/koan-tmp" in m and f"target={CONTAINER_TMP_DIR}" in m
                   for m in mounts)

    def test_no_remote_env_for_claude_provider(self, tmp_path):
        # GH_TOKEN is handled by _run_container_setup via file, not --remote-env on up.
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="claude")
        call_args = mock_run.call_args[0][0]
        assert not any("GH_TOKEN" in arg for arg in call_args)
        assert "--remote-env" not in call_args

    def test_no_additional_flags_for_non_claude_provider(self, tmp_path):
        from app.devcontainer import ensure_container_up
        payload = json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, payload)) as mock_run:
            ensure_container_up(str(tmp_path), provider_name="copilot")
        call_args = mock_run.call_args[0][0]
        assert "--additional-features" not in call_args
        assert "--mount" not in call_args
        assert "--remote-env" not in call_args

    def test_raises_on_nonzero_exit(self, tmp_path):
        from app.devcontainer import ensure_container_up
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(1, "", "container failed")):
            with pytest.raises(RuntimeError, match="devcontainer up failed"):
                ensure_container_up(str(tmp_path))

    def test_handles_multiline_output_picks_last_json(self, tmp_path, capsys):
        from app.devcontainer import ensure_container_up
        stdout = "some log line\nanother log line\n" + json.dumps({"outcome": "started"})
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0, stdout)):
            ensure_container_up(str(tmp_path))
        captured = capsys.readouterr()
        assert "started" in captured.out or "started" in captured.err


# ---------------------------------------------------------------------------
# _run_container_setup
# ---------------------------------------------------------------------------

class TestRunContainerSetup:
    def _run_result(self, returncode, stdout="", stderr=""):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_runs_gh_auth_setup_git_as_container_user(self, tmp_path):
        from app.devcontainer import _run_container_setup
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0)) as mock_run:
            _run_container_setup(str(tmp_path))
        call_args = mock_run.call_args[0][0]
        # Must use devcontainer exec (not docker exec) so it runs as the container user
        assert call_args[0] == "devcontainer"
        assert "gh" in call_args
        assert "auth" in call_args
        assert "setup-git" in call_args

    def test_gh_auth_login_runs_when_token_and_tmp_dir_provided(self, tmp_path):
        from app.devcontainer import _run_container_setup
        with patch("app.devcontainer.get_gh_env", return_value={"GH_TOKEN": "ghp_tok123"}), \
             patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0)) as mock_run:
            _run_container_setup(str(tmp_path), koan_tmp_path=str(tmp_path))
        # login call + setup-git call
        assert mock_run.call_count == 2
        login_cmd = mock_run.call_args_list[0][0][0]
        assert any("gh auth login" in arg for arg in login_cmd)

    def test_skips_login_when_no_token(self, tmp_path):
        from app.devcontainer import _run_container_setup
        with patch("app.devcontainer.get_gh_env", return_value={}), \
             patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0)) as mock_run:
            _run_container_setup(str(tmp_path), koan_tmp_path=str(tmp_path))
        # Only setup-git runs — no login call
        assert mock_run.call_count == 1

    def test_raises_on_gh_auth_login_failure(self, tmp_path):
        from app.devcontainer import _run_container_setup
        with patch("app.devcontainer.get_gh_env", return_value={"GH_TOKEN": "ghp_bad"}), \
             patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(1, stderr="bad credentials")):
            with pytest.raises(RuntimeError, match="gh auth login failed"):
                _run_container_setup(str(tmp_path), koan_tmp_path=str(tmp_path))

    def test_only_runs_one_subprocess_call(self, tmp_path):
        # No koan_tmp_path → no token file → only gh auth setup-git runs.
        from app.devcontainer import _run_container_setup
        with patch("app.devcontainer.subprocess.run",
                   return_value=self._run_result(0)) as mock_run:
            _run_container_setup(str(tmp_path))
        assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# wrap_command
# ---------------------------------------------------------------------------

class TestWrapCommand:
    def test_prepends_devcontainer_exec_prefix(self, tmp_path):
        from app.devcontainer import wrap_command
        cmd = ["claude", "-p", "hello world"]
        result = wrap_command(cmd, str(tmp_path))
        assert result[0] == "devcontainer"
        assert result[1] == "exec"
        assert result[2] == "--workspace-folder"
        assert result[3] == str(tmp_path.resolve())
        assert result[-len(cmd) - 1] == "--"
        assert result[-len(cmd):] == cmd

    def test_no_remote_env_injected(self, tmp_path):
        from app.devcontainer import wrap_command
        result = wrap_command(["claude"], str(tmp_path))
        assert "--remote-env" not in result

    def test_uses_absolute_path(self, tmp_path, monkeypatch):
        from app.devcontainer import wrap_command
        monkeypatch.chdir(tmp_path)
        result = wrap_command(["claude"], ".")
        assert os.path.isabs(result[3])

    def test_cmd_follows_double_dash(self, tmp_path):
        from app.devcontainer import wrap_command
        inner = ["codex", "--flag"]
        result = wrap_command(inner, str(tmp_path))
        dash_idx = result.index("--")
        assert result[dash_idx + 1:] == inner

    def test_translates_host_tmp_paths_to_container_paths(self, tmp_path):
        from app.devcontainer import wrap_command, CONTAINER_TMP_DIR
        host_tmp = "/host/koan-tmp"
        inner = [
            "claude",
            "--append-system-prompt-file", f"{host_tmp}/koan-sysprompt-abc.txt",
            "--plugin-dir", f"{host_tmp}/koan-plugins-xyz",
        ]
        result = wrap_command(inner, str(tmp_path),
                              host_tmp_dir=host_tmp, container_tmp_dir=CONTAINER_TMP_DIR)
        assert f"{CONTAINER_TMP_DIR}/koan-sysprompt-abc.txt" in result
        assert f"{CONTAINER_TMP_DIR}/koan-plugins-xyz" in result
        assert host_tmp not in " ".join(result)

    def test_no_path_translation_when_dirs_empty(self, tmp_path):
        from app.devcontainer import wrap_command
        inner = ["claude", "--append-system-prompt-file", "/host/tmp/koan-sysprompt.txt"]
        result = wrap_command(inner, str(tmp_path))
        assert "/host/tmp/koan-sysprompt.txt" in result


# ---------------------------------------------------------------------------
# prepare_devcontainer
# ---------------------------------------------------------------------------

class TestPrepareDevcontainer:
    def test_raises_when_devcontainer_cli_missing(self, tmp_path):
        from app.devcontainer import prepare_devcontainer
        with patch("app.devcontainer.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="npm install -g @devcontainers/cli"):
                prepare_devcontainer(str(tmp_path))

    def test_calls_container_up_and_run_container_setup_for_claude(self, tmp_path):
        from app.devcontainer import prepare_devcontainer
        with patch("app.devcontainer.shutil.which", return_value="/usr/bin/devcontainer"), \
             patch("app.devcontainer.get_gh_env", return_value={}), \
             patch("app.devcontainer.ensure_container_up", return_value="cid123") as mock_up, \
             patch("app.devcontainer._run_container_setup") as mock_setup:
            prepare_devcontainer(str(tmp_path), provider_name="claude",
                                 instance_path="/host/instance")

        mock_up.assert_called_once()
        mock_setup.assert_called_once_with(str(tmp_path), koan_tmp_path="")

    def test_no_run_container_setup_for_non_claude_provider(self, tmp_path):
        from app.devcontainer import prepare_devcontainer
        with patch("app.devcontainer.shutil.which", return_value="/usr/bin/devcontainer"), \
             patch("app.devcontainer.get_gh_env", return_value={}), \
             patch("app.devcontainer.ensure_container_up", return_value=""), \
             patch("app.devcontainer._run_container_setup") as mock_setup:
            prepare_devcontainer(str(tmp_path), provider_name="copilot")

        mock_setup.assert_not_called()
