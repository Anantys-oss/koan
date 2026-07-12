"""Capture outcome-attributed experience entries for fix/implement/review missions.

Experience entries are structured memory records that capture the single most
valuable artifact the agent produces: 'for issue X the root cause was Y and
approach Z worked (or failed).' Unlike flat session lines, they carry typed
fields (outcome, mission_kind, root_cause, approach, artifact) that make them
queryable by later recall/reflect work.

All writes go through the existing append_memory_entry dual-write path.
This module is purely additive -- it never blocks the caller's flow.
"""

import re
import sys
from typing import Optional

from app.memory_manager import append_memory_entry


def _classify_mission_kind(mission_title: str) -> Optional[str]:
    """Map a mission title to fix/implement/review, or None to suppress capture.

    Uses keyword dispatch consistent with session_tracker.classify_mission_type
    but collapses to the three mission kinds that produce actionable experience.
    Returns None for non-code missions (chat, rebase, analysis, etc.), which
    suppresses capture entirely.
    """
    if not mission_title or not mission_title.strip():
        return None
    lower = mission_title.lower()

    # /fix missions
    if "/fix" in lower:
        return "fix"
    # /implement, /implement_*, /ai missions
    if "/implement" in lower or "/ai " in lower:
        return "implement"
    # /review, /review_rebase missions
    if "/review" in lower:
        return "review"
    # Freetext detection (autonomous missions without a / prefix). Word-boundary
    # matching avoids substring misfires (e.g. "debug" -> "bug"). Review is
    # checked before fix so "review the broken parser" is tagged review, not fix.
    if re.search(r"\breview\b", lower):
        return "review"
    if any(re.search(rf"\b{kw}\b", lower) for kw in ("fix", "bug", "broken", "crash")):
        return "fix"
    if any(re.search(rf"\b{kw}\b", lower) for kw in ("implement", "feature", "build")):
        return "implement"

    return None


# Markers that commonly precede a root-cause statement in an agent journal.
_ROOT_CAUSE_MARKERS = (
    "root cause",
    "underlying cause",
    "caused by",
    "the cause",
    "root of the problem",
)

# Markers that commonly precede a description of the fix/approach taken.
_APPROACH_MARKERS = (
    "approach",
    "solution",
    "the fix",
    "fixed by",
    "resolved by",
    "changes made",
    "how i fixed",
)


def _extract_marked_text(text: str, markers) -> str:
    """Return the text following the first line containing any marker.

    Best-effort: scans line by line; when a line contains a marker, returns
    the remainder of that line after the marker plus any immediately
    following non-blank lines, joined and truncated to 500 chars. Returns
    '' when no marker is found.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        for m in markers:
            idx = low.find(m)
            if idx == -1:
                continue
            rest = line[idx + len(m):].lstrip(" \t:.-–—>")
            collected = [rest] if rest else []
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    break
                collected.append(nxt.strip())
            joined = " ".join(p for p in collected if p).strip()
            if joined:
                return joined[:500]
    return ""


def _summarize_journal(text: str) -> str:
    """Truncated summary fallback: first substantive paragraph of the journal."""
    for para in re.split(r"\n\s*\n", text.strip()):
        cleaned = " ".join(para.split())
        # Skip pure headings / short markers that carry no real content.
        if len(cleaned) >= 40:
            return cleaned[:500]
    flat = " ".join(text.split())
    return flat[:500]


def _extract_root_cause_approach(journal_content: str):
    """Best-effort extraction of (root_cause, approach) from journal content.

    Used on seams (e.g. the primary mission path) where the caller has no
    typed root_cause/approach to hand. A truncated summary is better than an
    empty structured field, so ``approach`` falls back to a journal summary
    when no explicit marker is present. ``root_cause`` stays '' when no
    marker is found -- guessing a cause from arbitrary prose is unreliable.
    """
    if not journal_content or not journal_content.strip():
        return "", ""
    root_cause = _extract_marked_text(journal_content, _ROOT_CAUSE_MARKERS)
    approach = _extract_marked_text(journal_content, _APPROACH_MARKERS)
    if not approach:
        approach = _summarize_journal(journal_content)
    return root_cause, approach


def _is_significant_for_capture(
    mission_title: str,
    duration_minutes: int,
    journal_content: str,
    mission_kind: Optional[str],
    exit_code: int,
) -> bool:
    """Gate capture to prevent low-signal entries from flooding the log.

    Reuses the post_mission_reflection significance heuristic for successes.
    Failures are always captured (when a mission kind is detected) since
    they are the highest-signal data.
    """
    # Failures are always significant -- they're the highest-signal data
    if exit_code != 0:
        return mission_kind is not None

    # For successes, require either significance heuristics or a minimum duration
    try:
        from app.post_mission_reflection import is_significant_mission
        if is_significant_mission(mission_title, duration_minutes, journal_content):
            return True
    except Exception as e:
        print(
            f"[experience_capture] warn: is_significant_mission failed, "
            f"using duration fallback: {e}",
            file=sys.stderr,
        )

    # Also capture when we have a mission kind AND reasonable substance
    return mission_kind is not None and duration_minutes >= 5


def capture_experience(
    instance_dir: str,
    project_name: str,
    mission_title: str,
    exit_code: int,
    outcome: str,
    verify_result=None,
    root_cause: str = "",
    approach: str = "",
    artifact: str = "",
    duration_minutes: int = 0,
    journal_content: str = "",
    force: bool = False,
) -> None:
    """Capture a structured experience entry (fire-and-forget, never raises).

    Args:
        instance_dir: Path to instance directory.
        project_name: Project name.
        mission_title: Mission description.
        exit_code: CLI exit code (0 = success, non-zero = failure).
        outcome: One of 'success', 'failed', 'reverted'.
        verify_result: Optional VerifyResult from mission verification.
            Its .passed attribute is folded into the content string.
        root_cause: Description of the root cause (best-effort, may be empty).
        approach: What approach was taken (best-effort, may be empty).
        artifact: PR/commit reference (best-effort, may be empty).
        duration_minutes: Mission duration for significance gating.
        journal_content: Journal content for significance gating; also mined
            for best-effort root_cause/approach when the caller supplies none.
        force: Bypass the significance gate. Used for seams that are
            inherently significant but carry no duration signal -- notably a
            successful CI fix, which lands with duration_minutes=0 and would
            otherwise be dropped by the >=5-minute floor.
    """
    try:
        mission_kind = _classify_mission_kind(mission_title)
        if mission_kind is None:
            return

        if not force and not _is_significant_for_capture(
            mission_title, duration_minutes, journal_content,
            mission_kind, exit_code,
        ):
            return

        # On seams that hand us no typed root_cause/approach (the primary
        # mission path), mine them best-effort from the journal so the
        # headline "root cause was Y and approach Z" data is not left empty.
        if journal_content and (not root_cause or not approach):
            extracted_rc, extracted_ap = _extract_root_cause_approach(journal_content)
            if not root_cause:
                root_cause = extracted_rc
            if not approach:
                approach = extracted_ap

        # Build content string (capped at 2000 chars by append_memory_entry)
        parts = [f"[{mission_kind}] {mission_title}"]
        parts.append(f"Outcome: {outcome}")

        if verify_result is not None:
            verify_status = "verified" if verify_result.passed else "verification failed"
            summary = getattr(verify_result, "summary", "")
            if summary:
                verify_status += f" ({summary})"
            parts.append(verify_status)

        if root_cause:
            parts.append(f"Root cause: {root_cause}")
        if approach:
            parts.append(f"Approach: {approach}")
        if artifact:
            parts.append(f"Artifact: {artifact}")

        content = " | ".join(parts)

        append_memory_entry(
            instance_dir, "experience", project_name or None, content,
            outcome=outcome,
            mission_kind=mission_kind,
            root_cause=root_cause or None,
            approach=approach or None,
            artifact=artifact or None,
        )
    except Exception as e:
        print(f"[experience_capture] error: Experience capture failed: {e}", file=sys.stderr)

