"""Mission storage behind a pluggable :class:`MissionStore` port.

Public surface:

- :func:`get_mission_store` — resolve the configured backend (single read path).
- :class:`MissionStore`, :class:`Mission`, :class:`IngestReport`,
  :class:`RecoverReport` — the domain contract.

See ``specs/004-mission-store/`` for the design and contract.
"""

from app.mission_store.base import (
    TERMINAL_STATES,
    VALID_STATES,
    IngestReport,
    Mission,
    MissionStore,
    RecoverReport,
)
from app.mission_store.resolver import (
    MissionStoreConfigError,
    get_mission_store,
    reset_cache,
)

__all__ = [
    "Mission",
    "MissionStore",
    "IngestReport",
    "RecoverReport",
    "VALID_STATES",
    "TERMINAL_STATES",
    "get_mission_store",
    "reset_cache",
    "MissionStoreConfigError",
]
