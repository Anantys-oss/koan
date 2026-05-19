"""Security intelligence layer for the /security_audit skill.

Accumulates reusable vulnerability patterns, exploit chain heuristics,
remediation strategies, and false-positive signatures across audits,
isolated per repository.

Storage layout:
  Global intelligence:  instance/memory/security/global_learnings.md
  Per-project:          instance/memory/projects/{name}/security_learnings.md
  Trust tracker:        instance/memory/security/.trust-tracker.json

Trust levels:
  ephemeral  — first seen in a single audit session
  verified   — seen in ≥ 2 sessions for the same project
  trusted    — verified globally across ≥ 2 different projects
"""

import fcntl
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({
    "detection_pattern",
    "exploitation_heuristic",
    "remediation_knowledge",
    "framework_weakness",
    "historical_false_positive",
})

VALID_TRUST_LEVELS = frozenset({"ephemeral", "verified", "trusted"})

# Injection priority order for categories (lower index = higher priority)
CATEGORY_PRIORITY = [
    "detection_pattern",
    "remediation_knowledge",
    "framework_weakness",
    "exploitation_heuristic",
    "historical_false_positive",
]

# Maximum lines injected into audit prompt
MAX_INJECTION_LINES = 150


@dataclass
class SecurityLearning:
    """A single security intelligence entry."""

    category: str          # one of VALID_CATEGORIES
    trust_level: str       # one of VALID_TRUST_LEVELS
    content: str           # the learning text
    source: str            # e.g. "audit-session", "human-review"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    scope: str = "local"   # "local" (project-specific) or "global"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _global_security_dir(instance_dir: str) -> Path:
    return Path(instance_dir) / "memory" / "security"


def _global_learnings_path(instance_dir: str) -> Path:
    return _global_security_dir(instance_dir) / "global_learnings.md"


def _project_security_path(instance_dir: str, project_name: str) -> Path:
    return (
        Path(instance_dir) / "memory" / "projects" / project_name / "security_learnings.md"
    )


def _trust_tracker_path(instance_dir: str) -> Path:
    return _global_security_dir(instance_dir) / ".trust-tracker.json"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _format_learning(learning: SecurityLearning) -> str:
    """Serialize a SecurityLearning to a markdown bullet line with metadata."""
    return (
        f"- [{learning.category}][{learning.trust_level}] {learning.content}"
        f"  <!-- source:{learning.source} created:{learning.created_at} scope:{learning.scope} -->"
    )


def _learning_core(line: str) -> str:
    """Strip metadata comment for dedup comparison."""
    return line.split("  <!--")[0].strip()


# ---------------------------------------------------------------------------
# Write / Read
# ---------------------------------------------------------------------------

def write_security_learning(
    instance_dir: str,
    project_name: str,
    learning: SecurityLearning,
) -> None:
    """Append a SecurityLearning to the appropriate file (atomic write).

    Global-scope learnings go to global_learnings.md; local ones go to
    the per-project security_learnings.md.  Exact-string dedup prevents
    double-writing the same entry.
    """
    from app.utils import atomic_write

    if learning.scope == "global":
        path = _global_learnings_path(instance_dir)
    else:
        path = _project_security_path(instance_dir, project_name)

    path.parent.mkdir(parents=True, exist_ok=True)

    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            existing = ""

    line = _format_learning(learning)
    line_core = _learning_core(line)

    for existing_line in existing.splitlines():
        if _learning_core(existing_line) == line_core:
            return  # already present

    if not existing:
        header = "# Security Intelligence\n\n"
        new_content = header + line + "\n"
    else:
        new_content = existing.rstrip("\n") + "\n" + line + "\n"

    atomic_write(path, new_content)


def read_security_learnings(
    instance_dir: str,
    project_name: str,
    global_only: bool = False,
) -> str:
    """Read security learnings, combining global + per-project content.

    Args:
        instance_dir: Path to the instance directory.
        project_name: Project name for scoped learnings.
        global_only: If True, return only global learnings.

    Returns:
        Combined markdown text, or empty string when no learnings exist.
    """
    parts = []

    global_path = _global_learnings_path(instance_dir)
    if global_path.exists():
        try:
            parts.append(global_path.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError) as e:
            log.warning("[security_learnings] Error reading global learnings: %s", e)

    if global_only:
        return "\n\n".join(parts) if parts else ""

    project_path = _project_security_path(instance_dir, project_name)
    if project_path.exists():
        try:
            parts.append(project_path.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeDecodeError) as e:
            log.warning(
                "[security_learnings] Error reading project learnings for %s: %s",
                project_name, e,
            )

    return "\n\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Trust escalation
# ---------------------------------------------------------------------------

def _read_trust_tracker(instance_dir: str) -> dict:
    """Read trust tracker JSON, returning empty dict on error."""
    path = _trust_tracker_path(instance_dir)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.warning("[security_learnings] Trust tracker read error, resetting: %s", e)
        return {}


def _write_trust_tracker(instance_dir: str, data: dict) -> None:
    """Write trust tracker JSON atomically."""
    from app.utils import atomic_write_json

    path = _trust_tracker_path(instance_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)


def _learning_key(content: str) -> str:
    """Compute a stable 16-char dedup key for a learning's content."""
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()[:16]


def escalate_trust(
    instance_dir: str,
    project_name: str,
    learnings: List[SecurityLearning],
) -> List[SecurityLearning]:
    """Update trust levels based on session recurrence.

    Rules:
    - ephemeral → verified: same content key seen for ≥ 2 distinct projects
      (or ≥ 2 sessions for the same project, tracked as repeated project entries)
    - verified → trusted: same content key seen across ≥ 2 different projects
      in the global tracker

    Updates the tracker on disk and returns the learnings with updated
    trust_level fields.  Tracker corruption is handled gracefully.
    """
    tracker = _read_trust_tracker(instance_dir)

    # project_sessions: {key: [project_name, ...]} — list of projects that saw this key
    project_sessions: dict = tracker.get("project_sessions", {})
    global_projects: dict = tracker.get("global_projects", {})

    updated = []
    for learning in learnings:
        key = _learning_key(learning.content)

        sessions = project_sessions.get(key, [])
        if project_name not in sessions:
            sessions = sessions + [project_name]
        project_sessions[key] = sessions

        all_projects = set(sessions)
        global_projects[key] = list(all_projects)

        # Escalate
        if learning.trust_level == "ephemeral" and len(sessions) >= 2:
            learning.trust_level = "verified"
        elif learning.trust_level == "verified" and len(all_projects) >= 2:
            learning.trust_level = "trusted"

        updated.append(learning)

    tracker["project_sessions"] = project_sessions
    tracker["global_projects"] = global_projects
    _write_trust_tracker(instance_dir, tracker)

    return updated


# ---------------------------------------------------------------------------
# Extraction pipeline
# ---------------------------------------------------------------------------

def extract_security_learnings(
    audit_output: str,
    project_name: str,
    instance_dir: str,
    project_path: str,
) -> List[SecurityLearning]:
    """Extract security learnings from completed audit output.

    Calls a lightweight Claude CLI invocation with the extraction prompt,
    parses the structured response, escalates trust, and persists to disk.

    Args:
        audit_output: Raw text output from the audit session.
        project_name: Project name for scoping.
        instance_dir: Path to the instance directory.
        project_path: Path to the project repo (used as cwd for CLI call).

    Returns:
        List of SecurityLearning entries written (may be empty).
    """
    if not audit_output or not audit_output.strip():
        return []

    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_skill_prompt

    skill_dir = Path(__file__).parent

    try:
        prompt = load_skill_prompt(
            skill_dir,
            "security_learnings_extraction",
            AUDIT_OUTPUT=audit_output,
            PROJECT_NAME=project_name,
        )
    except FileNotFoundError:
        log.warning("[security_learnings] Extraction prompt not found, skipping")
        return []

    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    from app.cli_exec import run_cli_with_retry

    try:
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=60, cwd=project_path,
        )
        if result.returncode != 0:
            log.warning(
                "[security_learnings] Extraction CLI failed: %s",
                (result.stderr or result.stdout)[:200],
            )
            return []
        raw = result.stdout.strip()
    except Exception as e:
        log.warning("[security_learnings] Extraction error: %s", e)
        return []

    learnings = _parse_extraction_output(raw)
    if not learnings:
        return []

    learnings = escalate_trust(instance_dir, project_name, learnings)

    for learning in learnings:
        write_security_learning(instance_dir, project_name, learning)

    return learnings


def _parse_extraction_output(raw: str) -> List[SecurityLearning]:
    """Parse Claude extraction output into SecurityLearning entries.

    Expected format per entry (blocks separated by ``---LEARNING---``):

        CATEGORY: detection_pattern
        TRUST: ephemeral
        SCOPE: local|global
        CONTENT: <learning text>
        SOURCE: audit-session
    """
    entries = []
    blocks = raw.split("---LEARNING---")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        learning = _parse_learning_block(block)
        if learning:
            entries.append(learning)
    return entries


def _parse_learning_block(block: str) -> Optional[SecurityLearning]:
    """Parse a single ---LEARNING--- block into a SecurityLearning."""
    fields: dict = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().upper()] = value.strip()

    category = fields.get("CATEGORY", "").lower()
    trust = fields.get("TRUST", "ephemeral").lower()
    scope = fields.get("SCOPE", "local").lower()
    content = fields.get("CONTENT", "").strip()
    source = fields.get("SOURCE", "audit-session").strip()

    if not content:
        return None
    if category not in VALID_CATEGORIES:
        category = "detection_pattern"
    if trust not in VALID_TRUST_LEVELS:
        trust = "ephemeral"
    if scope not in ("local", "global"):
        scope = "local"

    return SecurityLearning(
        category=category,
        trust_level=trust,
        content=content,
        source=source,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Prompt injection block
# ---------------------------------------------------------------------------

def build_security_memory_block(
    instance_dir: str,
    project_name: str,
) -> str:
    """Build the ## Security Intelligence block for injection into audit prompts.

    Only verified and trusted learnings are included (ephemeral entries are
    excluded — they haven't been confirmed across sessions yet).  Output is
    capped at MAX_INJECTION_LINES lines.  Sorting: trusted before verified,
    then by CATEGORY_PRIORITY order.

    Returns empty string when no qualifying learnings exist.
    """
    all_text = read_security_learnings(instance_dir, project_name)
    if not all_text.strip():
        return ""

    qualifying = []
    for line in all_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "[verified]" in stripped or "[trusted]" in stripped:
            qualifying.append(stripped)

    if not qualifying:
        return ""

    def _sort_key(line: str) -> tuple:
        trust_order = 0 if "[trusted]" in line else 1
        cat_order = len(CATEGORY_PRIORITY)
        for i, cat in enumerate(CATEGORY_PRIORITY):
            if f"[{cat}]" in line:
                cat_order = i
                break
        return (trust_order, cat_order)

    qualifying.sort(key=_sort_key)
    qualifying = qualifying[:MAX_INJECTION_LINES]

    return "## Security Intelligence\n\n" + "\n".join(qualifying) + "\n"
