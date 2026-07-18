"""Real-time config change classification for hot-reload vs restart-required.

The backend already re-reads config per use (``utils.load_config`` has no
cache; ``projects_config.load_projects_config`` self-invalidates on mtime),
so this module does NOT reload anything. It snapshots config at agent
startup (``write_baseline``) and later reports which on-disk changes are
safe (already live) vs unsafe (need a restart to take effect).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app import config as _config
from app.utils import atomic_write

BASELINE_FILE = ".koan-config-baseline.json"


class _ConfigReadError(Exception):
    """A config/baseline file exists on disk but could not be parsed.

    Distinct from 'file absent' — a broken file must surface a restart-pending
    state rather than being silently treated as empty/synced.
    """


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
    except (yaml.YAMLError, OSError) as e:
        # A malformed file parsed to {} would masquerade as "empty config",
        # misclassifying the diff. Surface it so callers can report an error.
        raise _ConfigReadError(f"{path.name}: {e}") from e


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
    # If config is broken at startup there is no meaningful baseline to
    # snapshot; skip rather than crash the startup hook. Log the failure so a
    # persistent write problem (disk full, permissions) is observable — a
    # missing baseline silently reports "synced", masking restart-pending state.
    try:
        atomic_write(path, json.dumps(_snapshot(koan_root)))
    except (OSError, _ConfigReadError) as e:
        print(f"[config_sync] baseline write failed: {e}", file=sys.stderr)


def _read_baseline(koan_root: Path) -> Optional[Dict[str, Dict[str, Any]]]:
    path = koan_root / "instance" / BASELINE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        # Corrupt baseline != absent baseline. Raise so compute_status reports
        # restart-pending instead of a false "synced".
        raise _ConfigReadError(f"{BASELINE_FILE}: {e}") from e


def _is_safe(dotted: str) -> bool:
    safe = _config.get_hot_reload_safe_keys()
    if dotted in safe:
        return True
    head = dotted.split(".", 1)[0]
    return head in safe


def _diff_keys(base: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    changed = [k for k in set(base) | set(cur) if base.get(k) != cur.get(k)]
    return sorted(changed)


def _synced_block() -> Dict[str, Any]:
    return {
        "synced": True,
        "restart_pending": False,
        "changed_safe_keys": [],
        "changed_unsafe_keys": [],
    }


def compute_status(koan_root: Path) -> Dict[str, Any]:
    """Return the config-sync status block for the SSE payload / API."""
    koan_root = Path(koan_root)

    # Feature disabled -> suppress all UI feedback (badge/toast/modal).
    if not _config.is_config_sync_enabled():
        return _synced_block()

    try:
        baseline = _read_baseline(koan_root)
        current = _snapshot(koan_root)
    except _ConfigReadError as e:
        # A corrupt baseline or unparseable config MUST NOT report "synced" —
        # that would silently hide changes needing a restart, the exact failure
        # the closed allowlist guards against. Surface a distinct error state.
        print(f"[config_sync] {e}", file=sys.stderr)
        return {
            "synced": False,
            "restart_pending": True,
            "changed_safe_keys": [],
            "changed_unsafe_keys": [f"<config read error: {e}>"],
            "error": str(e),
        }

    # No baseline (agent never recorded one) -> never block the UI.
    if baseline is None:
        return _synced_block()

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
