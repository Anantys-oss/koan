"""Lazy memory accessor for SkillContext.

Wraps :mod:`app.skill_memory` (read) and :mod:`app.memory_manager` (write/search)
behind a small read/write/search API.  Zero cost when unused — the underlying
MemoryManager is created only on first write or search call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class MemoryAccessor:
    """Unified memory facade exposed via ``SkillContext.memory``.

    Read paths delegate to :mod:`app.skill_memory` helpers (no MemoryManager
    needed).  Write/search paths lazily construct a single
    :class:`app.memory_manager.MemoryManager`.
    """

    __slots__ = ("_instance_dir", "_manager")

    def __init__(self, instance_dir: Path) -> None:
        self._instance_dir = instance_dir
        self._manager = None

    def _get_manager(self):
        if self._manager is None:
            from app.memory_manager import MemoryManager
            self._manager = MemoryManager(str(self._instance_dir))
        return self._manager

    def read_learnings(
        self,
        project: str,
        task_text: str = "",
        *,
        max_k: Optional[int] = None,
    ) -> str:
        """Return filtered learnings for *project*, scored against *task_text*.

        Delegates to :func:`app.skill_memory._load_filtered_learnings`.
        Returns ``""`` when the project has no learnings or the project name
        is empty/invalid.
        """
        if not project:
            return ""
        from app.skill_memory import (
            _is_safe_project_name,
            _load_filtered_learnings,
            _load_recall_defaults,
        )
        if not _is_safe_project_name(project):
            return ""
        max_learnings, recent_hedge = _load_recall_defaults()
        if max_k is not None:
            max_learnings = max_k
        result = _load_filtered_learnings(
            str(self._instance_dir), project, task_text, max_learnings, recent_hedge,
        )
        return result or ""

    def read_context(self, project: str) -> str:
        """Return human-curated ``context.md`` content for *project*.

        Returns ``""`` when missing or empty.
        """
        if not project:
            return ""
        from app.skill_memory import (
            _CONTEXT_CAP_LINES,
            _is_safe_project_name,
            _read_capped,
        )
        if not _is_safe_project_name(project):
            return ""
        path = Path(self._instance_dir) / "memory" / "projects" / project / "context.md"
        return _read_capped(path, _CONTEXT_CAP_LINES)

    def read_block(
        self,
        project: str,
        task_text: str = "",
        *,
        max_learnings: Optional[int] = None,
        title: str = "Project Memory",
    ) -> str:
        """Return a full formatted memory block (context + priorities + learnings).

        Delegates to :func:`app.skill_memory.build_memory_block`.
        Drop-in replacement for ``build_memory_block()`` (not
        ``build_memory_block_for_skill()``, which additionally resolves the
        project name from the registry). Pass an already-resolved project name.
        Returns ``""`` when the project name is empty or no memory exists.
        """
        if not project:
            return ""
        from app.skill_memory import build_memory_block
        kwargs = {}
        if max_learnings is not None:
            kwargs["max_learnings"] = max_learnings
        return build_memory_block(
            str(self._instance_dir), project, task_text, title=title, **kwargs,
        )

    def append(
        self,
        type_: str,
        content: str,
        project: str = "",
    ) -> None:
        """Write an entry to the JSONL memory log.

        Empty *project* is recorded as a global (``None``) entry.
        """
        self._get_manager().append_memory_entry(
            type_, project or None, content,
        )

    def search(
        self,
        query: str,
        project: str = "",
        max_results: int = 10,
    ) -> List[dict]:
        """FTS5-ranked search over the memory log.

        Empty *project* searches global entries only.
        """
        return self._get_manager().read_memory_window(
            project or None, max_entries=max_results, query_text=query,
        )
