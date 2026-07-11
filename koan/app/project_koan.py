"""Readers for a target project's optional .koan/ steering tree.

Raw-content readers only — framing (system-prompt templates) stays with the
callers next to their templates. Mirrors prompt_builder._get_koan_md_section's
absent/blank/unreadable handling: absent is the normal case (no log); a
present-but-unreadable file warns and is treated as empty.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_KOAN_MD_CHARS = 16000
_MAX_KOAN_SKILL_CHARS = 16000


def _read_or_empty(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except FileNotFoundError:
        return ""
    except OSError as e:
        logger.warning("present but unreadable at %s: %s", path, e)
        return ""


def _cap(content: str, limit: int, label: str) -> str:
    if len(content) > limit:
        return content[:limit] + f"\n\n[{label} truncated — exceeded {limit} chars]"
    return content


def read_general_koan_md(project_path: str) -> str:
    """Root KOAN.md + .koan/KOAN.md, stripped, concatenated (root first).

    Returns "" when project_path is empty or both sources are absent/blank.
    The combined length is capped at _MAX_KOAN_MD_CHARS.
    """
    if not project_path:
        return ""
    root = _read_or_empty(Path(project_path) / "KOAN.md")
    dot = _read_or_empty(Path(project_path) / ".koan" / "KOAN.md")
    parts = []
    if root:
        parts.append(root)
    if dot:
        parts.append(f"# .koan/KOAN.md\n\n{dot}")
    if not parts:
        return ""
    return _cap("\n\n".join(parts), _MAX_KOAN_MD_CHARS, "KOAN.md")


def read_skill_instructions(project_path: str, skill_name: str) -> str:
    """Concatenate <project>/.koan/skills/<skill_name>/*.md, sorted by filename.

    Each fragment is prefixed with a `# <filename>` provenance marker. Ignores
    non-.md files and subdirectories. Returns "" when absent/empty/all-blank.
    Capped at _MAX_KOAN_SKILL_CHARS.
    """
    if not project_path or not skill_name:
        return ""
    skill_dir = Path(project_path) / ".koan" / "skills" / skill_name
    if not skill_dir.is_dir():
        return ""
    parts = []
    for md in sorted(skill_dir.glob("*.md"), key=lambda p: p.name):
        if not md.is_file():
            continue
        body = _read_or_empty(md)
        if body:
            parts.append(f"# {md.name}\n\n{body}")
    if not parts:
        return ""
    return _cap("\n\n".join(parts), _MAX_KOAN_SKILL_CHARS, ".koan skill instructions")
