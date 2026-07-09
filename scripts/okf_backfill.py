#!/usr/bin/env python3
"""
okf_backfill.py — Mechanical, idempotent generators for koan's docs/ and specs/
OKF bundles. This is the "fixer" half of the detector/fixer split established by
scripts/wiki_check.py (detector) — it never decides whether something is wrong, it
only regenerates derived content from frontmatter that's already there.

Subcommands:

  descriptions --from-wiki-index
      Mines the one-line summary already written for each page in wiki/index.md and
      writes it as a `description:` frontmatter field on the source file — but only
      when that field is absent. Never overwrites an existing description (organic
      hand-edits always win). Safe to re-run.

  indexes [--bundle docs|specs|all]
      Regenerates every bundle-root index.md (docs/index.md, specs/index.md) and
      per-topic-folder index.md (docs/architecture/index.md, ..., specs/components/index.md,
      specs/skills/index.md) from each page's own `title`/`description` frontmatter.
      Topic folders are discovered dynamically (any immediate subdirectory containing
      at least one concept page) — a future docs/reference/ needs no script change.
      Deterministic, alphabetically ordered, always safe to re-run — never hand-edit
      a generated index.md, edit the source page's frontmatter instead and re-run this.

Usage:
    python3 scripts/okf_backfill.py descriptions --from-wiki-index
    python3 scripts/okf_backfill.py indexes [--bundle docs|specs|all]
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NNN_SLUG_RE = re.compile(r"^specs/\d{3}-")
SKILL_SPEC_TEMPLATE = "specs/skills/SKILL_SPEC_TEMPLATE.md"
WIKI_INDEX_ENTRY_RE = re.compile(r"^-\s*\[`([^`]+)`\]\(([^)]+)\)\s*—\s*(.+)$")

# One-line blurbs for known topic folders (mirrors the tag-taxonomy prose in
# docs/SCHEMA.md / specs/SCHEMA.md). A folder not listed here still gets indexed,
# just with a generic fallback blurb — this dict is a nicety, not a gate.
TOPIC_BLURBS = {
    "docs/architecture": "Daemon runtime, mission lifecycle, providers, skills system, memory, shared state.",
    "docs/design": "Durable decisions and design notes.",
    "docs/messaging": "Telegram/Slack/Matrix/Discord/GitHub/Jira integration.",
    "docs/operations": "Maintenance, troubleshooting, dashboard, REST API, auto-update, RTK, skill evals.",
    "docs/providers": "CLI/local-model provider setup and behavior.",
    "docs/security": "Security review, threat models, prompt guard.",
    "docs/setup": "Installation and host runtime (Docker, Railway, systemd, launchd, ssh).",
    "docs/users": "User manual, onboarding, quickstart, skill reference.",
    "docs/reference": "Pages compiled by /brain ingest from external material.",
    "specs/components": "Durable design contracts, one per architectural module group.",
    "specs/skills": "Durable design contracts, one per skill.",
}


def is_speckit_ephemeral(path):
    return bool(NNN_SLUG_RE.match(path))


def is_reserved(path):
    return Path(path).name == "index.md"


def is_eligible(path):
    if not path.endswith(".md"):
        return False
    if is_speckit_ephemeral(path):
        return False
    if is_reserved(path):
        return False
    if path == SKILL_SPEC_TEMPLATE:
        return False
    return path.startswith("docs/") or path.startswith("specs/")


def parse_frontmatter(text):
    if not text.startswith("---\n"):
        return None, None, None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, None, None
    block = text[4:end]
    body = text[end + 5:]
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, block, body


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


NNN_PREFIX_RE = re.compile(r"^\d{3}-")


def wiki_rel_to_real_path(rel):
    """Reverse of wiki_check.py's check_index_entry path mapping. Returns None for
    speckit's ephemeral specs/<NNN-slug>/ entries — those are deliberately never
    frontmattered (see specs/SCHEMA.md), so they're not a description-backfill target."""
    if NNN_PREFIX_RE.match(rel):
        return None
    if rel == "specs/README.md":
        return "specs/README.md"
    if rel.startswith("components/") or rel.startswith("skills/"):
        return f"specs/{rel}"
    return f"docs/{rel}"


def cmd_descriptions(args):
    index_path = REPO_ROOT / "wiki" / "index.md"
    text = index_path.read_text(errors="replace")

    written, skipped, missing = [], [], []
    for line in text.splitlines():
        m = WIKI_INDEX_ENTRY_RE.match(line.strip())
        if not m:
            continue
        rel, _link, desc = m.groups()
        real_path = wiki_rel_to_real_path(rel)
        if real_path is None:
            continue  # speckit ephemeral entry — never frontmattered, not a gap
        full = REPO_ROOT / real_path
        if not full.exists():
            missing.append(real_path)
            continue
        page_text = full.read_text(errors="replace")
        fields, block, body = parse_frontmatter(page_text)
        if fields is None:
            missing.append(f"{real_path}: no frontmatter block, skipped")
            continue
        if "description" in fields:
            skipped.append(real_path)
            continue
        desc_escaped = desc.replace('"', '\\"')
        new_line = f'description: "{desc_escaped}"'
        lines = block.splitlines()
        insert_at = len(lines)
        for i, bl in enumerate(lines):
            if bl.strip().startswith("title:"):
                insert_at = i + 1
                break
        lines.insert(insert_at, new_line)
        new_block = "\n".join(lines)
        full.write_text(f"---\n{new_block}\n---\n{body}")
        written.append(real_path)

    print(f"Wrote description: to {len(written)} file(s), skipped {len(skipped)} (already had one).")
    if missing:
        print(f"{len(missing)} wiki/index.md entr(y/ies) could not be mapped to a real file:")
        for m in missing:
            print(f"  - {m}")
    return 0


def discover_topic_folders(bundle):
    """Immediate subdirectories of `bundle` containing >=1 concept page."""
    base = REPO_ROOT / bundle
    folders = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        rel = child.relative_to(REPO_ROOT).as_posix()
        if is_speckit_ephemeral(rel + "/"):
            continue
        has_concept = any(
            is_eligible(p.relative_to(REPO_ROOT).as_posix())
            for p in child.glob("*.md")
        )
        if has_concept:
            folders.append(rel)
    return folders


def root_pages(bundle):
    base = REPO_ROOT / bundle
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in base.glob("*.md")
        if is_eligible(p.relative_to(REPO_ROOT).as_posix())
    )


def page_title_desc(path):
    full = REPO_ROOT / path
    fields, _, _ = parse_frontmatter(full.read_text(errors="replace"))
    fields = fields or {}
    title = unquote(fields.get("title", Path(path).stem))
    desc = unquote(fields.get("description", ""))
    return title, desc


def write_folder_index(folder_rel):
    """Generate <folder>/index.md — no frontmatter, OKF §5 bullet listing."""
    folder = REPO_ROOT / folder_rel
    pages = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in folder.glob("*.md")
        if is_eligible(p.relative_to(REPO_ROOT).as_posix())
    )
    heading = Path(folder_rel).name.replace("-", " ").replace("_", " ").title()
    lines = [f"# {heading}", ""]
    for page in pages:
        title, desc = page_title_desc(page)
        filename = Path(page).name
        suffix = f" - {desc}" if desc else ""
        lines.append(f"* [{title}]({filename}){suffix}")
    content = "\n".join(lines) + "\n"
    (folder / "index.md").write_text(content)
    return folder_rel + "/index.md"


def write_bundle_root_index(bundle):
    """Generate <bundle>/index.md — okf_version frontmatter, root pages + topic links."""
    heading = "Docs" if bundle == "docs" else "Specs"
    lines = ['---', 'okf_version: "0.1"', '---', "", f"# {heading}", ""]

    for page in root_pages(bundle):
        if Path(page).name == "index.md":
            continue
        title, desc = page_title_desc(page)
        filename = Path(page).name
        suffix = f" - {desc}" if desc else ""
        lines.append(f"* [{title}]({filename}){suffix}")

    lines += ["", "## Topics", ""]
    for folder_rel in discover_topic_folders(bundle):
        name = Path(folder_rel).name
        blurb = TOPIC_BLURBS.get(folder_rel, f"{name.title()} pages.")
        lines.append(f"* [{name}/]({name}/) - {blurb}")

    content = "\n".join(lines) + "\n"
    (REPO_ROOT / bundle / "index.md").write_text(content)
    return f"{bundle}/index.md"


def cmd_indexes(args):
    bundles = ["docs", "specs"] if args.bundle == "all" else [args.bundle]
    written = []
    for bundle in bundles:
        written.append(write_bundle_root_index(bundle))
        written.extend(write_folder_index(f) for f in discover_topic_folders(bundle))
    print(f"Regenerated {len(written)} index.md file(s):")
    for w in written:
        print(f"  - {w}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_desc = sub.add_parser("descriptions", help="Backfill missing description: fields from wiki/index.md")
    p_desc.add_argument("--from-wiki-index", action="store_true", required=True, help="Required flag naming the (only) source used")

    p_idx = sub.add_parser("indexes", help="Regenerate bundle-root and per-folder index.md files")
    p_idx.add_argument("--bundle", choices=["docs", "specs", "all"], default="all")

    args = parser.parse_args(argv)
    if args.command == "descriptions":
        return cmd_descriptions(args)
    if args.command == "indexes":
        return cmd_indexes(args)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
