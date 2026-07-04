"""
Koan -- Documentation extraction runner.

Performs a read-only analysis of a project codebase and produces structured
documentation files under the project's docs/ directory.

Pipeline:
1. Build a documentation extraction prompt with project context
2. Run Claude Code CLI (read-only tools) to analyze the codebase
3. Parse structured ---DOC--- blocks from output
4. Write/merge documentation files to docs/

CLI:
    python3 -m skills.core.doc.doc_runner \
        --project-path <path> --project-name <name> --instance-dir <dir>
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from app.prompts import load_prompt_or_skill


# All supported categories
ALL_CATEGORIES = [
    "architecture",
    "code-style",
    "test-style",
    "anti-patterns",
    "modules",
]

# Regex for parsing ---DOC--- blocks
_DOC_BLOCK_RE = re.compile(
    r"---DOC---\s*\n"
    r"category:\s*(?P<category>[^\n]+)\n"
    r"title:\s*(?P<title>[^\n]+)\n"
    r"---\s*\n"
    r"(?P<content>.*?)"
    r"---END DOC---",
    re.DOTALL,
)

# H2 heading pattern for section-level merging
_H2_RE = re.compile(r"^## .+$", re.MULTILINE)


class DocBlock:
    """A parsed documentation block from Claude output."""

    def __init__(self, category: str, title: str, content: str):
        self.category = category.strip()
        self.title = title.strip()
        self.content = content.strip()

    @property
    def filename(self) -> str:
        """Derive the output filename from the category."""
        return f"{self.category}.md"


def parse_doc_blocks(raw_output: str) -> List[DocBlock]:
    """Parse ---DOC--- blocks from Claude's raw output.

    Returns a list of DocBlock instances, one per block found.
    """
    return [
        DocBlock(
            category=match.group("category"),
            title=match.group("title"),
            content=match.group("content"),
        )
        for match in _DOC_BLOCK_RE.finditer(raw_output)
    ]


def _split_sections(text: str) -> dict:
    """Split markdown text into sections keyed by H2 heading.

    Returns a dict mapping heading text (e.g. "## Overview") to the
    content below it (up to the next H2 or end of file).
    A special key "__preamble__" holds content before the first H2.
    """
    sections = {}
    positions = [(m.start(), m.group()) for m in _H2_RE.finditer(text)]

    if not positions:
        return {"__preamble__": text}

    # Content before first heading
    preamble = text[:positions[0][0]].strip()
    if preamble:
        sections["__preamble__"] = preamble

    for i, (start, heading) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        sections[heading] = text[start:end].strip()

    return sections


def merge_doc(existing: str, new_content: str) -> str:
    """Merge new documentation into existing content using H2 sections as keys.

    New sections replace existing sections with the same heading.
    Existing sections not present in new content are preserved.
    New sections not present in existing content are appended.
    """
    existing_sections = _split_sections(existing)
    new_sections = _split_sections(new_content)

    # Start with existing preamble (prefer new if provided)
    result_parts = []
    preamble = new_sections.pop("__preamble__", None)
    if preamble is None:
        preamble = existing_sections.pop("__preamble__", None)
    else:
        existing_sections.pop("__preamble__", None)
    if preamble:
        result_parts.append(preamble)

    # Update existing sections, preserving order
    seen = set()
    for heading, content in existing_sections.items():
        if heading in new_sections:
            result_parts.append(new_sections[heading])
        else:
            result_parts.append(content)
        seen.add(heading)

    # Append new sections not in existing
    for heading, content in new_sections.items():
        if heading not in seen:
            result_parts.append(content)

    return "\n\n".join(result_parts) + "\n"


def write_doc_file(
    docs_dir: Path, block: DocBlock, mode: str,
) -> Optional[Path]:
    """Write a documentation block to a file based on the mode.

    Args:
        docs_dir: Target directory for documentation files.
        block: Parsed documentation block.
        mode: One of 'create', 'update', 'replace'.

    Returns:
        Path to the written file, or None if skipped.
    """
    filepath = docs_dir / block.filename
    content = f"# {block.title}\n\n{block.content}\n"

    if mode == "create":
        if filepath.exists():
            return None
        filepath.write_text(content)
        return filepath

    if mode == "replace":
        filepath.write_text(content)
        return filepath

    # mode == "update"
    if filepath.exists():
        existing = filepath.read_text()
        merged = merge_doc(existing, content)
        filepath.write_text(merged)
    else:
        filepath.write_text(content)
    return filepath


# ---------------------------------------------------------------------------
# Wiki bookkeeping — only activates when the TARGET project has adopted the
# LLM Wiki pattern (praneybehl/llm-wiki-plugin) itself, i.e. it has its own
# wiki/SCHEMA.md. This keeps /doc generic across arbitrary mission projects;
# it's a no-op for projects without a wiki/.
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _wiki_root(project_dir: Path) -> Optional[Path]:
    """Return the project's wiki/ dir if it has adopted the LLM Wiki pattern, else None."""
    wiki_dir = project_dir / "wiki"
    if (wiki_dir / "SCHEMA.md").exists():
        return wiki_dir
    return None


def _ensure_frontmatter(filepath: Path, category: str, title: str, today: str) -> None:
    """Prepend or refresh a wiki frontmatter block on a doc file written by this skill.

    New files get `created`/`updated` set to today. Existing frontmatter has only
    `updated` refreshed; `created` and any existing `tags` are preserved.
    """
    text = filepath.read_text()
    match = _FRONTMATTER_RE.match(text)
    if match:
        block = match.group(0)
        created_match = re.search(r"^created:\s*(.+)$", block, re.MULTILINE)
        created = created_match.group(1).strip() if created_match else today
        tags_match = re.search(r"^tags:\s*(.+)$", block, re.MULTILINE)
        tags = tags_match.group(1).strip() if tags_match else f"[{category}]"
        body = text[match.end():]
    else:
        created = today
        tags = f"[{category}]"
        body = text

    frontmatter = (
        "---\n"
        "type: doc\n"
        f'title: "{title}"\n'
        f"tags: {tags}\n"
        f"created: {created}\n"
        f"updated: {today}\n"
        "---\n\n"
    )
    filepath.write_text(frontmatter + body.lstrip("\n"))


def _update_wiki_index(wiki_dir: Path, docs_dir: Path, filepath: Path, summary: str) -> None:
    """Add or refresh this doc's one-line entry in wiki/index.md."""
    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        return
    rel_from_docs_root = filepath.relative_to(docs_dir)
    rel_from_wiki = filepath.relative_to(docs_dir.parent)  # e.g. docs/architecture.md
    link = f"- [`{rel_from_docs_root}`]({rel_from_wiki}) — {summary}"
    text = index_path.read_text()
    heading = "## Docs (via /doc)"
    entry_re = re.compile(rf"^- \[`{re.escape(str(rel_from_docs_root))}`\].*$", re.MULTILINE)
    if entry_re.search(text):
        text = entry_re.sub(link, text)
    elif heading in text:
        text = text.replace(heading, f"{heading}\n{link}", 1)
    else:
        text = text.rstrip("\n") + f"\n\n{heading}\n{link}\n"
    index_path.write_text(text)


def _append_wiki_log(wiki_dir: Path, today: str, summary: str) -> None:
    """Append one line to wiki/log.md, if it exists."""
    log_path = wiki_dir / "log.md"
    if not log_path.exists():
        return
    with log_path.open("a") as f:
        f.write(f"\n## [{today}] ingest | /doc: {summary}\n")


def _sync_wiki(project_dir: Path, docs_dir: Path, blocks: List["DocBlock"], written: List[str]) -> None:
    """Best-effort wiki bookkeeping for docs this run actually wrote.

    No-op when the target project has no wiki/SCHEMA.md.
    """
    wiki_dir = _wiki_root(project_dir)
    if wiki_dir is None or not written:
        return

    import datetime
    today = datetime.date.today().isoformat()

    for block in blocks:
        if block.category not in written:
            continue
        filepath = docs_dir / block.filename
        if not filepath.exists():
            continue
        _ensure_frontmatter(filepath, block.category, block.title, today)
        first_line = block.content.strip().splitlines()[0] if block.content.strip() else block.title
        summary = first_line.lstrip("#").strip()[:160]
        _update_wiki_index(wiki_dir, docs_dir, filepath, summary)

    _append_wiki_log(wiki_dir, today, f"{len(written)} doc file(s) updated via /doc ({', '.join(written)})")


def _describe_existing_docs(docs_dir: Path, categories: List[str]) -> str:
    """Describe which docs already exist for the requested categories."""
    if not docs_dir.exists():
        return "No docs/ directory exists yet."

    existing = []
    for cat in categories:
        filepath = docs_dir / f"{cat}.md"
        if filepath.exists():
            size = len(filepath.read_text().splitlines())
            existing.append(f"- {cat}.md ({size} lines) — already exists")
        else:
            existing.append(f"- {cat}.md — does not exist")

    return "\n".join(existing) if existing else "No matching doc files found."


def build_doc_prompt(
    project_name: str,
    categories: List[str],
    mode: str,
    existing_docs: str,
    skill_dir: Optional[Path] = None,
) -> str:
    """Build the documentation extraction prompt."""
    categories_str = ", ".join(categories)
    return load_prompt_or_skill(
        skill_dir, "doc",
        PROJECT_NAME=project_name,
        CATEGORIES=categories_str,
        MODE=mode,
        EXISTING_DOCS=existing_docs,
    )


def _run_claude_scan(prompt: str, project_path: str) -> str:
    """Run Claude CLI with read-only tools and return the output text."""
    from app.cli_provider import run_command_streaming
    from app.config import get_analysis_max_turns, get_skill_timeout

    return run_command_streaming(
        prompt, project_path,
        allowed_tools=["Read", "Glob", "Grep"],
        max_turns=get_analysis_max_turns(),
        timeout=get_skill_timeout(),
    )


def run_doc(
    project_path: str,
    project_name: str,
    instance_dir: str,
    categories: Optional[List[str]] = None,
    mode: str = "create",
    notify_fn=None,
    skill_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Execute documentation extraction on a project.

    Args:
        project_path: Local path to the project.
        project_name: Project name for labeling.
        instance_dir: Path to instance directory.
        categories: List of categories to extract (default: all).
        mode: Write mode — 'create', 'update', or 'replace'.
        notify_fn: Optional callback for progress notifications.
        skill_dir: Optional path to skill directory for prompts.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    if categories is None:
        categories = list(ALL_CATEGORIES)

    # Validate categories
    invalid = [c for c in categories if c not in ALL_CATEGORIES]
    if invalid:
        return False, f"Unknown categories: {', '.join(invalid)}. Valid: {', '.join(ALL_CATEGORIES)}"

    project_dir = Path(project_path)
    docs_dir = project_dir / "docs"

    # Step 1: Describe existing docs
    existing_docs = _describe_existing_docs(docs_dir, categories)

    # Step 2: Build prompt
    cat_text = ", ".join(categories)
    notify_fn(f"\U0001f4da Extracting documentation for {project_name} ({cat_text})...")
    prompt = build_doc_prompt(
        project_name, categories, mode, existing_docs, skill_dir=skill_dir,
    )

    # Step 3: Run Claude scan (read-only)
    try:
        raw_output = _run_claude_scan(prompt, project_path)
    except RuntimeError as e:
        return False, f"Documentation extraction failed: {e}"

    if not raw_output:
        return False, f"Documentation extraction produced no output for {project_name}."

    # Step 4: Parse doc blocks
    blocks = parse_doc_blocks(raw_output)
    if not blocks:
        return False, (
            f"No ---DOC--- blocks found in output for {project_name}. "
            f"Claude may have produced unstructured output."
        )

    # Step 5: Write files
    docs_dir.mkdir(parents=True, exist_ok=True)
    written = []
    skipped = []

    for block in blocks:
        path = write_doc_file(docs_dir, block, mode)
        if path:
            written.append(block.category)
        else:
            skipped.append(block.category)

    # Step 6: Wiki bookkeeping — no-op unless this project has its own wiki/SCHEMA.md
    _sync_wiki(project_dir, docs_dir, blocks, written)

    # Build summary
    written_text = f"{len(written)} files written ({', '.join(written)})" if written else "no files written"
    skipped_text = f", {len(skipped)} skipped ({', '.join(skipped)})" if skipped else ""
    summary = f"Documentation extracted: {written_text}{skipped_text}"
    notify_fn(f"\u2705 {summary}")

    return True, summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for doc_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract structured documentation from a project codebase."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Project name for labeling",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to instance directory",
    )
    parser.add_argument(
        "--categories",
        help="Comma-separated list of categories to extract (default: all)",
    )
    parser.add_argument(
        "--mode", default="create",
        choices=["create", "update", "replace"],
        help="Write mode: create (skip existing), update (merge), replace (overwrite)",
    )

    cli_args = parser.parse_args(argv)
    skill_dir = Path(__file__).resolve().parent

    categories = None
    if cli_args.categories:
        categories = [c.strip() for c in cli_args.categories.split(",")]

    success, summary = run_doc(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        categories=categories,
        mode=cli_args.mode,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
