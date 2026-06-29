"""Real-time config change classification for hot-reload vs restart-required.

The backend already re-reads config per use (``utils.load_config`` has no
cache; ``projects_config.load_projects_config`` self-invalidates on mtime),
so this module does NOT reload anything. It snapshots config at agent
startup (``write_baseline``) and later reports which on-disk changes are
safe (already live) vs unsafe (need a restart to take effect).
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app import config as _config
from app.utils import atomic_write

BASELINE_FILE = ".koan-config-baseline.json"


def _config_path(koan_root: Path) -> Path:
    return koan_root / "instance" / "config.yaml"


def _projects_path(koan_root: Path) -> Path:
    return koan_root / "instance" / "projects.yaml"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _flatten(data: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested dict into dotted-path -> serialized scalar values."""
    out: Dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix] = json.dumps(data, sort_keys=True, default=str)
    return out


def _snapshot(koan_root: Path) -> Dict[str, Dict[str, Any]]:
    return {
        "config": _flatten(_read_yaml(_config_path(koan_root))),
        "projects": _flatten(_read_yaml(_projects_path(koan_root))),
    }


def write_baseline(koan_root: Path) -> None:
    """Persist the current config as the post-restart baseline."""
    path = koan_root / "instance" / BASELINE_FILE
    with contextlib.suppress(OSError):
        atomic_write(path, json.dumps(_snapshot(koan_root)))


def _read_baseline(koan_root: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    path = koan_root / "instance" / BASELINE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _is_safe(dotted: str) -> bool:
    safe = _config.get_hot_reload_safe_keys()
    if dotted in safe:
        return True
    head = dotted.split(".", 1)[0]
    return head in safe


def _diff_keys(base: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    changed = [k for k in set(base) | set(cur) if base.get(k) != cur.get(k)]
    return sorted(changed)


def compute_status(koan_root: Path) -> Dict[str, Any]:
    """Return the config-sync status block for the SSE payload / API."""
    koan_root = Path(koan_root)

    # Feature disabled -> suppress all UI feedback (badge/toast/modal).
    if not _config.is_config_sync_enabled():
        return {
            "synced": True,
            "restart_pending": False,
            "changed_safe_keys": [],
            "changed_unsafe_keys": [],
        }

    baseline = _read_baseline(koan_root)
    current = _snapshot(koan_root)

    # No baseline (agent never recorded one) -> never block the UI.
    if baseline is None:
        return {
            "synced": True,
            "restart_pending": False,
            "changed_safe_keys": [],
            "changed_unsafe_keys": [],
        }

    safe_keys: List[str] = []
    unsafe_keys: List[str] = []

    for ck in _diff_keys(baseline.get("config", {}), current["config"]):
        (safe_keys if _is_safe(ck) else unsafe_keys).append(ck)

    # projects.yaml: path/cli_provider/models are all unsafe -> wholesale.
    unsafe_keys.extend(
        f"projects.yaml:{pk}"
        for pk in _diff_keys(baseline.get("projects", {}), current["projects"])
    )

    return {
        "synced": not safe_keys and not unsafe_keys,
        "restart_pending": bool(unsafe_keys),
        "changed_safe_keys": safe_keys,
        "changed_unsafe_keys": unsafe_keys,
    }
