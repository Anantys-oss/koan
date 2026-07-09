#!/usr/bin/env python3
"""
wiki_check.py — Deterministic wiki-hygiene / OKF-conformance checker for koan's
docs/ and specs/ bundles.

Default mode (no --full, no --strict) is UNCHANGED from before OKF adoption: it walks
files changed relative to a base ref and reports gaps in wiki bookkeeping —

  - missing/malformed frontmatter on docs/**, specs/components/*.md,
    specs/skills/*.md (excluding SKILL_SPEC_TEMPLATE.md), specs/README.md
  - a wiki-eligible file with no matching wiki/index.md entry
  - a specs/<NNN-slug>/tasks.md whose checkbox ratio no longer matches the
    status wiki/index.md currently records for that feature

This is the deterministic half of the wiki-sync CI job (see
.github/workflows/wiki-sync.yml): it decides WHETHER there's a gap. An LLM
step (via app.provider.run_command, see wiki_sync_ci.py) decides HOW to fix
it — this script never edits anything itself. Existing CI usage
(`wiki_check.py --base-ref origin/main`) is byte-for-byte behavior-compatible
with before this file grew OKF support.

Two additive flags extend the check for OKF v0.1 conformance (see docs/SPEC.md):

  --full    scan every eligible file in each bundle instead of only the files
            changed vs --base-ref. Orthogonal to --strict.
  --strict  in addition to the checks above, run the fuller OKF-conformance
            surface: missing/empty `type` (OKF §9), non-root index.md carrying
            frontmatter (violates OKF §5), orphan pages, broken internal links,
            and per-directory index.md coverage. Findings are split into HARD
            (OKF §9 conformance — the only normative rules) and SOFT
            (everything else, including koan's own stricter house-style
            fields). The exit code in --strict mode is 1 iff a HARD finding
            exists; SOFT findings are still printed/emitted but don't fail the
            check on their own.

Deliberately excluded from all checks: specs/<NNN-slug>/** (except its
tasks.md checkbox-ratio-vs-status check) — see specs/SCHEMA.md ("Why speckit
feature folders are excluded").

Usage:
    python3 scripts/wiki_check.py [--base-ref <ref>] [--full] [--strict] [--json]

Examples:
    python3 scripts/wiki_check.py --base-ref origin/main
    python3 scripts/wiki_check.py --full --strict --json
"""

import argparse
import posixpath
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
NNN_SLUG_RE = re.compile(r"^specs/\d{3}-")
NNN_TASKS_RE = re.compile(r"^specs/(\d{3}-[^/]+)/tasks\.md$")

REQUIRED_FM_FIELDS = ("type", "title", "tags", "created", "updated")
RECOMMENDED_FM_FIELDS = ("description",)

BUNDLE_ROOT_INDEX = {"docs": "docs/index.md", "specs": "specs/index.md"}

INTERNAL_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def sh(*args, cwd=REPO_ROOT):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def changed_files(base_ref):
    out = sh("git", "diff", "--name-only", f"{base_ref}...HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def bundle_of(path):
    if path.startswith("docs/"):
        return "docs"
    if path.startswith("specs/") and not NNN_SLUG_RE.match(path):
        return "specs"
    return None


def is_speckit_ephemeral(path):
    return bool(NNN_SLUG_RE.match(path))


def is_reserved_index(path):
    return Path(path).name == "index.md"


def is_frontmatter_eligible(path):
    """Legacy eligibility test, extended (additive) to cover any root-level specs/*.md
    file — not just specs/README.md — so specs/SCHEMA.md (and any future sibling) gets
    checked too, instead of silently falling through."""
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
    if path.startswith("specs/") and path.endswith(".md") and "/" not in path[len("specs/"):]:
        return True  # any direct child of specs/ root, e.g. specs/SCHEMA.md
    return False


def is_concept_page(path):
    """OKF concept-page eligibility: same as legacy, minus the reserved index.md."""
    if not path.endswith(".md"):
        return False
    if is_speckit_ephemeral(path):
        return False
    if is_reserved_index(path):
        return False
    return is_frontmatter_eligible(path)


def all_bundle_files():
    """Every real, on-disk .md file under docs/ and specs/ (any type), for --full mode
    and for building the whole-bundle link graph needed by orphan/broken-link checks."""
    files = []
    for base in ("docs", "specs"):
        for p in (REPO_ROOT / base).rglob("*.md"):
            rel = p.relative_to(REPO_ROOT).as_posix()
            if is_speckit_ephemeral(rel):
                continue
            files.append(rel)
    return sorted(files)


def parse_frontmatter(text):
    """Return (fields_dict, block_text) if `text` starts with a --- block, else (None, None)."""
    if not text.startswith("---\n"):
        return None, None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None, None
    block = text[4:end]
    fields = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, block


def check_frontmatter(path):
    """Legacy behavior: one finding string per missing-block-or-field file, empty if clean."""
    full = REPO_ROOT / path
    if not full.exists():
        return []  # deleted in this diff — nothing to check
    text = full.read_text(errors="replace")
    fields, _ = parse_frontmatter(text)
    if fields is None:
        return [f"{path}: missing frontmatter block"]
    missing = [f for f in REQUIRED_FM_FIELDS if f not in fields]
    if missing:
        return [f"{path}: frontmatter missing field(s): {', '.join(missing)}"]
    return []


def check_frontmatter_strict(path):
    """Split frontmatter findings into (hard, soft) for --strict mode.

    HARD = OKF §9 conformance: no parseable frontmatter block, or missing/empty `type`.
    SOFT = everything else: missing title/tags/created/updated/description (koan's own
    house style, stricter than bare OKF, which only requires `type`).
    """
    full = REPO_ROOT / path
    if not full.exists():
        return [], []
    text = full.read_text(errors="replace")
    fields, _ = parse_frontmatter(text)
    if fields is None:
        return [f"{path}: missing or malformed frontmatter block (OKF §9)"], []
    hard = []
    if not fields.get("type", "").strip():
        hard.append(f"{path}: missing or empty `type` field (OKF §9)")
    soft = []
    missing_required = [f for f in REQUIRED_FM_FIELDS if f != "type" and f not in fields]
    if missing_required:
        soft.append(f"{path}: frontmatter missing required field(s): {', '.join(missing_required)}")
    missing_recommended = [f for f in RECOMMENDED_FM_FIELDS if f not in fields]
    if missing_recommended:
        soft.append(f"{path}: frontmatter missing recommended field(s): {', '.join(missing_recommended)}")
    return hard, soft


def check_index_md_structure(path):
    """HARD/SOFT findings for a file literally named index.md (OKF §5/§9)."""
    full = REPO_ROOT / path
    if not full.exists():
        return [], []
    text = full.read_text(errors="replace")
    fields, _ = parse_frontmatter(text)
    is_bundle_root = path in BUNDLE_ROOT_INDEX.values()
    hard, soft = [], []
    if is_bundle_root:
        if fields is None:
            soft.append(f"{path}: bundle-root index.md has no `okf_version` frontmatter (recommended, OKF §5/§8)")
        elif not fields.get("okf_version", "").strip():
            soft.append(f"{path}: bundle-root index.md frontmatter missing `okf_version` (recommended, OKF §5/§8)")
    else:
        if fields is not None:
            hard.append(f"{path}: non-root index.md carries a frontmatter block (forbidden by OKF §5)")
    return hard, soft


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


def resolve_link_target(source_path, target):
    """Resolve a markdown link target to a repo-relative path, or None if not a
    same-bundle internal .md link (external URL, mailto:, anchor-only, non-md).

    Per docs/SPEC.md §4, a leading `/` is bundle-root-absolute (resolved relative to
    the source file's own bundle root — docs/ or specs/ — not the repo root).
    """
    target = target.split("#", 1)[0].strip()
    if not target or target.endswith("/"):
        return None
    if "://" in target or target.startswith("mailto:"):
        return None
    if not target.endswith(".md"):
        return None
    bundle = bundle_of(source_path)
    if target.startswith("/"):
        rel = f"{bundle}/{target.lstrip('/')}" if bundle else target.lstrip("/")
    else:
        rel = (Path(source_path).parent / target).as_posix()
    return posixpath.normpath(rel)


def extract_links(text):
    """Strip fenced code blocks and inline code spans first, so example link syntax
    quoted as documentation (e.g. in docs/SPEC.md itself) isn't checked as a real link."""
    stripped = FENCED_CODE_RE.sub("", text)
    stripped = INLINE_CODE_RE.sub("", stripped)
    return INTERNAL_LINK_RE.findall(stripped)


def build_link_graph(files):
    """Return {path: set(resolved internal .md targets)} for every file in `files`."""
    graph = {}
    for path in files:
        full = REPO_ROOT / path
        if not full.exists():
            continue
        text = full.read_text(errors="replace")
        targets = set()
        for raw_target in extract_links(text):
            resolved = resolve_link_target(path, raw_target)
            if resolved:
                targets.add(resolved)
        graph[path] = targets
    return graph


def check_broken_links(path, graph):
    return [
        f"{path}: broken internal link to `{target}`"
        for target in sorted(graph.get(path, ()))
        if not (REPO_ROOT / target).exists()
    ]


def check_orphan(path, graph, bundle_files):
    """A concept page is an orphan if no other page/index.md in its bundle links to it."""
    if path in BUNDLE_ROOT_INDEX.values() or Path(path).name in ("README.md", "SPEC.md", "SCHEMA.md"):
        return []  # entry points are never "orphans" in a meaningful sense
    for other, targets in graph.items():
        if other == path:
            continue
        if path in targets:
            return []
    return [f"{path}: orphan — no page or index.md in its bundle links to it"]


def check_folder_index_coverage(path):
    """The concept page's own containing directory's index.md, if present, should link it."""
    folder = Path(path).parent
    folder_index = (folder / "index.md").as_posix()
    full_index = REPO_ROOT / folder_index
    if not full_index.exists():
        return [f"{folder.as_posix()}/: has concept page(s) but no index.md yet (run scripts/okf_backfill.py indexes)"]
    text = full_index.read_text(errors="replace")
    name = Path(path).name
    if f"]({name})" not in text and f"]({path.split('/')[-1]})" not in text:
        return [f"{path}: not listed in {folder_index} (run scripts/okf_backfill.py indexes)"]
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


def run_legacy_checks(files, index_text):
    """Exactly today's behavior — used whenever --strict is NOT passed."""
    findings = []
    for path in files:
        if is_frontmatter_eligible(path):
            findings.extend(check_frontmatter(path))
            findings.extend(check_index_entry(path, index_text))
        findings.extend(check_feature_status(path, index_text))
    return findings


def run_strict_checks(files, index_text):
    """Fuller OKF-conformance surface — used when --strict is passed. Returns (hard, soft)."""
    hard, soft = [], []
    graph_files = all_bundle_files()  # orphan/broken-link detection needs the whole graph
    graph = build_link_graph(graph_files)

    for path in files:
        if is_reserved_index(path):
            h, s = check_index_md_structure(path)
            hard.extend(h)
            soft.extend(s)
            continue
        if not is_concept_page(path):
            soft.extend(check_feature_status(path, index_text))
            continue
        h, s = check_frontmatter_strict(path)
        hard.extend(h)
        soft.extend(s)
        soft.extend(check_index_entry(path, index_text))
        soft.extend(check_broken_links(path, graph))
        soft.extend(check_orphan(path, graph, graph_files))
        soft.extend(check_folder_index_coverage(path))
        soft.extend(check_feature_status(path, index_text))

    return hard, soft


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="origin/main", help="Base ref to diff against (default: origin/main); ignored with --full")
    parser.add_argument("--full", action="store_true", help="Scan every eligible file in each bundle instead of only files changed vs --base-ref")
    parser.add_argument("--strict", action="store_true", help="Run the fuller OKF-conformance check set (HARD vs SOFT); exit 1 iff a HARD finding exists")
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON instead of text")
    args = parser.parse_args(argv)

    if args.full:
        files = all_bundle_files()
    else:
        try:
            files = changed_files(args.base_ref)
        except subprocess.CalledProcessError as e:
            print(f"error: git diff failed: {e.stderr}", file=sys.stderr)
            return 2

    index_path = REPO_ROOT / "wiki" / "index.md"
    index_text = index_path.read_text(errors="replace") if index_path.exists() else ""

    if not args.strict:
        findings = run_legacy_checks(files, index_text)
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

    hard, soft = run_strict_checks(files, index_text)
    if args.json:
        import json
        print(json.dumps({"hard": hard, "soft": soft}, indent=2))
    else:
        if not hard and not soft:
            print("No OKF conformance gaps found.")
        else:
            if hard:
                print(f"{len(hard)} HARD (OKF §9 conformance) finding(s):")
                for f in hard:
                    print(f"  - {f}")
            if soft:
                print(f"{len(soft)} SOFT finding(s):")
                for f in soft:
                    print(f"  - {f}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
