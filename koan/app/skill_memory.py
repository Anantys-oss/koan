"""Kōan — Shared project-memory injection helper.

Single source of truth for "give me a memory block for this project, scoped
to this task." Used by both the agent loop (via :mod:`app.prompt_builder`)
and the mission-driving skills (`/fix`, `/plan`, `/implement`, `/refactor`,
`/review`).

Three sources are merged into one block:

* ``memory/projects/{name}/learnings.md`` — agent-grown, machine-compacted.
  Filtered with Jaccard similarity against the task text (same scoring as
  :mod:`app.memory_recall`).
* ``memory/projects/{name}/context.md`` — human-curated project context
  (architecture, ongoing initiatives). Loaded verbatim, line-capped.
* ``memory/projects/{name}/priorities.md`` — human-curated priorities and
  no-touch zones. Loaded verbatim, line-capped.

The block is wrapped in ``<memory-context>`` fences so it's visually and
semantically distinct in the model's view — inspired by the Hermes-agent
memory-provider pattern.

Returns ``""`` when every source is missing or empty — callers should
substitute the placeholder unconditionally and let the empty string render
as nothing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from app.prompt_guard import fence_external_data

logger = logging.getLogger(__name__)

# Verbatim caps for the human-curated files. Kept generous (these files are
# small by design) but bounded so a runaway operator doesn't accidentally
# blow out the prompt budget.
_CONTEXT_CAP_LINES = 80
_PRIORITIES_CAP_LINES = 40


def _is_safe_project_name(project_name: str) -> bool:
    """Reject project names that could escape the memory tree.

    Today every caller derives ``project_name`` from operator-controlled
    config (``projects.yaml``) or a git directory basename, neither of
    which contains path separators. This guard is defensive: a future
    caller that passes untrusted input must not be able to read or
    create files outside ``memory/projects/``.

    Rejects:
        * empty / whitespace
        * any path separator (``/`` or ``\\``)
        * any ``..`` segment (parent-directory traversal)
        * leading ``.`` (would resolve to dotfile dirs)
    """
    if not project_name or not project_name.strip():
        return False
    if "/" in project_name or "\\" in project_name:
        return False
    if project_name.startswith("."):
        return False
    # Path.parts splits on the platform separator; ``..`` as a literal
    # segment is the parent-dir traversal we care about.
    return ".." not in Path(project_name).parts


def _read_capped(path: Path, max_lines: int) -> str:
    """Read a small text file, truncating to ``max_lines`` from the top.

    Returns ``""`` for missing files, empty files, or read errors. A
    truncation marker is appended when the file was actually truncated so
    the model knows content was elided.
    """
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[skill_memory] read failed for %s: %s", path, e)
        return ""

    stripped = text.strip()
    if not stripped:
        return ""

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.rstrip()

    kept = lines[:max_lines]
    kept.append(f"\n_(truncated — {len(lines) - max_lines} more lines in {path.name})_")
    return "\n".join(kept)


def _load_filtered_learnings(
    instance: str,
    project_name: str,
    task_text: str,
    max_k: int,
    recent_hedge: int,
) -> Optional[str]:
    """Return rendered learnings sub-block, or ``None`` if nothing to inject.

    Honours the ``[recall:full]`` escape hatch in ``task_text``: when present,
    the entire ``learnings.md`` file is included verbatim.
    """
    path = Path(instance) / "memory" / "projects" / project_name / "learnings.md"
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("[skill_memory] learnings read failed: %s", e)
        return None

    if not content.strip():
        return None

    from app.memory_recall import has_recall_full_tag, score_and_select

    if has_recall_full_tag(task_text):
        body = content.rstrip()
        return (
            "## Learnings (full, [recall:full] override)\n\n"
            f"{body}"
        )

    selected, total, _dropped = score_and_select(
        content, task_text, max_k=max_k, recent_hedge=recent_hedge,
    )
    if not selected:
        return None

    header = (
        f"## Learnings (filtered — {len(selected)} of {total})\n\n"
        "Ranked by relevance to the current task. Use the `[recall:full]` "
        "tag in your task description to bypass filtering.\n\n"
    )
    return header + "\n".join(selected)


def _read_int(mapping: dict, key: str, fallback: int) -> int:
    """Read a non-negative int from ``mapping[key]``, defaulting on any failure."""
    try:
        value = int(mapping.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    return max(0, value)


def load_recall_config(default_max: int, default_hedge: int) -> tuple[int, int]:
    """Return ``(max_k, recent_hedge)`` from ``config.yaml`` ``memory:`` block.

    Public cross-module API: shared by the agent loop (via
    :mod:`app.prompt_builder`) and the skill-injection helpers, since
    only the fallback defaults differ between callers. Keys read:
    ``memory.max_relevant_learnings`` and ``memory.recall_recent_hedge``.
    Both values are clamped to ``>= 0``.
    """
    try:
        from app.utils import load_config
        cfg = load_config() or {}
    except (ImportError, OSError, ValueError) as e:
        logger.warning("[skill_memory] recall config load failed: %s", e)
        return default_max, default_hedge
    mem = cfg.get("memory", {}) or {}
    return (
        _read_int(mem, "max_relevant_learnings", default_max),
        _read_int(mem, "recall_recent_hedge", default_hedge),
    )


def _load_recall_defaults() -> tuple[int, int]:
    """Skill-side defaults: tighter than the agent loop (25 vs 40, 3 vs 5)
    because skill prompts are already dense with issue body / plan content.
    """
    return load_recall_config(default_max=25, default_hedge=3)


# Default cap for the total assembled memory block, in lines. Sized to match
# the on-disk learnings hard cap (``cap_learnings`` default 200) so the clamp
# is operator-misconfig protection rather than a routine trim of well-behaved
# instances. Override via ``memory.max_block_lines`` in ``config.yaml``.
_DEFAULT_MAX_BLOCK_LINES = 200


def _load_max_block_lines() -> int:
    """Return ``memory.max_block_lines`` from ``config.yaml`` (default 200).

    Mirrors :func:`load_recall_config`'s coercion: non-int / negative values
    fall back to the default, config-load failures fall back to the default.
    """
    try:
        from app.utils import load_config
        cfg = load_config() or {}
    except (ImportError, OSError, ValueError) as e:
        logger.warning("[skill_memory] max_block_lines config load failed: %s", e)
        return _DEFAULT_MAX_BLOCK_LINES
    mem = cfg.get("memory", {}) or {}
    return _read_int(mem, "max_block_lines", _DEFAULT_MAX_BLOCK_LINES)


def _truncate_part(part: str, drop_n: int) -> tuple[str, int]:
    """Drop up to ``drop_n`` lines from the bottom of ``part``.

    Each part begins with a ``## Header`` line and at least one blank line.
    Preserves a minimum of (header + blank + 1 content line) so a clamped
    part still carries something readable; if fewer than 3 lines remain
    after trimming, the part is left alone (caller continues to the next).

    Returns ``(truncated_part, actually_dropped)``.
    """
    if drop_n <= 0:
        return part, 0
    lines = part.splitlines()
    if len(lines) <= 3:
        return part, 0
    max_droppable = len(lines) - 3
    actual_drop = min(drop_n, max_droppable)
    if actual_drop <= 0:
        return part, 0
    kept = lines[: len(lines) - actual_drop]
    return "\n".join(kept), actual_drop


def _clamp_to_max_lines(parts: list[str], max_lines: int) -> tuple[list[str], int]:
    """Clamp the total line count by truncating parts in reverse order.

    Earlier parts are higher-priority (most curated). Truncation drops
    content lines from the bottom of later parts first (learnings →
    priorities → context), preserving each part's sub-header so the
    model still sees what kind of content was present.

    Returns ``(clamped_parts, lines_dropped)``. ``lines_dropped`` is 0
    when no clamp was needed.
    """
    if max_lines <= 0:
        return parts, 0
    total = sum(len(p.splitlines()) for p in parts)
    if total <= max_lines:
        return parts, 0

    excess = total - max_lines
    out = list(parts)
    for i in range(len(out) - 1, -1, -1):
        if excess <= 0:
            break
        truncated, dropped = _truncate_part(out[i], excess)
        if dropped > 0:
            out[i] = truncated
            excess -= dropped

    kept_total = sum(len(p.splitlines()) for p in out)
    return out, total - kept_total


def build_memory_block(
    instance: str,
    project_name: str,
    task_text: str,
    *,
    max_learnings: Optional[int] = None,
    recent_hedge: Optional[int] = None,
    title: str = "Project Memory",
) -> str:
    """Assemble the project-memory injection block for a skill or agent prompt.

    Args:
        instance: Path to the Kōan instance directory.
        project_name: Project slug used under ``memory/projects/``.
        task_text: The text used to score learnings relevance. For skills this
            is typically the issue title + body or the branch name; for the
            agent loop it's the mission title or focus-area string.
        max_learnings: Override for the learnings line budget. ``None`` uses
            ``config.yaml`` ``memory.max_relevant_learnings`` (default 25).
        recent_hedge: Override for the always-keep-recent budget. ``None``
            uses ``config.yaml`` ``memory.recall_recent_hedge`` (default 3).
        title: Heading for the rendered block. Default ``"Project Memory"``;
            the agent loop passes ``"Project Learnings"`` for backward
            compatibility with the existing section it emits.

    Returns:
        A multi-line string starting with two newlines (so it concatenates
        cleanly onto an existing prompt) and ending with one newline. Wraps
        the content in ``<memory-context>`` fences. Returns ``""`` when no
        memory source produced any content.
    """
    if not instance or not project_name:
        return ""
    if not _is_safe_project_name(project_name):
        logger.warning(
            "[skill_memory] rejected unsafe project_name=%r — refusing to "
            "build memory block (would escape memory/projects/ tree)",
            project_name,
        )
        return ""

    cfg_max, cfg_hedge = _load_recall_defaults()
    eff_max = cfg_max if max_learnings is None else max_learnings
    eff_hedge = cfg_hedge if recent_hedge is None else recent_hedge

    project_dir = Path(instance) / "memory" / "projects" / project_name
    context_text = _read_capped(project_dir / "context.md", _CONTEXT_CAP_LINES)
    priorities_text = _read_capped(project_dir / "priorities.md", _PRIORITIES_CAP_LINES)
    learnings_block = _load_filtered_learnings(
        instance, project_name, task_text, eff_max, eff_hedge,
    )

    # Build parts in curation order: context (most curated) → priorities →
    # learnings (lowest-confidence). Verbatim human-curated files are wrapped
    # in ``fence_external_data`` so an accidental prompt-injection payload
    # in those files is neutralised — agent-generated learnings stay raw.
    parts: list[str] = []
    sources_present: list[str] = []
    if context_text:
        fenced_ctx = fence_external_data(context_text, "context.md")
        parts.append(f"## Context (human-curated)\n\n{fenced_ctx}")
        sources_present.append("context")
    if priorities_text:
        fenced_prio = fence_external_data(priorities_text, "priorities.md")
        parts.append(f"## Priorities (human-curated)\n\n{fenced_prio}")
        sources_present.append("priorities")
    if learnings_block:
        parts.append(learnings_block)
        sources_present.append("learnings")

    if not parts:
        return ""

    # Apply the global block clamp before assembling. Tail-truncates parts
    # in reverse-curation order so a runaway ``context.md`` or oversized
    # learnings recall can't blow out the per-mission prompt budget.
    max_block = _load_max_block_lines()
    original_total = sum(len(p.splitlines()) for p in parts)
    parts, lines_dropped = _clamp_to_max_lines(parts, max_block)
    kept_total = original_total - lines_dropped

    body = "\n\n".join(parts)
    if lines_dropped > 0:
        body += (
            f"\n\n_(memory block clamped from {original_total} to {kept_total} "
            f"lines — raise memory.max_block_lines in config.yaml to see more)_"
        )
        logger.warning(
            "[skill_memory] memory block clamped: %d → %d lines (project=%s)",
            original_total, kept_total, project_name,
        )

    per_source = dict(zip(sources_present, (len(p.splitlines()) for p in parts)))
    logger.info(
        "[skill_memory] block built: lines=%d (ctx=%d prio=%d learn=%d) project=%s",
        kept_total,
        per_source.get("context", 0),
        per_source.get("priorities", 0),
        per_source.get("learnings", 0),
        project_name,
    )

    return (
        f"\n\n<memory-context>\n# {title}\n\n"
        f"{body}\n"
        f"</memory-context>\n"
    )


def _resolve_project_name_from_path(koan_root: str, project_path: str) -> str:
    """Reverse-resolve ``project_path`` to the project name in ``projects.yaml``.

    The agent loop receives ``project_name`` from ``projects.yaml`` while
    skill runners only have the repo path on disk. If we trust the
    basename here, operators whose configured project slug differs from
    the repo directory name (common: ``path: ~/code/koan-fork`` mapped to
    name ``koan``) silently get no memory injected.

    Strategy:
        1. Load ``projects.yaml``; for each ``(name, path)`` entry, expand
           ``~`` and resolve symlinks on both sides.
        2. Return the configured ``name`` whose resolved path matches.
        3. On any failure (no config, lookup error, no match) fall back
           to ``Path(project_path).name`` — same behaviour as before, so
           operators relying on basename-matching see no regression.
    """
    basename = Path(project_path).name
    if not koan_root:
        return basename

    try:
        from app.projects_config import get_projects_from_config, load_projects_config
        config = load_projects_config(koan_root)
        if not config:
            return basename
        try:
            target = Path(project_path).expanduser().resolve()
        except OSError:
            return basename
        for name, path in get_projects_from_config(config):
            try:
                candidate = Path(path).expanduser().resolve()
            except OSError:
                continue
            if candidate == target:
                return name
        # Loop completed without a match — projects.yaml loaded fine but
        # the path on disk isn't registered. This is the silent-drift case:
        # the basename fallback may point at a memory/projects/<slug>/ that
        # doesn't exist, in which case memory loads as empty with no clue
        # for the operator. Emit a warning so it shows up in logs.
        logger.warning(
            "[skill_memory] project_path=%r not found in projects.yaml — "
            "using basename %r; memory may not load if your project slug "
            "differs from the directory name",
            project_path, basename,
        )
    except (ImportError, OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("[skill_memory] project_name resolution fell back to basename: %s", e)

    return basename


def build_memory_block_for_skill(project_path: str, task_text: str, **kwargs) -> str:
    """Resolve instance + project_name from environment and delegate.

    Convenience wrapper used by skill runners (`/fix`, `/plan`, `/implement`,
    `/refactor`, `/review`) so each runner doesn't have to repeat the
    ``KOAN_ROOT`` lookup. Falls back to ``""`` when ``KOAN_ROOT`` is unset
    (i.e. the skill is being invoked outside a Kōan instance, e.g. from a
    standalone test or one-off CLI invocation).

    The project name is resolved by matching ``project_path`` against
    ``projects.yaml`` first (matching the agent loop), then falling back
    to ``Path(project_path).name`` if no match is found. Pre-existing
    setups where the configured name equals the repo directory name keep
    working unchanged.
    """
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        logger.info(
            "[skill_memory] KOAN_ROOT unset — skipping memory injection "
            "(standalone invocation for project_path=%r)",
            project_path,
        )
        return ""
    instance = str(Path(koan_root) / "instance")
    project_name = _resolve_project_name_from_path(koan_root, project_path)
    return build_memory_block(instance, project_name, task_text, **kwargs)
