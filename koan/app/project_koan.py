"""Readers for a target project's optional .koan/ steering tree.

Raw-content readers only — framing (system-prompt templates) stays with the
callers next to their templates. Mirrors prompt_builder._get_koan_md_section's
absent/blank/unreadable handling: absent is the normal case (no log); a
present-but-unreadable file warns and is treated as empty.
"""
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_KOAN_MD_CHARS = 16000
_MAX_KOAN_SKILL_CHARS = 16000


def log_context_load(label: str, content: str) -> None:
    """Announce a steering file koan just loaded into a prompt, for ``make logs``.

    Emits ``Detected <label>, loaded N chars (~ M tokens)`` on **stderr** so it
    lands in ``logs/run.log`` (visible via ``make logs``) without ever
    corrupting the JSON some skill runners write to stdout. The ``logging``
    module has no stdout/stderr handler wired in the run loop, so ``logger.info``
    alone would be invisible there — hence the direct ``print``.

    Best-effort: a broken stream (or a missing ``estimate_tokens``) must never
    break prompt assembly, so every failure is swallowed — logged at debug so it
    stays visible without ever raising.
    """
    try:
        from app.diff_compressor import estimate_tokens
        print(
            f"[context] Detected {label}, loaded {len(content)} chars "
            f"(~ {estimate_tokens(content)} tokens)",
            file=sys.stderr,
            flush=True,
        )
    except Exception as e:
        logger.debug("log_context_load failed for %s: %s", label, e)


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


_MAX_CONVENTION_DOC_CHARS = 16000    # per-source cap (applied before the block cap)
_MAX_CONVENTION_BLOCK_CHARS = 16000  # whole-block cap

# Well-known root convention files, in signal-priority order.
_WELL_KNOWN_CONVENTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")

# OKF bundle: the small, high-signal root pages worth injecting whole.
_OKF_ROOT_DOCS = ("index.md", "SPEC.md", "SCHEMA.md")


def read_repo_convention_docs(
    project_path: str,
    *,
    well_known=_WELL_KNOWN_CONVENTION_FILES,
    okf_docs_dir: str = "docs",
    include_topic_indexes: bool = True,
    auto_detect_okf: bool = True,
    max_source_chars: int = _MAX_CONVENTION_DOC_CHARS,
    max_block_chars: int = _MAX_CONVENTION_BLOCK_CHARS,
) -> str:
    """Concatenate a repo's own convention/knowledge docs, provenance-labelled.

    Sources, in priority order:
      1. Well-known root files (AGENTS.md, CLAUDE.md, CONTRIBUTING.md).
      2. An OKF/docs bundle detected by ``<docs>/index.md``: the curated bundle
         index + SPEC.md + SCHEMA.md, plus (optionally) each topic folder's
         generated ``index.md`` catalog — never the full topic pages, which the
         reviewer can Read on demand.

    Each fragment is prefixed with a ``# <relpath>`` provenance marker (matching
    :func:`read_skill_instructions`). De-dupes by resolved realpath so an
    ``AGENTS.md -> CLAUDE.md`` symlink is read once. Per-source content is capped
    at ``max_source_chars``; the whole block at ``max_block_chars``. Returns ""
    when ``project_path`` is empty or nothing is found.
    """
    if not project_path:
        return ""
    root = Path(project_path)
    parts: list = []
    seen: set = set()

    def _add(rel: str, path: Path) -> None:
        if not path.is_file():
            return
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            return
        seen.add(key)
        body = _read_or_empty(path)
        if body:
            parts.append(f"# {rel}\n\n{_cap(body, max_source_chars, rel)}")

    for name in well_known:
        _add(str(name), root / str(name))

    if auto_detect_okf and okf_docs_dir:
        docs = root / okf_docs_dir
        if (docs / "index.md").is_file():
            for name in _OKF_ROOT_DOCS:
                _add(f"{okf_docs_dir}/{name}", docs / name)
            if include_topic_indexes:
                try:
                    topic_indexes = sorted(
                        docs.glob("*/index.md"), key=lambda p: p.as_posix())
                except OSError:
                    topic_indexes = []
                for idx in topic_indexes:
                    rel = f"{okf_docs_dir}/{idx.parent.name}/index.md"
                    _add(rel, idx)

    if not parts:
        return ""
    return _cap("\n\n".join(parts), max_block_chars, "repo convention docs")
