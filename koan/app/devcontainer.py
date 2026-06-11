"""Devcontainer execution support for Kōan mission runner.

Handles container lifecycle and command wrapping for projects configured
with devcontainer: true.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List

from app.github_auth import get_gh_env
from app.run_log import log_safe

# Paths inside the container where host directories are bind-mounted.
CONTAINER_INSTANCE_DIR = "/mnt/koan-instance"
CONTAINER_TMP_DIR = "/mnt/koan-tmp"


def _read_configuration(project_path: str) -> "subprocess.CompletedProcess[str] | None":
    """Run `devcontainer read-configuration` and return the result, or None on failure."""
    if not shutil.which("devcontainer"):
        return None
    abs_path = str(Path(project_path).resolve())
    try:
        return subprocess.run(
            ["devcontainer", "read-configuration", "--workspace-folder", abs_path],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        log_safe("devcontainer", f"read-configuration timed out after 30s for {abs_path} — Docker may be unresponsive")
        return None
    except OSError:
        return None


def _parse_workspace_path(result: "subprocess.CompletedProcess[str]", fallback: str) -> str:
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                folder = (data.get("configuration") or {}).get("workspaceFolder")
                if folder:
                    return str(folder)
                break  # valid JSON, no workspaceFolder — no point checking more lines
            except (json.JSONDecodeError, TypeError):
                pass  # not valid JSON; try the next line
    return fallback


def get_devcontainer_config(project_path: str) -> "tuple[bool, str]":
    """Return (is_present, workspace_path) from a single read-configuration call.

    workspace_path falls back to /workspaces/<basename> when the CLI is
    unavailable, returns non-zero, or has no workspaceFolder.
    """
    fallback = f"/workspaces/{Path(project_path).name}"
    result = _read_configuration(project_path)
    if result is None or result.returncode != 0:
        return False, fallback
    return True, _parse_workspace_path(result, fallback)



def ensure_container_up(
    project_path: str,
    provider_name: str = "claude",
    instance_path: str = "",
    koan_tmp_path: str = "",
) -> str:
    """Bring the devcontainer up (idempotent via devcontainer up).

    For the "claude" provider, injects ghcr.io features and sets GITHUB_TOKEN.
    Dynamic mounts for the instance directory and temp dir are added via CLI flags.

    Parses JSON output for outcome and containerId.
    Returns the container ID string.
    Raises RuntimeError on non-zero exit.
    """
    abs_path = str(Path(project_path).resolve())
    cmd = ["devcontainer", "up", "--workspace-folder", abs_path]

    if provider_name == "claude":
        cmd.extend([
            "--additional-features", json.dumps({
                # Handles ~/.claude bind-mount and ~/.claude symlink inside container
                "ghcr.io/exciton/devcontainer-features/claude-code-config-bind-mount:latest": {},
                "ghcr.io/anthropics/devcontainer-features/claude-code:1": {},
                "ghcr.io/devcontainers/features/github-cli:1": {},
            }),
        ])
        if instance_path:
            cmd.extend([
                "--mount",
                f"type=bind,source={instance_path},target={CONTAINER_INSTANCE_DIR}",
            ])
        if koan_tmp_path:
            cmd.extend([
                "--mount",
                f"type=bind,source={koan_tmp_path},target={CONTAINER_TMP_DIR}",
            ])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        log_safe("devcontainer", f"devcontainer up timed out after 20 minutes for {abs_path}")
        raise RuntimeError(f"devcontainer up timed out after 20 minutes for {abs_path}")
    if result.returncode != 0:
        raise RuntimeError(
            f"devcontainer up failed (exit={result.returncode}): {result.stderr.strip()}"
        )

    outcome = "unknown"
    container_id = ""
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                outcome = data.get("outcome", "unknown")
                container_id = data.get("containerId", "")
            except (json.JSONDecodeError, TypeError) as exc:
                log_safe("devcontainer", f"failed to parse devcontainer up output: {exc} — line: {line!r}")
            break

    if outcome == "exists":
        log_safe("devcontainer", f"Container reused for {abs_path}")
    elif outcome == "started":
        log_safe("devcontainer", f"Container started for {abs_path}")
    else:
        log_safe("devcontainer", f"Container up (outcome={outcome}) for {abs_path}")

    return container_id


def _run_container_setup(
    project_path: str,
    koan_tmp_path: str = "",
) -> None:
    """Post-start container setup: configure git HTTPS credentials.

    The ~/.claude mount and symlink are handled by the
    ghcr.io/exciton/devcontainer-features/claude-code-config-bind-mount feature
    at build time, so only git credential setup is needed here.

    Must run as the container user via devcontainer exec so gh auth setup-git
    writes to the user's ~/.gitconfig, not root's.
    """
    abs_path = str(Path(project_path).resolve())
    github_token = get_gh_env().get("GH_TOKEN", "")

    token_file = None
    try:
        if github_token and koan_tmp_path:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=koan_tmp_path, prefix="gh-token-", suffix=".txt", delete=False,
            ) as tf:
                tf.write(github_token)
                token_file = Path(tf.name)
            container_token_path = f"{CONTAINER_TMP_DIR}/{token_file.name}"
            login_cmd = [
                "devcontainer", "exec", "--workspace-folder", abs_path,
                "--", "sh", "-c", f"gh auth login --with-token < {container_token_path}",
            ]
            result = subprocess.run(login_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(f"gh auth login failed (rc={result.returncode}): {detail}")

        exec_cmd = ["devcontainer", "exec", "--workspace-folder", abs_path, "--", "gh", "auth", "setup-git"]
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            log_safe("devcontainer", f"gh auth setup-git failed (rc={result.returncode}): {detail}")
    finally:
        if token_file and token_file.exists():
            token_file.unlink()


def wrap_command(
    cmd: List[str],
    project_path: str,
    host_tmp_dir: str = "",
    container_tmp_dir: str = "",
) -> List[str]:
    """Wrap cmd with devcontainer exec prefix.

    When host_tmp_dir and container_tmp_dir are both set, translates any
    command arg that starts with host_tmp_dir to use container_tmp_dir
    instead — so temp files created on the host are referenced by their
    container path in the CLI command.
    """
    abs_path = str(Path(project_path).resolve())
    exec_cmd = ["devcontainer", "exec", "--workspace-folder", abs_path]
    exec_cmd.append("--")

    if host_tmp_dir and container_tmp_dir:
        cmd = [
            arg.replace(host_tmp_dir, container_tmp_dir, 1) if arg.startswith(host_tmp_dir) else arg
            for arg in cmd
        ]

    exec_cmd.extend(cmd)
    return exec_cmd


def prepare_devcontainer(
    project_path: str,
    provider_name: str = "claude",
    instance_path: str = "",
    koan_tmp_path: str = "",
) -> None:
    """Orchestrate devcontainer setup before mission execution.

    1. Verifies devcontainer CLI is installed.
    2. Brings the container up with appropriate mounts and features.
    3. For the "claude" provider, runs post-start git credential setup.

    Raises RuntimeError if the devcontainer CLI is not found.
    """
    if not shutil.which("devcontainer"):
        raise RuntimeError(
            "devcontainer CLI not found. Install it with: "
            "npm install -g @devcontainers/cli"
        )

    ensure_container_up(
        project_path,
        provider_name,
        instance_path=instance_path,
        koan_tmp_path=koan_tmp_path,
    )
    if provider_name == "claude":
        _run_container_setup(project_path, koan_tmp_path=koan_tmp_path)
