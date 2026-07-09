"""Resolve the active :class:`MissionStore` backend from config (single read path).

``missions.backend`` selects the backend at startup:

- ``sqlite`` (default) → the in-tree :class:`SqliteMissionStore`.
- ``module.path:ClassName`` → an out-of-tree adapter, imported dynamically. This
  is what lets a third party supply their own store (a file-based one, a
  networked one) without editing Kōan — the same grain as CLI providers, bridges,
  and hooks. Kōan enumerates no external backends (mechanism, not enumeration).

A failed import/resolution aborts startup with a clear error — never a silent
fallback that would mask a misconfiguration.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Dict

from app.mission_store.base import MissionStore

logger = logging.getLogger(__name__)

# One store instance per instance-dir (bridge + run each build their own; within
# a process we reuse it so the startup log fires once).
_cache: Dict[str, MissionStore] = {}


class MissionStoreConfigError(RuntimeError):
    """Raised when ``missions.backend`` cannot be resolved to a usable store."""


def _build(instance: str, backend: str) -> MissionStore:
    if backend == "sqlite":
        from app.mission_store.sqlite_store import SqliteMissionStore
        return SqliteMissionStore(instance)
    # Anything else is a dotted import path: "module.path:ClassName".
    if ":" not in backend:
        raise MissionStoreConfigError(
            f"missions.backend '{backend}' is not a known backend ('sqlite') and is "
            "not a 'module:Class' import path. Fix missions.backend in config.")
    module_path, _, class_name = backend.partition(":")
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        raise MissionStoreConfigError(
            f"missions.backend '{backend}' could not be imported: {e}") from e
    store = cls(instance)
    if not isinstance(store, MissionStore):
        raise MissionStoreConfigError(
            f"missions.backend '{backend}' is not a MissionStore subclass.")
    return store


def get_mission_store(instance) -> MissionStore:
    """Return the configured mission store for ``instance`` (cached per process)."""
    key = str(Path(instance))
    cached = _cache.get(key)
    if cached is not None:
        return cached
    from app.config import get_mission_backend
    backend = get_mission_backend()
    store = _build(key, backend)
    _cache[key] = store
    logger.info("[mission_store] backend=%s instance=%s", store.backend_name(), key)
    return store


def reset_cache() -> None:
    """Clear the per-process store cache (tests / config reload)."""
    _cache.clear()
