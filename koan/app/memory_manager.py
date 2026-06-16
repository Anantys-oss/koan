#!/usr/bin/env python3
"""
Kōan — Memory manager

Handles memory scope isolation and periodic cleanup:
- Scoped summary: filter summary.md to only show relevant project sessions
- Summary compaction: keep last N sessions per project, archive older ones
- Learnings dedup: remove duplicate lines from learnings files
- Journal archival: compact old daily journals into monthly digests
- Learnings cap: truncate oversized learnings to keep most recent entries

Designed to scale: a 1-year instance with 20 runs/day across 3 projects
produces ~200K lines of journal. Without compaction, context loading and
git operations degrade. This module keeps growth bounded.

Usage from shell:
    python3 memory_manager.py <instance_dir> <command> [args...]

Commands:
    scoped-summary <project_name>   Print summary.md filtered to project-relevant sessions
    compact <max_sessions>          Compact summary.md, keeping last N sessions per date
    cleanup-learnings <project>     Remove duplicate lines from learnings.md
    archive-journals [days]         Archive journals older than N days (default 30)
    cleanup                         Run all cleanup tasks
"""

import contextlib
import fcntl
import hashlib
import json
import logging
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.utils import PROJECT_HINT_RE, atomic_write

logger = logging.getLogger(__name__)


def _log_memory_use(message: str) -> None:
    """Emit a memory-usage line to stderr so it lands in logs/run.log.

    Routed to stderr (never stdout) because the read paths also run inside CLI
    subprocess runners whose stdout carries JSON/transcript data. Best-effort:
    falls back to the stdlib logger if run_log is unavailable.
    """
    try:
        from app.run_log import log_safe
        log_safe("koan", message, force_stderr=True)
    except Exception:
        logger.info(message)


# Hermes-inspired anti-thrash threshold. When a compaction pass would save
# less than this fraction of the file (predicted from current size vs
# target), the pass is skipped — running it would burn lightweight-model
# quota for no practical gain. 10% is a sweet spot: large enough that the
# guard kicks in on already-tight files, small enough that genuinely
# bloated files still trigger compaction.
_ANTI_THRASH_MIN_SAVINGS_PCT = 0.10

# Files in memory/projects/{name}/ that must NEVER be compacted or
# semantically merged. These contain quantitative signals (e.g. markdown
# table rows) where LLM rewriting would destroy the data.
PROTECTED_PROJECT_FILES = frozenset({
    "skill-metrics.md",
})


def _should_skip_compaction(
    original_count: int,
    max_lines: int,
    content_hash: str,
    prior_state: Optional[Dict[str, object]],
) -> Optional[Dict[str, int]]:
    """Decide whether to skip compaction, returning the skip result or None.

    Checks three conditions in order:
    1. Below threshold — file doesn't need compaction.
    2. Hash match — content hasn't changed since last compaction.
    3. Anti-thrash guard — marginal savings don't justify a CLI call.
       Two flavours: growth-aware (preferred when prior telemetry exists)
       and target-distance (fallback for first-ever or legacy state).

    Returns a stats dict (with ``skipped=True``) when compaction should be
    skipped, or ``None`` when compaction should proceed.
    """
    base = {"original_lines": original_count, "compacted_lines": original_count, "skipped": True}

    # 1. Below threshold — nothing to compact.
    if original_count <= max_lines:
        return base

    # 2. Hash match — content unchanged since last successful compaction.
    if prior_state and prior_state.get("hash") == content_hash:
        return base

    # 3. Anti-thrash guard.
    prior_compacted = prior_state.get("compacted_lines") if prior_state else None
    if isinstance(prior_compacted, int) and prior_compacted > 0:
        # Growth-aware: skip if file grew less than threshold since last compaction.
        growth_pct = (original_count - prior_compacted) / prior_compacted
        if growth_pct < _ANTI_THRASH_MIN_SAVINGS_PCT:
            return {**base, "reason": "anti_thrash"}
    else:
        # Target-distance fallback: skip if predicted savings are marginal.
        predicted_savings_pct = (original_count - max_lines) / original_count
        if predicted_savings_pct < _ANTI_THRASH_MIN_SAVINGS_PCT:
            return {**base, "reason": "anti_thrash"}

    return None


# ---------------------------------------------------------------------------
# Pure parsing helpers (stateless, no instance_dir needed)
# ---------------------------------------------------------------------------

def parse_summary_sessions(content: str) -> List[Tuple[str, str, str]]:
    """Parse summary.md into (date_header, session_text, project_hint) tuples.

    Each entry is a paragraph under a ## date header. The project_hint is
    extracted from "(projet: X)" or "(project: X)" markers, or empty if none.
    """
    sessions = []
    current_date = ""
    current_lines: List[str] = []

    for line in content.splitlines():
        if line.startswith("## "):
            # Flush previous
            if current_lines and current_date:
                _flush_sessions(current_date, current_lines, sessions)
            current_date = line
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last
    if current_lines and current_date:
        _flush_sessions(current_date, current_lines, sessions)

    return sessions


def _flush_sessions(date_header: str, lines: List[str], sessions: list):
    """Split lines into individual session paragraphs and append to sessions."""
    current_paragraph: List[str] = []

    for line in lines:
        if line.strip() == "" and current_paragraph:
            text = "\n".join(current_paragraph)
            project = _extract_project_hint(text)
            sessions.append((date_header, text, project))
            current_paragraph = []
        elif line.strip():
            current_paragraph.append(line)

    if current_paragraph:
        text = "\n".join(current_paragraph)
        project = _extract_project_hint(text)
        sessions.append((date_header, text, project))


def _extract_project_hint(text: str) -> str:
    """Extract project name from session text like '(projet: koan)' or 'projet:koan'."""
    m = PROJECT_HINT_RE.search(text)
    if m:
        return m.group(1).lower()
    return ""


def _extract_session_digest(content: str) -> List[str]:
    """Extract a one-line digest per session from a journal file.

    Parses ## Session N headers and takes the first meaningful line after
    the ### sub-header (or the header itself if no sub-header).
    """
    digests = []
    current_header = ""
    found_sub = False

    for line in content.splitlines():
        if line.startswith("## Session") or line.startswith("## Mode"):
            if current_header and not found_sub:
                digests.append(current_header)
            current_header = line.strip()
            found_sub = False
        elif line.startswith("### ") and current_header:
            digests.append(f"{current_header} — {line.lstrip('#').strip()}")
            found_sub = True
            current_header = ""

    if current_header and not found_sub:
        digests.append(current_header)

    return digests


def _balanced_select(
    sessions: List[Tuple[str, str, str]],
    max_sessions: int,
    min_per_project: int = 2,
) -> List[Tuple[str, str, str]]:
    """Select sessions preserving per-project representation.

    Algorithm:
    1. Reserve the last ``min_per_project`` sessions for each project.
    2. If reserved count exceeds budget, fall back to 1 per project.
    3. Fill remaining budget with the most recent unreserved sessions.
    4. Return selected sessions in their original order.
    """
    by_project: Dict[str, List[int]] = defaultdict(list)
    for idx, (_date, _text, project) in enumerate(sessions):
        by_project[project].append(idx)

    # Phase 1: reserve last min_per_project per project
    kept_set = set()
    for indices in by_project.values():
        kept_set.update(indices[-min_per_project:])

    # Phase 2: if over budget, reduce to 1 per project
    if len(kept_set) > max_sessions:
        kept_set = set()
        for indices in by_project.values():
            kept_set.add(indices[-1])

    # Phase 3: fill remaining budget with most recent unreserved sessions
    remaining = max_sessions - len(kept_set)
    if remaining > 0:
        candidates = [i for i in range(len(sessions)) if i not in kept_set]
        for idx in candidates[-remaining:]:
            kept_set.add(idx)

    # Return in original order, capped at max_sessions
    selected = sorted(kept_set)[-max_sessions:]
    return [sessions[i] for i in selected]


def _rebuild_sessions(title: str, sessions: List[Tuple[str, str, ...]]) -> str:
    """Rebuild summary content from a title and list of session tuples."""
    output_lines = []
    if title:
        output_lines.append(title)
        output_lines.append("")

    current_date = ""
    for entry in sessions:
        date_header = entry[0]
        text = entry[1]
        if date_header != current_date:
            if current_date:
                output_lines.append("")
            output_lines.append(date_header)
            output_lines.append("")
            current_date = date_header
        output_lines.append(text)
        output_lines.append("")

    return "\n".join(output_lines).rstrip() + "\n"


_SNAPSHOT_SECTION_PREFIXES = (
    "## Summary",
    "## Global / ",
    "## Projects / ",
    "## Soul",
    "## Shared Journal",
)


def _is_snapshot_header(line: str) -> bool:
    """Check if a line is a snapshot section header (not a date header inside content)."""
    return any(line.startswith(p) for p in _SNAPSHOT_SECTION_PREFIXES)


def _parse_snapshot_sections(content: str) -> Dict[str, str]:
    """Parse a SNAPSHOT.md file into {section_name: section_content} dict.

    Only recognized snapshot section headers (Summary, Global/*, Projects/*,
    Soul, Shared Journal) are treated as boundaries. Date headers like
    ``## 2026-03-01`` inside the Summary section are preserved as content.
    """
    sections: Dict[str, str] = {}
    current_name = ""
    current_lines: List[str] = []

    for line in content.splitlines():
        if _is_snapshot_header(line):
            if current_name and current_lines:
                sections[current_name] = "\n".join(current_lines).strip() + "\n"
            current_name = line[3:].strip()
            current_lines = []
        elif current_name:
            current_lines.append(line)

    if current_name and current_lines:
        sections[current_name] = "\n".join(current_lines).strip() + "\n"

    return sections


def _extract_title(content: str) -> str:
    """Extract the # title line from summary content."""
    for line in content.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line
    return ""


def _read_compact_state(path: Path) -> Optional[Dict[str, object]]:
    """Read the per-project compaction state file.

    Returns a dict with at least ``hash`` and (when available)
    ``compacted_lines``. Returns ``None`` for a missing or unreadable
    file. Tolerates the legacy plain-string hash format (versions before
    the anti-thrash guard wrote just the hex digest) by treating it as a
    dict with only ``hash`` populated — callers can still short-circuit
    on hash match but get no growth telemetry until the next successful
    compaction rewrites the state in JSON form.

    Hardening: a state file containing valid JSON that isn't an object
    (``true``, ``[1,2,3]``, a bare number or string, etc.) is treated
    the same as legacy — wrapped so callers can safely call ``.get()``.
    The next successful compaction rewrites the file in canonical form.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Legacy format: plain hex digest. Wrap it for callers.
        return {"hash": raw}
    if not isinstance(parsed, dict):
        # Valid JSON, wrong shape (bool, list, number, string). Treat as
        # legacy so the caller's ``.get("hash")`` doesn't crash.
        return {"hash": raw}
    return parsed


def _write_compact_state(path: Path, content_hash: str, compacted_lines: int) -> None:
    """Persist the compaction state. Errors are swallowed (state is advisory)."""
    payload = {
        "hash": content_hash,
        "compacted_lines": int(compacted_lines),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with contextlib.suppress(OSError):
        atomic_write(path, json.dumps(payload) + "\n")


# ---------------------------------------------------------------------------
# MemoryManager class — encapsulates instance_dir state
# ---------------------------------------------------------------------------

class MemoryManager:
    """Manages memory operations for a koan instance directory.

    Encapsulates the instance_dir path so callers don't need to thread it
    through every function call. All operations are relative to this directory.
    """

    def __init__(self, instance_dir: str):
        self.instance_dir = Path(instance_dir)
        self.memory_dir = self.instance_dir / "memory"
        self.journal_dir = self.instance_dir / "journal"
        self.summary_path = self.memory_dir / "summary.md"
        self.projects_dir = self.memory_dir / "projects"

    def _learnings_path(self, project_name: str) -> Path:
        return self.projects_dir / project_name / "learnings.md"

    def scoped_summary(self, project_name: str) -> str:
        """Return summary.md content filtered to sessions relevant to a project.

        A session is relevant if:
        - It explicitly mentions the project (projet: X)
        - It has no project hint (pre-multi-project sessions, kept for all)
        """
        if not self.summary_path.exists():
            return ""

        content = self.summary_path.read_text()
        sessions = parse_summary_sessions(content)
        title = _extract_title(content)

        filtered = []
        project_lower = project_name.lower()
        for date_header, text, project_hint in sessions:
            if not project_hint or project_hint == project_lower:
                filtered.append((date_header, text))

        return _rebuild_sessions(title, filtered)

    def compact_summary(self, max_sessions: int = 10, min_per_project: int = 2) -> int:
        """Keep only the last N sessions in summary.md, preserving per-project balance.

        Without balancing, a burst of work on one project (e.g. 15 consecutive
        rebases) would evict ALL context for every other project.  This method
        guarantees each project retains at least ``min_per_project`` sessions
        (or 1, if the total budget is tight), then fills remaining slots with
        the most recent sessions overall.

        Returns the number of sessions removed.
        """
        if not self.summary_path.exists():
            return 0

        content = self.summary_path.read_text()
        sessions = parse_summary_sessions(content)

        if len(sessions) <= max_sessions:
            return 0

        title = _extract_title(content)
        kept = _balanced_select(sessions, max_sessions, min_per_project)
        removed = len(sessions) - len(kept)

        atomic_write(self.summary_path, _rebuild_sessions(title, kept))
        return removed

    def cleanup_learnings(self, project_name: str) -> int:
        """Remove duplicate lines from a project's learnings.md. Returns removed count."""
        learnings_path = self._learnings_path(project_name)
        if not learnings_path.exists():
            return 0

        try:
            content = learnings_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[memory_manager] Error reading {learnings_path}: {e}", file=sys.stderr)
            return 0

        lines = content.splitlines()

        seen = set()
        new_lines = []
        removed = 0

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped == "":
                new_lines.append(line)
                continue

            if stripped in seen:
                removed += 1
            else:
                seen.add(stripped)
                new_lines.append(line)

        if removed > 0:
            atomic_write(learnings_path, "\n".join(new_lines) + "\n")

        return removed

    def archive_journals(
        self,
        archive_after_days: int = 30,
        delete_after_days: int = 90,
    ) -> Dict[str, int]:
        """Archive old journal entries and delete very old raw journals.

        Strategy (3 tiers):
        - Recent (< archive_after_days): untouched
        - Mid-age (archive_after_days..delete_after_days): extract session digests
          into monthly archive files, then delete raw daily dirs
        - Old (> delete_after_days): delete raw daily dirs (archives kept forever)

        Returns dict with stats: archived_days, deleted_days, archive_lines.
        """
        if not self.journal_dir.exists():
            return {"archived_days": 0, "deleted_days": 0, "archive_lines": 0}

        today = date.today()
        archive_cutoff = today - timedelta(days=archive_after_days)
        delete_cutoff = today - timedelta(days=delete_after_days)

        archived_days = 0
        deleted_days = 0
        archive_lines = 0

        monthly: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        # Collect paths to delete AFTER archives are safely written
        to_delete_dirs: List[Tuple[Path, bool]] = []  # (path, is_old)
        to_delete_files: List[Tuple[Path, bool]] = []

        for entry in sorted(self.journal_dir.iterdir()):
            name = entry.name
            date_str = name.replace(".md", "") if name.endswith(".md") else name

            try:
                entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            if entry_date >= archive_cutoff:
                continue

            month_key = entry_date.strftime("%Y-%m")

            if entry.is_dir():
                for md_file in sorted(entry.glob("*.md")):
                    project = md_file.stem
                    try:
                        content = md_file.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError) as e:
                        print(f"[memory_manager] Error reading {md_file}: {e}", file=sys.stderr)
                        continue
                    digests = _extract_session_digest(content)
                    if digests:
                        monthly[(month_key, project)].extend(
                            [f"  {date_str}: {d}" for d in digests]
                        )
                        archive_lines += len(digests)

                is_old = entry_date < delete_cutoff
                to_delete_dirs.append((entry, is_old))

            elif entry.is_file() and entry.suffix == ".md":
                try:
                    content = entry.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as e:
                    print(f"[memory_manager] Error reading {entry}: {e}", file=sys.stderr)
                    continue
                digests = _extract_session_digest(content)
                if digests:
                    monthly[(month_key, "legacy")].extend(
                        [f"  {date_str}: {d}" for d in digests]
                    )
                    archive_lines += len(digests)

                is_old = entry_date < delete_cutoff
                to_delete_files.append((entry, is_old))

        # Write archives BEFORE deleting source files
        archives_dir = self.journal_dir / "archives"
        for (month, project), lines in monthly.items():
            month_dir = archives_dir / month
            month_dir.mkdir(parents=True, exist_ok=True)
            archive_file = month_dir / f"{project}.md"

            existing_content = ""
            existing = set()
            if archive_file.exists():
                existing_content = archive_file.read_text(encoding="utf-8")
                existing = set(existing_content.splitlines())

            new_lines = [l for l in lines if l not in existing]
            if new_lines:
                if existing_content:
                    full_content = existing_content.rstrip("\n") + "\n" + "\n".join(new_lines) + "\n"
                else:
                    full_content = f"# Journal archive — {project} — {month}\n\n" + "\n".join(new_lines) + "\n"
                atomic_write(archive_file, full_content)

        # Now safe to delete source files
        for path, is_old in to_delete_dirs:
            try:
                shutil.rmtree(path)
            except OSError as e:
                print(f"[memory_manager] Error deleting {path}: {e}", file=sys.stderr)
                continue
            if is_old:
                deleted_days += 1
            else:
                archived_days += 1

        for path, is_old in to_delete_files:
            try:
                path.unlink()
            except OSError as e:
                print(f"[memory_manager] Error deleting {path}: {e}", file=sys.stderr)
                continue
            if is_old:
                deleted_days += 1
            else:
                archived_days += 1

        return {
            "archived_days": archived_days,
            "deleted_days": deleted_days,
            "archive_lines": archive_lines,
        }

    def cap_learnings(self, project_name: str, max_lines: int = 200) -> int:
        """Truncate a learnings file to keep only the most recent entries.

        Keeps: the # header, then the last max_lines content lines.
        Returns number of lines removed.
        """
        learnings_path = self._learnings_path(project_name)
        if not learnings_path.exists():
            return 0

        try:
            content = learnings_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[memory_manager] Error reading {learnings_path}: {e}", file=sys.stderr)
            return 0

        lines = content.splitlines()

        headers = []
        content_lines = []
        in_header = True
        for line in lines:
            if in_header and (line.startswith("#") or line.strip() == ""):
                headers.append(line)
            else:
                in_header = False
                content_lines.append(line)

        if len(content_lines) <= max_lines:
            return 0

        removed = len(content_lines) - max_lines
        kept = content_lines[-max_lines:]

        result = headers + ["", f"_(oldest {removed} entries archived)_", ""] + kept
        atomic_write(learnings_path, "\n".join(result) + "\n")
        return removed

    def cap_global_memory(self, filename: str, max_lines: int = 150) -> int:
        """Truncate an append-only global memory file to keep recent entries.

        Same logic as cap_learnings but for files under memory/global/.
        Preserves the # header and keeps the last max_lines content lines.
        Only triggers when content exceeds the threshold.

        Returns number of lines removed.
        """
        filepath = self.memory_dir / "global" / filename
        if not filepath.exists():
            return 0

        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[memory_manager] Error reading {filepath}: {e}", file=sys.stderr)
            return 0

        lines = content.splitlines()

        headers = []
        content_lines = []
        in_header = True
        for line in lines:
            if in_header and (line.startswith("#") or line.strip() == ""):
                headers.append(line)
            else:
                in_header = False
                content_lines.append(line)

        if len(content_lines) <= max_lines:
            return 0

        removed = len(content_lines) - max_lines
        kept = content_lines[-max_lines:]

        result = headers + ["", f"_(oldest {removed} entries rotated)_", ""] + kept
        atomic_write(filepath, "\n".join(result) + "\n")
        return removed

    def compact_learnings(
        self,
        project_name: str,
        max_lines: int = 100,
        project_path: Optional[str] = None,
    ) -> Dict[str, int]:
        """Semantically compact a project's learnings using Claude CLI.

        Uses a lightweight model to merge redundant entries, remove obsolete
        ones (cross-referenced with the project's file tree), and consolidate
        by topic. Falls back to cap_learnings() if the Claude call fails.

        Args:
            project_name: Project whose learnings to compact.
            max_lines: Target number of content lines after compaction.
            project_path: Path to the project's git repo (for file tree).
                If None, attempts to resolve from projects.yaml.

        Returns:
            Dict with stats: original_lines, compacted_lines, skipped (bool).
        """
        learnings_path = self._learnings_path(project_name)
        if not learnings_path.exists():
            return {"original_lines": 0, "compacted_lines": 0, "skipped": True}

        try:
            content = learnings_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[memory_manager] Error reading {learnings_path}: {e}", file=sys.stderr)
            return {"original_lines": 0, "compacted_lines": 0, "skipped": True}

        # Count content lines (non-header, non-blank)
        lines = content.splitlines()
        content_lines = [l for l in lines if l.strip() and not l.startswith("#")]
        original_count = len(content_lines)

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        hash_path = self.instance_dir / f".koan-learnings-compact-hash-{project_name}"
        prior_state = _read_compact_state(hash_path)

        skip_result = _should_skip_compaction(original_count, max_lines, content_hash, prior_state)
        if skip_result is not None:
            return skip_result

        # Resolve project path for file tree
        if project_path is None:
            project_path = self._resolve_project_path(project_name)

        # Get file tree for cross-reference
        file_tree = self._get_file_tree(project_path)

        # Truncate input if very large (keep first 20 + last 500 lines)
        if len(lines) > 520:
            truncated_lines = lines[:20] + ["", "... (middle entries omitted) ...", ""] + lines[-500:]
            learnings_input = "\n".join(truncated_lines)
        else:
            learnings_input = content

        # Extract header for preservation
        header_lines = []
        for line in lines:
            if line.startswith("#") or (not line.strip() and not header_lines):
                header_lines.append(line)
            elif line.strip() == "" and header_lines:
                header_lines.append(line)
            else:
                break

        # Call Claude CLI for semantic compaction
        try:
            compacted = self._run_compaction_cli(learnings_input, file_tree, max_lines, project_path)
        except Exception as e:
            print(
                f"[memory_manager] Compaction CLI failed for {project_name}, "
                f"falling back to cap_learnings: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            # Fallback: just cap learnings
            self.cap_learnings(project_name, max_lines)
            return {
                "original_lines": original_count,
                "compacted_lines": max_lines,
                "skipped": False,
                "fallback": True,
                "method": "fallback",
                "error": str(e),
            }

        if not compacted or not compacted.strip():
            print(f"[memory_manager] Compaction returned empty for {project_name}, skipping", file=sys.stderr)
            return {"original_lines": original_count, "compacted_lines": original_count, "skipped": True}

        # Build result: header + compaction marker + compacted content
        compacted_lines = [l for l in compacted.splitlines() if l.strip()]
        compacted_count = len(compacted_lines)
        today = date.today().isoformat()

        result_parts = header_lines if header_lines else [f"# Learnings — {project_name}", ""]
        result_parts.append(f"_(compacted from {original_count} to {compacted_count} lines on {today})_")
        result_parts.append("")
        result_parts.append(compacted.strip())
        result_parts.append("")

        atomic_write(learnings_path, "\n".join(result_parts))

        # Persist state (hash + last-compacted size) so future calls can
        # both short-circuit on unchanged content AND apply the anti-thrash
        # guard based on growth since the last successful compaction.
        new_content = learnings_path.read_text(encoding="utf-8")
        new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        _write_compact_state(hash_path, new_hash, compacted_count)

        return {"original_lines": original_count, "compacted_lines": compacted_count, "skipped": False, "method": "semantic"}

    def compact_security_learnings(
        self,
        project_name: str,
        max_lines: int = 100,
        project_path: Optional[str] = None,
    ) -> Dict[str, int]:
        """Semantically compact a project's security_learnings.md using Claude CLI.

        Mirrors compact_learnings() but targets the security-specific file
        at instance/memory/projects/{project_name}/security_learnings.md and
        uses the security-learnings-compaction prompt to preserve category
        and trust-level metadata during merge.

        Falls back to plain truncation if the Claude call fails.

        Args:
            project_name: Project whose security learnings to compact.
            max_lines: Target number of content lines after compaction.
            project_path: Path to the project's git repo.
                If None, attempts to resolve from projects.yaml.

        Returns:
            Dict with stats: original_lines, compacted_lines, skipped (bool).
        """
        security_path = (
            self.projects_dir / project_name / "security_learnings.md"
        )
        if not security_path.exists():
            return {"original_lines": 0, "compacted_lines": 0, "skipped": True}

        try:
            content = security_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(
                f"[memory_manager] Error reading {security_path}: {e}",
                file=sys.stderr,
            )
            return {"original_lines": 0, "compacted_lines": 0, "skipped": True}

        lines = content.splitlines()
        content_lines = [l for l in lines if l.strip() and not l.startswith("#")]
        original_count = len(content_lines)

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        hash_path = self.instance_dir / f".koan-security-compact-hash-{project_name}"
        prior_state = _read_compact_state(hash_path)

        skip_result = _should_skip_compaction(original_count, max_lines, content_hash, prior_state)
        if skip_result is not None:
            return skip_result

        if project_path is None:
            project_path = self._resolve_project_path(project_name)

        try:
            compacted = self._run_security_compaction_cli(content, max_lines, project_path)
        except Exception as e:
            print(
                f"[memory_manager] Security compaction CLI failed for {project_name}, "
                f"truncating instead: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
            # Fallback: truncate to last max_lines content lines
            kept = content_lines[-max_lines:]
            header_lines = [l for l in lines if l.startswith("#")]
            result = (header_lines or ["# Security Intelligence", ""]) + [""] + kept + [""]
            atomic_write(security_path, "\n".join(result))
            return {
                "original_lines": original_count,
                "compacted_lines": min(original_count, max_lines),
                "skipped": False,
                "fallback": True,
                "method": "fallback",
                "error": str(e),
            }

        if not compacted or not compacted.strip():
            print(
                f"[memory_manager] Security compaction returned empty for {project_name}, skipping",
                file=sys.stderr,
            )
            return {"original_lines": original_count, "compacted_lines": original_count, "skipped": True}

        compacted_lines = [l for l in compacted.splitlines() if l.strip()]
        compacted_count = len(compacted_lines)
        today = date.today().isoformat()

        header_lines = []
        for line in lines:
            if line.startswith("#") or (not line.strip() and not header_lines):
                header_lines.append(line)
            elif line.strip() == "" and header_lines:
                header_lines.append(line)
            else:
                break

        result_parts = header_lines if header_lines else ["# Security Intelligence", ""]
        result_parts.append(
            f"_(compacted from {original_count} to {compacted_count} lines on {today})_"
        )
        result_parts.append("")
        result_parts.append(compacted.strip())
        result_parts.append("")

        atomic_write(security_path, "\n".join(result_parts))

        new_content = security_path.read_text(encoding="utf-8")
        new_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        _write_compact_state(hash_path, new_hash, compacted_count)

        return {
            "original_lines": original_count,
            "compacted_lines": compacted_count,
            "skipped": False,
            "method": "semantic",
        }

    def _run_security_compaction_cli(
        self,
        security_content: str,
        max_lines: int,
        project_path: Optional[str],
    ) -> str:
        """Run Claude CLI with the security-learnings-compaction prompt."""
        from app.cli_provider import build_full_command
        from app.config import get_model_config
        from app.prompts import load_prompt

        prompt = load_prompt(
            "security-learnings-compaction",
            SECURITY_CONTENT=security_content,
            MAX_LINES=str(max_lines),
        )
        models = get_model_config()

        cmd = build_full_command(
            prompt=prompt,
            allowed_tools=[],
            model=models.get("lightweight", "haiku"),
            fallback=models.get("fallback", "sonnet"),
            max_turns=1,
        )

        from app.cli_exec import run_cli_with_retry

        cwd = project_path or "."
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=120, cwd=cwd,
        )
        if result.returncode != 0:
            raise RuntimeError(f"CLI returned {result.returncode}: {result.stderr[:200]}")
        return result.stdout.strip()

    def _resolve_project_path(self, project_name: str) -> Optional[str]:
        """Resolve a project's filesystem path from projects.yaml."""
        try:
            import os
            from app.projects_config import load_projects_config, get_projects_from_config
            koan_root = os.environ.get("KOAN_ROOT", "")
            if not koan_root:
                return None
            config = load_projects_config(koan_root)
            if not config:
                return None
            for name, path in get_projects_from_config(config):
                if name.lower() == project_name.lower():
                    return path
        except Exception as e:
            print(f"[memory_manager] project path resolution error: {e}", file=sys.stderr)
        return None

    def _get_file_tree(self, project_path: Optional[str]) -> str:
        """Get file tree from a project using git ls-files."""
        if not project_path:
            return "(project path not available)"
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                capture_output=True, text=True, timeout=10,
                cwd=project_path,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            print(f"[memory_manager] git ls-files timed out for {project_path}", file=sys.stderr)
        except OSError as e:
            print(f"[memory_manager] git ls-files failed for {project_path}: {e}", file=sys.stderr)
        return "(file tree not available)"

    def _run_compaction_cli(
        self, learnings_content: str, file_tree: str, max_lines: int,
        project_path: Optional[str],
    ) -> str:
        """Run Claude CLI to semantically compact learnings."""
        from app.cli_provider import build_full_command
        from app.config import get_model_config
        from app.prompts import load_prompt

        prompt = load_prompt(
            "learnings-compaction",
            LEARNINGS_CONTENT=learnings_content,
            FILE_TREE=file_tree,
            MAX_LINES=str(max_lines),
        )
        models = get_model_config()

        cmd = build_full_command(
            prompt=prompt,
            allowed_tools=[],
            model=models.get("lightweight", "haiku"),
            fallback=models.get("fallback", "sonnet"),
            max_turns=1,
        )

        from app.cli_exec import run_cli_with_retry

        cwd = project_path or "."
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=180, cwd=cwd,
        )
        if result.returncode != 0:
            raise RuntimeError(f"CLI returned {result.returncode}: {result.stderr[:200]}")
        return result.stdout.strip()

    def export_snapshot(self) -> Path:
        """Export critical memory state to memory/SNAPSHOT.md.

        Assembles a portable snapshot from:
        - memory/summary.md (last 20 sessions)
        - memory/global/* files
        - memory/projects/*/learnings.md (per project, capped at 200 lines)
        - soul.md (from instance root)
        - shared-journal.md (last 50 lines)

        Returns the path to the written snapshot file.
        """
        sections = []

        # Metadata header
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        project_names = []
        if self.projects_dir.exists() and self.projects_dir.is_dir():
            project_names = sorted(
                d.name for d in self.projects_dir.iterdir()
                if d.is_dir() and d.name != "_template"
            )
        sections.append("# Kōan Memory Snapshot\n")
        sections.append(f"Exported: {now}")
        sections.append(f"Projects: {', '.join(project_names) if project_names else 'none'}")
        sections.append("")

        # Summary (last 20 sessions)
        sections.append("## Summary\n")
        if self.summary_path.exists():
            content = self.summary_path.read_text(encoding="utf-8")
            all_sessions = parse_summary_sessions(content)
            title = _extract_title(content)
            kept = all_sessions[-20:] if len(all_sessions) > 20 else all_sessions
            sections.append(_rebuild_sessions(title, kept).strip())
        sections.append("")

        # Global memory files
        global_dir = self.memory_dir / "global"
        global_files = [
            "personality-evolution.md", "emotional-memory.md", "genesis.md",
            "strategy.md", "human-preferences.md", "draft-bot.md",
        ]
        for filename in global_files:
            filepath = global_dir / filename
            if filepath.exists():
                try:
                    content = filepath.read_text(encoding="utf-8").strip()
                    if content:
                        stem = filepath.stem
                        sections.append(f"## Global / {stem}\n")
                        sections.append(content)
                        sections.append("")
                except (OSError, UnicodeDecodeError):
                    pass

        # Per-project learnings
        for project_name in project_names:
            learnings_path = self._learnings_path(project_name)
            if learnings_path.exists():
                try:
                    lines = learnings_path.read_text(encoding="utf-8").splitlines()
                    # Cap at 200 lines
                    if len(lines) > 200:
                        lines = lines[:5] + ["", "_(truncated to last 200 lines)_", ""] + lines[-200:]
                    content = "\n".join(lines).strip()
                    if content:
                        sections.append(f"## Projects / {project_name} / learnings\n")
                        sections.append(content)
                        sections.append("")
                except (OSError, UnicodeDecodeError):
                    pass

        # Per-project protected files (quantitative, never compacted)
        for project_name in project_names:
            for protected_file in sorted(PROTECTED_PROJECT_FILES):
                pf_path = self.projects_dir / project_name / protected_file
                if pf_path.exists():
                    try:
                        content = pf_path.read_text(encoding="utf-8").strip()
                        if content:
                            stem = pf_path.stem
                            sections.append(f"## Projects / {project_name} / {stem}\n")
                            sections.append(content)
                            sections.append("")
                    except (OSError, UnicodeDecodeError):
                        pass

        # Soul
        soul_path = self.instance_dir / "soul.md"
        if soul_path.exists():
            try:
                content = soul_path.read_text(encoding="utf-8").strip()
                if content:
                    sections.append("## Soul\n")
                    sections.append(content)
                    sections.append("")
            except (OSError, UnicodeDecodeError):
                pass

        # Shared journal (last 50 lines)
        journal_path = self.instance_dir / "shared-journal.md"
        if journal_path.exists():
            try:
                lines = journal_path.read_text(encoding="utf-8").splitlines()
                kept_lines = lines[-50:] if len(lines) > 50 else lines
                content = "\n".join(kept_lines).strip()
                if content:
                    sections.append("## Shared Journal\n")
                    sections.append(content)
                    sections.append("")
            except (OSError, UnicodeDecodeError):
                pass

        snapshot_content = "\n".join(sections).rstrip() + "\n"
        snapshot_path = self.memory_dir / "SNAPSHOT.md"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(snapshot_path, snapshot_content)
        return snapshot_path

    def hydrate_from_snapshot(self, force: bool = False) -> Dict[str, bool]:
        """Rebuild memory files from SNAPSHOT.md.

        Looks for SNAPSHOT.md in memory/ first, then instance root as fallback.
        Parses structured sections and recreates missing files.

        When force=False (default), existing files are skipped. When force=True,
        all files are written unconditionally via atomic_write (temp+rename), so
        the write itself is race-free on POSIX even under concurrent access.

        Returns dict mapping restored file paths (relative) to True, or empty
        if no snapshot found.
        """
        snapshot_path = self.memory_dir / "SNAPSHOT.md"
        if not snapshot_path.exists():
            # Fallback: check instance root
            snapshot_path = self.instance_dir / "SNAPSHOT.md"
        if not snapshot_path.exists():
            return {}

        try:
            content = snapshot_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[memory_manager] Error reading snapshot: {e}", file=sys.stderr)
            return {}

        sections = _parse_snapshot_sections(content)
        restored = {}

        # Restore summary
        if "Summary" in sections:
            if force or not self.summary_path.exists():
                self.memory_dir.mkdir(parents=True, exist_ok=True)
                atomic_write(self.summary_path, sections["Summary"])
                restored["memory/summary.md"] = True

        # Restore global files
        global_dir = self.memory_dir / "global"
        for key, text in sections.items():
            if key.startswith("Global / "):
                stem = key[len("Global / "):]
                filepath = global_dir / f"{stem}.md"
                if force or not filepath.exists():
                    global_dir.mkdir(parents=True, exist_ok=True)
                    atomic_write(filepath, text)
                    restored[f"memory/global/{stem}.md"] = True

        # Restore per-project learnings
        for key, text in sections.items():
            if key.startswith("Projects / ") and key.endswith(" / learnings"):
                project_name = key[len("Projects / "):-len(" / learnings")]
                learnings_path = self._learnings_path(project_name)
                if force or not learnings_path.exists():
                    learnings_path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(learnings_path, text)
                    restored[f"memory/projects/{project_name}/learnings.md"] = True

        # Restore soul.md
        if "Soul" in sections:
            soul_path = self.instance_dir / "soul.md"
            if force or not soul_path.exists():
                atomic_write(soul_path, sections["Soul"])
                restored["soul.md"] = True

        # Restore shared journal
        if "Shared Journal" in sections:
            journal_path = self.instance_dir / "shared-journal.md"
            if force or not journal_path.exists():
                atomic_write(journal_path, sections["Shared Journal"])
                restored["shared-journal.md"] = True

        for path in sorted(restored.keys()):
            print(f"[memory_manager] Hydrated: {path}")

        return restored

    # -----------------------------------------------------------------------
    # One-shot migration from markdown to JSONL
    # -----------------------------------------------------------------------

    def migrate_markdown_to_jsonl(self) -> dict:
        """Populate memory/log.jsonl from existing summary.md and learnings.md files.

        Runs once, gated by the presence of memory/.migration_done sentinel.
        Idempotent: subsequent calls return immediately if sentinel exists.

        Returns a dict with counts of migrated entries by type.
        """
        sentinel = self.memory_dir / ".migration_done"
        if sentinel.exists():
            return {"skipped": True}

        self.memory_dir.mkdir(parents=True, exist_ok=True)

        stats: Dict[str, int] = {"sessions": 0, "learnings": 0}

        # Migrate summary.md → type=session entries
        if self.summary_path.exists():
            try:
                content = self.summary_path.read_text(encoding="utf-8")
                sessions = parse_summary_sessions(content)
                for date_header, text, project_hint in sessions:
                    # Derive a rough timestamp from the date header (## YYYY-MM-DD)
                    ts = None
                    parts = date_header.lstrip("#").strip().split()
                    for part in parts:
                        try:
                            datetime.strptime(part, "%Y-%m-%d")
                            ts = part + "T00:00:00Z"
                            break
                        except ValueError:
                            continue
                    project = project_hint or None
                    self.append_memory_entry("session", project, text, ts=ts)
                    stats["sessions"] += 1
            except Exception as e:
                logger.warning("Migration of summary.md failed: %s", e)

        # Migrate learnings.md files → type=learning entries
        if self.projects_dir.exists():
            for project_dir in self.projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                learnings_path = project_dir / "learnings.md"
                if not learnings_path.exists():
                    continue
                try:
                    mtime = learnings_path.stat().st_mtime
                    ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    content = learnings_path.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#"):
                            continue
                        self.append_memory_entry("learning", project_dir.name, stripped, ts=ts)
                        stats["learnings"] += 1
                except Exception as e:
                    logger.warning("Migration of %s/learnings.md failed: %s", project_dir.name, e)

        # Write sentinel to prevent re-migration
        atomic_write(sentinel, "done\n")

        # NOTE: SQLite FTS5 indexing is NOT done here. This method is gated by
        # the .migration_done sentinel and short-circuits for any instance that
        # already migrated markdown→JSONL — so wiring the SQLite bulk import here
        # would never run on existing instances. Indexing is a separate, always-run
        # startup step (startup_manager.index_memory_sqlite), self-gated on an
        # empty/missing memory.db so it is cheap and idempotent.

        return stats

    # -----------------------------------------------------------------------
    # JSONL truth log
    # -----------------------------------------------------------------------

    @property
    def _log_path(self) -> Path:
        return self.memory_dir / "log.jsonl"

    def append_memory_entry(
        self,
        type_: str,
        project: Optional[str],
        content: str,
        ts: Optional[str] = None,
    ) -> None:
        """Append one entry to memory/log.jsonl (append-only truth log).

        Uses O(1) file append with ``fcntl.flock(LOCK_EX)`` so concurrent
        callers never lose entries.  Content is capped at 2000 chars to
        prevent runaway diffs from inflating the log.
        """
        entry = {
            "ts": ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": type_,
            "project": project,
            "content": content[:2000],
        }
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        new_line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._log_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(new_line)

        # Dual-write: mirror to SQLite FTS5 index (best-effort)
        try:
            from app.memory_db import ensure_db, insert_entry
            conn = ensure_db(str(self.instance_dir))
            if conn is not None:
                try:
                    insert_entry(conn, entry)
                finally:
                    conn.close()
        except Exception as e:
            logger.warning("[memory_manager] SQLite dual-write failed: %s", e)

    def read_memory_window(
        self,
        project: Optional[str],
        max_entries: int = 20,
        query_text: str = "",
    ) -> List[dict]:
        """Return the most relevant ``max_entries`` log entries for a project.

        When ``query_text`` is non-empty, uses two-phase retrieval:
        (1) FTS5-matched entries ranked by BM25, (2) recency fill for
        remaining slots.  When ``query_text`` is empty or SQLite is
        unavailable, falls back to JSONL tail (recency only).

        Includes entries where ``project`` matches (case-insensitive) OR where
        ``project`` is null/absent (global entries).  Malformed lines are
        silently skipped.  Returns entries in chronological order (oldest first).
        """
        # Two-phase retrieval when query_text provided
        if query_text.strip():
            try:
                from app.memory_db import ensure_db, search_entries, recent_entries
                conn = ensure_db(str(self.instance_dir))
                if conn is not None:
                    try:
                        fts_results = search_entries(
                            conn, project or "", query_text, max_results=max_entries,
                        )
                        fts_match_count = len(fts_results)
                        def _dedup_key(e):
                            return (e.get("ts", ""), (e.get("content") or "")[:80])

                        seen = {_dedup_key(e) for e in fts_results}
                        remaining = max_entries - len(fts_results)
                        if remaining > 0:
                            recency = recent_entries(
                                conn, project or "", max_results=remaining + len(fts_results),
                            )
                            for e in recency:
                                key = _dedup_key(e)
                                if key not in seen:
                                    fts_results.append(e)
                                    seen.add(key)
                                    if len(fts_results) >= max_entries:
                                        break
                    finally:
                        conn.close()
                    if fts_results:
                        fts_results.sort(key=lambda e: e.get("ts", ""))
                        _log_memory_use(
                            "[memory] FTS5 surfaced %d/%d entries for %s "
                            "(%d ranked match, %d recency fill) — query=%r"
                            % (
                                len(fts_results), max_entries, project or "global",
                                fts_match_count, len(fts_results) - fts_match_count,
                                query_text[:60],
                            )
                        )
                        return fts_results
            except Exception as e:
                logger.warning("[memory_manager] FTS5 retrieval failed, falling back to JSONL: %s", e)

        # Fallback: JSONL tail (recency only)
        if not self._log_path.exists():
            return []
        try:
            raw = self._log_path.read_text(encoding="utf-8")
        except OSError:
            return []

        project_lower = project.lower() if project else None
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_project = obj.get("project")
            if entry_project is None:
                entries.append(obj)
            elif project_lower and entry_project.lower() == project_lower:
                entries.append(obj)

        # Return the max_entries most recent (tail), oldest-first order
        return entries[-max_entries:]

    def prune_memory_log(self, horizon_days: int = 365) -> int:
        """Remove log entries older than ``horizon_days``. Returns removed count.

        The full read-filter-write cycle holds ``flock(LOCK_EX)`` on the log
        file so a concurrent ``append_memory_entry`` cannot lose data.
        """
        if not self._log_path.exists():
            return 0
        try:
            f = open(self._log_path, "r+", encoding="utf-8")
        except OSError:
            return 0

        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            raw = f.read()

            cutoff = datetime.now(timezone.utc) - timedelta(days=horizon_days)
            kept = []
            removed = 0
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ts_str = obj.get("ts", "")
                    # Parse ISO8601 timestamp; keep entries with unparseable ts
                    try:
                        ts_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        if ts_dt < cutoff:
                            removed += 1
                            continue
                    except ValueError:
                        pass
                    kept.append(line)
                except json.JSONDecodeError:
                    kept.append(line)  # preserve malformed lines rather than lose them

            if removed > 0:
                f.seek(0)
                f.truncate()
                f.write("\n".join(kept) + "\n" if kept else "")
        finally:
            f.close()

        # Mirror deletion to SQLite FTS5 index (best-effort)
        if removed > 0:
            try:
                from app.memory_db import ensure_db, delete_before
                conn = ensure_db(str(self.instance_dir))
                if conn is not None:
                    try:
                        cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
                        delete_before(conn, cutoff_iso)
                    finally:
                        conn.close()
            except Exception as e:
                logger.warning("[memory_manager] SQLite prune mirror failed: %s", e)

        return removed

    def run_cleanup(
        self,
        max_sessions: int = 15,
        archive_after_days: int = 30,
        delete_after_days: int = 90,
        max_learnings_lines: int = 200,
        compact_learnings_lines: int = 100,
        global_personality_max: int = 150,
        global_emotional_max: int = 100,
        log_horizon_days: int = 365,
    ) -> dict:
        """Run all cleanup tasks. Returns stats dict."""
        stats = {}
        stats["summary_compacted"] = self.compact_summary(max_sessions)

        if self.projects_dir.exists() and self.projects_dir.is_dir():
            for project_dir in self.projects_dir.iterdir():
                if project_dir.is_dir():
                    name = project_dir.name
                    # Step 1: dedup exact duplicates
                    removed = self.cleanup_learnings(name)
                    if removed > 0:
                        stats[f"learnings_dedup_{name}"] = removed
                    # Step 2: semantic compaction (Claude-powered)
                    try:
                        compact_stats = self.compact_learnings(name, compact_learnings_lines)
                        if not compact_stats.get("skipped"):
                            method = " (fallback)" if compact_stats.get("fallback") else ""
                            stats[f"learnings_compacted_{name}"] = (
                                f"{compact_stats['original_lines']}->{compact_stats['compacted_lines']}{method}"
                            )
                    except Exception as e:
                        print(f"[memory_manager] Compaction failed for {name}: {e}", file=sys.stderr)
                    # Step 2b: compact security learnings
                    try:
                        sec_stats = self.compact_security_learnings(name, compact_learnings_lines)
                        if not sec_stats.get("skipped"):
                            method = " (fallback)" if sec_stats.get("fallback") else ""
                            stats[f"security_compacted_{name}"] = (
                                f"{sec_stats['original_lines']}->{sec_stats['compacted_lines']}{method}"
                            )
                    except Exception as e:
                        print(
                            f"[memory_manager] Security compaction failed for {name}: {e}",
                            file=sys.stderr,
                        )
                    # Step 3: hard cap as safety net
                    capped = self.cap_learnings(name, max_learnings_lines)
                    if capped > 0:
                        stats[f"learnings_capped_{name}"] = capped

        # Cap append-only global memory files
        _GLOBAL_CAPS = {
            "personality-evolution.md": global_personality_max,
            "emotional-memory.md": global_emotional_max,
        }
        for filename, cap in _GLOBAL_CAPS.items():
            capped = self.cap_global_memory(filename, cap)
            if capped > 0:
                stem = filename.replace(".md", "").replace("-", "_")
                stats[f"global_capped_{stem}"] = capped

        journal_stats = self.archive_journals(archive_after_days, delete_after_days)
        stats.update(journal_stats)

        # Prune JSONL truth log
        try:
            pruned = self.prune_memory_log(log_horizon_days)
            if pruned > 0:
                stats["log_pruned"] = pruned
        except Exception as e:
            logger.warning("Log pruning failed: %s", e)

        # Export snapshot after cleanup (reflects clean state)
        try:
            snapshot_path = self.export_snapshot()
            stats["snapshot_exported"] = snapshot_path.stat().st_size
        except Exception as e:
            print(f"[memory_manager] Snapshot export failed: {e}", file=sys.stderr)

        return stats


# ---------------------------------------------------------------------------
# Module-level functions (backward compatibility)
# ---------------------------------------------------------------------------

def scoped_summary(instance_dir: str, project_name: str) -> str:
    """Return summary.md content filtered to sessions relevant to a project."""
    return MemoryManager(instance_dir).scoped_summary(project_name)


def compact_summary(instance_dir: str, max_sessions: int = 10, min_per_project: int = 2) -> int:
    """Keep only the last N sessions in summary.md. Returns removed count."""
    return MemoryManager(instance_dir).compact_summary(max_sessions, min_per_project)


def cleanup_learnings(instance_dir: str, project_name: str) -> int:
    """Remove duplicate lines from a project's learnings.md. Returns removed count."""
    return MemoryManager(instance_dir).cleanup_learnings(project_name)


def archive_journals(
    instance_dir: str,
    archive_after_days: int = 30,
    delete_after_days: int = 90,
) -> Dict[str, int]:
    """Archive old journal entries and delete very old raw journals."""
    return MemoryManager(instance_dir).archive_journals(archive_after_days, delete_after_days)


def cap_learnings(instance_dir: str, project_name: str, max_lines: int = 200) -> int:
    """Truncate a learnings file to keep only the most recent entries."""
    return MemoryManager(instance_dir).cap_learnings(project_name, max_lines)


def compact_learnings(
    instance_dir: str, project_name: str, max_lines: int = 100,
    project_path: Optional[str] = None,
) -> Dict[str, int]:
    """Semantically compact a project's learnings using Claude CLI."""
    return MemoryManager(instance_dir).compact_learnings(
        project_name, max_lines, project_path
    )


def run_cleanup(
    instance_dir: str,
    max_sessions: int = 15,
    archive_after_days: int = 30,
    delete_after_days: int = 90,
    max_learnings_lines: int = 200,
    compact_learnings_lines: int = 100,
    global_personality_max: int = 150,
    global_emotional_max: int = 100,
    log_horizon_days: int = 365,
) -> dict:
    """Run all cleanup tasks. Returns stats dict."""
    return MemoryManager(instance_dir).run_cleanup(
        max_sessions, archive_after_days, delete_after_days,
        max_learnings_lines, compact_learnings_lines,
        global_personality_max, global_emotional_max,
        log_horizon_days=log_horizon_days,
    )


def append_memory_entry(
    instance_dir: str,
    type_: str,
    project: Optional[str],
    content: str,
    ts: Optional[str] = None,
) -> None:
    """Append one entry to memory/log.jsonl."""
    MemoryManager(instance_dir).append_memory_entry(type_, project, content, ts)


def read_memory_window(
    instance_dir: str,
    project: Optional[str],
    max_entries: int = 20,
    query_text: str = "",
) -> List[dict]:
    """Return the most relevant log entries for a project."""
    return MemoryManager(instance_dir).read_memory_window(
        project, max_entries, query_text=query_text,
    )


def prune_memory_log(instance_dir: str, horizon_days: int = 365) -> int:
    """Remove log entries older than horizon_days. Returns removed count."""
    return MemoryManager(instance_dir).prune_memory_log(horizon_days)


def migrate_markdown_to_jsonl(instance_dir: str) -> dict:
    """One-shot migration from markdown memory to JSONL truth log."""
    return MemoryManager(instance_dir).migrate_markdown_to_jsonl()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <instance_dir> <command> [args...]",
            file=sys.stderr,
        )
        print(
            "Commands: scoped-summary <project>, compact [max], "
            "cleanup-learnings <project>, compact-learnings [project], "
            "archive-journals [days], cleanup, "
            "snapshot, hydrate",
            file=sys.stderr,
        )
        sys.exit(1)

    instance = sys.argv[1]
    command = sys.argv[2]
    mgr = MemoryManager(instance)

    if command == "scoped-summary":
        if len(sys.argv) < 4:
            print("Error: project name required", file=sys.stderr)
            sys.exit(1)
        print(mgr.scoped_summary(sys.argv[3]))

    elif command == "compact":
        max_s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        removed = mgr.compact_summary(max_s)
        print(f"Compacted: {removed} sessions removed")

    elif command == "cleanup-learnings":
        if len(sys.argv) < 4:
            print("Error: project name required", file=sys.stderr)
            sys.exit(1)
        removed = mgr.cleanup_learnings(sys.argv[3])
        print(f"Deduped: {removed} lines removed")

    elif command == "compact-learnings":
        if len(sys.argv) < 4:
            # Compact all projects
            if mgr.projects_dir.exists():
                for project_dir in mgr.projects_dir.iterdir():
                    if project_dir.is_dir():
                        name = project_dir.name
                        stats = mgr.compact_learnings(name)
                        print(f"  {name}: {stats}")
            else:
                print("No projects directory found")
        else:
            project = sys.argv[3]
            stats = mgr.compact_learnings(project)
            for k, v in stats.items():
                print(f"  {k}: {v}")

    elif command == "archive-journals":
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        stats = mgr.archive_journals(archive_after_days=days)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    elif command == "cleanup":
        max_s = int(sys.argv[3]) if len(sys.argv) > 3 else 15
        stats = mgr.run_cleanup(max_s)
        for k, v in stats.items():
            print(f"  {k}: {v}")

    elif command == "snapshot":
        path = mgr.export_snapshot()
        size = path.stat().st_size
        print(f"Snapshot exported to {path} ({size} bytes)")

    elif command == "hydrate":
        force = "--force" in sys.argv
        restored = mgr.hydrate_from_snapshot(force=force)
        if restored:
            for p in sorted(restored.keys()):
                print(f"  Restored: {p}")
            print(f"Hydrated {len(restored)} file(s)")
        else:
            print("No snapshot found or nothing to restore")

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)
