"""Workspace directory scanner — auto-discovers projects.

Scans KOAN_ROOT/workspace/ for immediate child directories (including
symlinks), returning (name, resolved_path) tuples for each discovered project.

Projects are discovered by their directory name — no configuration needed.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def discover_workspace_projects(koan_root: str) -> List[Tuple[str, str]]:
    """Scan workspace/ directory for projects.

    Returns sorted list of (name, resolved_path) tuples.
    Skips hidden directories, broken symlinks, and non-directories.
    Returns empty list if workspace/ doesn't exist.

    Prefers instance/workspace (persistent volume on hosted deploys),
    falling back to <root>/workspace for local/dev installs.
    """
    inst_ws = Path(koan_root) / "instance" / "workspace"
    workspace_dir = inst_ws if inst_ws.is_dir() else Path(koan_root) / "workspace"
    if not workspace_dir.is_dir():
        return []

    try:
        entries = sorted(workspace_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        logger.warning("Cannot read workspace directory: %s", e)
        return []

    projects = []
    for entry in entries:
        resolved_path = _validate_entry(entry)
        if resolved_path:
            projects.append((entry.name, resolved_path))

    return projects


def _validate_entry(entry: Path) -> Optional[str]:
    """Validate a workspace entry and return its resolved path.
    
    Returns None if the entry should be skipped (hidden, file, broken symlink).
    Returns the resolved absolute path string if valid.
    """
    name = entry.name
    
    # Skip hidden directories and special files
    if name.startswith("."):
        return None
    
    # Skip non-directory files (README.md, etc.)
    # Wrapped in try/except for symlink loops where stat() fails
    try:
        if entry.is_file():
            return None
    except OSError:
        pass
    
    # Resolve symlinks
    try:
        resolved = entry.resolve()
    except (OSError, RuntimeError) as e:
        logger.warning("Workspace: cannot resolve '%s': %s", name, e)
        return None
    
    # Validate target is a directory
    try:
        if not resolved.is_dir():
            logger.warning("Workspace: '%s' points to non-directory: %s", name, resolved)
            return None
    except OSError as e:
        logger.warning("Workspace: cannot stat '%s': %s", name, e)
        return None
    
    return str(resolved)
