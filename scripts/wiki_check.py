#!/usr/bin/env python3
"""
wiki_check.py — Deterministic wiki-hygiene checker for koan's LLM Wiki.

Walks the files changed relative to a base ref and reports (or, with --json,
emits machine-readable) gaps in wiki bookkeeping:

  - missing/malformed frontmatter on docs/**, specs/components/*.md,
    specs/skills/*.md (excluding SKILL_SPEC_TEMPLATE.md), specs/README.md
  - a wiki-eligible file with no matching wiki/index.md entry
  - a specs/<NNN-slug>/tasks.md whose checkbox ratio no longer matches the
    status wiki/index.md currently records for that feature

This is the deterministic half of the wiki-sync CI job (see
.github/workflows/wiki-sync.yml): it decides WHETHER there's a gap. An LLM
step (via app.provider.run_command) decides HOW to fix it — this script
never edits anything itself.

Deliberately excluded from frontmatter/index checks: specs/<NNN-slug>/** —
see wiki/SCHEMA.md ("Why speckit feature folders get no frontmatter").

Usage:
    python3 scripts/wiki_check.py [--base-ref <ref>] [--json]

Examples:
    python3 scripts/wiki_check.py --base-ref origin/main
    python3 scripts/wiki_check.py --base-ref origin/main --json
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FRONTMATTER_ELIGIBLE_PREFIXES = ("docs/",)
COMPONENT_SPEC_DIR = "specs/components/"
SKILL_SPEC_DIR = "specs/skills/"
SKILL_SPEC_TEMPLATE = "specs/skills/SKILL_SPEC_TEMPLATE.md"
SPECS_README = "specs/README.md"
NNN_TASKS_RE = re.compile(r"^specs/(\d{3}-[^/]+)/tasks\.md$")

REQUIRED_FM_FIELDS = ("type", "title", "tags", "created", "updated")


def sh(*args, cwd=REPO_ROOT):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def changed_files(base_ref):
    out = sh("git", "diff", "--name-only", f"{base_ref}...HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_frontmatter_eligible(path):
    if path == SKILL_SPEC_TEMPLATE:
        return False
    if path.startswith(FRONTMATTER_ELIGIBLE_PREFIXES) and path.endswith(".md"):
        return True
    if path.startswith(COMPONENT_SPEC_DIR) and path.endswith(".md"):
        return True
    if path.startswith(SKILL_SPEC_DIR) and path.endswith(".md"):
        return True
    if path == SPECS_README:
        return True
    return False


def parse_frontmatter(text):
    """Return the parsed field dict if `text` starts with a --- block, else None."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    block = text[4:end]
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def check_frontmatter(path):
    """Return a list of finding strings for one file, empty if clean."""
    full = REPO_ROOT / path
    if not full.exists():
        return []  # deleted in this diff — nothing to check
    text = full.read_text(errors="replace")
    fields = parse_frontmatter(text)
    if fields is None:
        return [f"{path}: missing frontmatter block"]
    missing = [f for f in REQUIRED_FM_FIELDS if f not in fields]
    if missing:
        return [f"{path}: frontmatter missing field(s): {', '.join(missing)}"]
    return []


def check_index_entry(path, index_text):
    """A wiki-eligible file should have a `[`<rel>`]` link somewhere in index.md.

    wiki/index.md's display paths mirror the wiki/ symlink names, not the real
    specs/ path: `specs/components/core.md` -> `components/core.md`,
    `specs/skills/ask.md` -> `skills/ask.md` (matching wiki/specs-components,
    wiki/specs-skills). Only the `specs/` prefix is dropped, not the
    components/skills segment.
    """
    if path == SPECS_README:
        rel = "specs/README.md"  # index.md keeps the full path for this one, unlike docs/README.md
    elif path.startswith("docs/"):
        rel = path[len("docs/"):]
    elif path.startswith("specs/"):
        rel = path[len("specs/"):]  # "components/core.md" / "skills/ask.md"
    else:
        return []
    needle = f"[`{rel}`]"
    if needle not in index_text:
        return [f"{path}: no wiki/index.md entry found (expected `{needle}`)"]
    return []


def task_ratio(tasks_path):
    text = (REPO_ROOT / tasks_path).read_text(errors="replace")
    total = len(re.findall(r"^\s*-\s*\[[ xX]\]", text, re.MULTILINE))
    checked = len(re.findall(r"^\s*-\s*\[[xX]\]", text, re.MULTILINE))
    return checked, total


def status_for_ratio(checked, total):
    if total == 0:
        return "draft"
    pct = checked / total
    if pct >= 0.95:
        return "shipped"
    if pct > 0:
        return "in-progress"
    return "draft"


def check_feature_status(path, index_text):
    match = NNN_TASKS_RE.match(path)
    if not match:
        return []
    slug = match.group(1)
    if not (REPO_ROOT / path).exists():
        return []
    checked, total = task_ratio(path)
    expected_status = status_for_ratio(checked, total)
    # Look for "<slug>/spec.md] — **<status>**" style entries in index.md
    entry_re = re.compile(rf"{re.escape(slug)}/spec\.md.*?\*\*(draft|in-progress|shipped)\*\*")
    m = entry_re.search(index_text)
    if not m:
        return [f"{path}: no status entry found in wiki/index.md for {slug} (expected status: {expected_status}, {checked}/{total} tasks)"]
    recorded_status = m.group(1)
    if recorded_status != expected_status:
        return [
            f"{path}: wiki/index.md records status '{recorded_status}' for {slug}, "
            f"but tasks.md is now {checked}/{total} ({expected_status})"
        ]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="origin/main", help="Base ref to diff against (default: origin/main)")
    parser.add_argument("--json", action="store_true", help="Emit findings as a JSON array instead of text")
    args = parser.parse_args(argv)

    try:
        files = changed_files(args.base_ref)
    except subprocess.CalledProcessError as e:
        print(f"error: git diff failed: {e.stderr}", file=sys.stderr)
        return 2

    index_path = REPO_ROOT / "wiki" / "index.md"
    index_text = index_path.read_text(errors="replace") if index_path.exists() else ""

    findings = []
    for path in files:
        if is_frontmatter_eligible(path):
            findings.extend(check_frontmatter(path))
            findings.extend(check_index_entry(path, index_text))
        findings.extend(check_feature_status(path, index_text))

    if args.json:
        import json
        print(json.dumps(findings, indent=2))
    else:
        if not findings:
            print("No wiki hygiene gaps found.")
        else:
            print(f"{len(findings)} wiki hygiene gap(s) found:")
            for f in findings:
                print(f"  - {f}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
