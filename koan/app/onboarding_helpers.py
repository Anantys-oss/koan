"""Shared helpers for Kōan installation and onboarding."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent
KOAN_ROOT = SCRIPT_DIR.parent.parent
INSTANCE_DIR = KOAN_ROOT / "instance"
INSTANCE_EXAMPLE = KOAN_ROOT / "instance.example"
ENV_FILE = KOAN_ROOT / ".env"
ENV_EXAMPLE = KOAN_ROOT / "env.example"
KOAN_REPO_URL = "https://github.com/Anantys-oss/koan.git"


def paths_for_root(koan_root: Path) -> dict[str, Path]:
    """Return onboarding paths for a specific Kōan root."""
    return {
        "koan_root": koan_root,
        "instance_dir": koan_root / "instance",
        "instance_example": koan_root / "instance.example",
        "env_file": koan_root / ".env",
        "env_example": koan_root / "env.example",
        "workspace_dir": koan_root / "workspace",
        "workspace_koan": koan_root / "workspace" / "koan",
    }


def create_instance_dir(koan_root: Path | None = None) -> bool:
    """Copy instance.example to instance if it does not already exist."""
    paths = paths_for_root(koan_root or KOAN_ROOT)
    instance_dir = paths["instance_dir"]
    if instance_dir.exists():
        return True
    instance_example = paths["instance_example"]
    if not instance_example.exists():
        return False
    shutil.copytree(instance_example, instance_dir)
    return True


def create_env_file(koan_root: Path | None = None) -> bool:
    """Ensure a .env exists. Prefer env.example; if absent (hosted image),
    synthesize from the environment so env-var-only deploys never fail
    (closes #2076)."""
    paths = paths_for_root(koan_root or KOAN_ROOT)
    env_file = paths["env_file"]
    if env_file.exists():
        return True
    env_example = paths["env_example"]
    if env_example.exists():
        shutil.copy(env_example, env_file)
        return True
    # No env.example: synthesize from environment when possible.
    from app.railway import required_env_present, write_env_from_environment
    if required_env_present():
        write_env_from_environment(env_file)
        return True
    return False


def update_env_var(key: str, value: str, env_file: Path | None = None) -> bool:
    """Update or add an environment variable in a .env file."""
    path = env_file or ENV_FILE
    if not path.exists():
        return False

    lines = path.read_text().split("\n")
    updated = False
    new_lines = []

    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines))
    return True


def get_env_var(key: str, env_file: Path | None = None) -> Optional[str]:
    """Read an environment variable from a .env file."""
    path = env_file or ENV_FILE
    if not path.exists():
        return None

    for line in path.read_text().split("\n"):
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def remove_env_var(key: str, env_file: Path | None = None) -> bool:
    """Remove an environment variable line from a .env file."""
    path = env_file or ENV_FILE
    if not path.exists():
        return False

    lines = path.read_text().split("\n")
    new_lines = [line for line in lines if not line.startswith(f"{key}=")]

    if len(new_lines) == len(lines):
        return False

    path.write_text("\n".join(new_lines))
    return True


def verify_telegram_token(token: str) -> dict:
    """Verify a Telegram bot token by calling getMe."""
    import urllib.request

    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            if data.get("ok"):
                bot_info = data.get("result", {})
                return {
                    "valid": True,
                    "username": bot_info.get("username", ""),
                    "first_name": bot_info.get("first_name", ""),
                }
    except Exception as exc:
        return {"valid": False, "error": str(exc)}

    return {"valid": False, "error": "Invalid token"}


def get_chat_id_from_updates(token: str) -> Optional[str]:
    """Try to get a Telegram chat ID from recent updates."""
    import urllib.request

    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates?limit=5"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            if data.get("ok"):
                for update in data.get("result", []):
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    if chat.get("id"):
                        return str(chat["id"])
    except (OSError, ValueError):
        pass
    return None


def has_instance(koan_root: Path) -> bool:
    """True when the instance dir + env file exist, OR when a hosted deploy
    is fully configured via environment variables."""
    paths = paths_for_root(koan_root)
    if paths["instance_dir"].is_dir() and paths["env_file"].is_file():
        return True
    from app.railway import env_configured
    return paths["instance_dir"].is_dir() and env_configured()


def onboarding_needed(koan_root: Path) -> bool:
    """Return True when first-run onboarding should be shown."""
    checkpoint = koan_root / ".koan-onboarding.json"
    return checkpoint.exists() or not has_instance(koan_root)


def setup_workspace_koan(koan_root: Path) -> tuple[bool, str]:
    """Ensure workspace/koan is a clone of the public Kōan repository."""
    paths = paths_for_root(koan_root)
    workspace_dir = paths["workspace_dir"]
    koan_project = paths["workspace_koan"]
    workspace_dir.mkdir(exist_ok=True)

    if koan_project.exists():
        if not koan_project.is_dir():
            return False, f"{koan_project} exists but is not a directory"
        if _has_koan_remote(koan_project):
            return True, f"workspace/koan already configured at {koan_project}"
        return (
            False,
            f"{koan_project} exists but does not point to {KOAN_REPO_URL}",
        )

    result = subprocess.run(
        ["git", "clone", KOAN_REPO_URL, str(koan_project)],
        cwd=str(workspace_dir),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        return False, output or "git clone failed"

    return True, f"cloned {KOAN_REPO_URL} to {koan_project}"


def _has_koan_remote(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    # Normalize SSH form (git@github.com:owner/repo) to the HTTPS path form
    # so both remote styles match the expected repository.
    remotes = result.stdout.lower().replace("github.com:", "github.com/")
    return "github.com/anantys-oss/koan" in remotes
