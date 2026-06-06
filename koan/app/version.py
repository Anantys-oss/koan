"""Kōan version string from git tags."""

import subprocess
from pathlib import Path
from subprocess import TimeoutExpired

_KOAN_SRC = Path(__file__).resolve().parents[1]


def get_version() -> str:
    """Return Kōan version from git tags.

    Format: 'v0.73' (exact tag) or 'v0.73@deadbeef +17' (ahead of tag).
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags"],
            capture_output=True, text=True, timeout=5,
            cwd=_KOAN_SRC,
        )
        if result.returncode != 0:
            return ""
        desc = result.stdout.strip()
        parts = desc.rsplit("-", 2)
        if len(parts) == 3 and parts[2].startswith("g"):
            tag, commits_ahead, sha = parts[0], parts[1], parts[2][1:]
            return f"{tag}@{sha[:8]} +{commits_ahead}"
        return desc
    except (OSError, TimeoutExpired):
        return ""
