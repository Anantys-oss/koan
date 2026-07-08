#!/usr/bin/env python3
"""
wiki_sync_ci.py — CI driver for the wiki-sync backstop (see wiki-sync.yml).

Runs scripts/wiki_check.py to find gaps, and if any exist, invokes the
configured CLI provider (via koan's own app.provider.run_command — the same
helper skills/core/spec_generator.py and github_intent.py already use) with a
tightly-scoped prompt to fix ONLY the specific gaps found: frontmatter fields,
wiki/index.md entries, and specs/<NNN-slug>/ computed status. It never asks
the LLM to touch doc/spec bodies or code.

This script does not commit or push anything itself — the calling workflow
step handles `git status`/`git commit`/`git push` after this returns, so a run
with zero real changes is a safe no-op.

Must be invoked with cwd=koan/ and PYTHONPATH=. (same convention as
.github/workflows/tests.yml), and KOAN_ROOT set to the koan/ directory.

Usage:
    cd koan && PYTHONPATH=. KOAN_ROOT="$PWD" python3 ../scripts/wiki_sync_ci.py --base-ref origin/main
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROMPT_TEMPLATE = """You are fixing wiki-bookkeeping gaps in koan's LLM Wiki (see wiki/SCHEMA.md for full conventions). This is a narrowly-scoped, mechanical fix — not a design or code change.

## Gaps found

{findings}

## Rules (do not deviate)

- Only touch: YAML frontmatter blocks (type/title/tags/created/updated) on the specific files named above, `wiki/index.md`, and `wiki/log.md`.
- Never edit doc/spec body content, never touch code, never touch `specs/<NNN-slug>/**` (those files are deliberately never frontmattered — see wiki/SCHEMA.md).
- For missing frontmatter: read the file, derive `title` from its H1, `tags` from its parent folder / SCHEMA.md's tag taxonomy, `type` from SCHEMA.md's page-type rules, and get real `created`/`updated` dates from `git log --diff-filter=A --format=%ad --date=short -- <file> | tail -1` and `git log -1 --format=%ad --date=short -- <file>`.
- For a stale `updated:` date: bump it to today using the same git-log approach (last-modified date), don't guess.
- For a missing `wiki/index.md` entry: add one line under the correct section, matching the existing format in that file (a markdown link plus a one-sentence, content-accurate summary).
- For a `specs/<NNN-slug>/` status mismatch: recompute the checkbox ratio in its `tasks.md` (see wiki/SCHEMA.md's status thresholds: 0% = draft, partial = in-progress, ~100% = shipped) and update the status word wiki/index.md records for that feature, under "Specs — Active Features".
- Append one line to `wiki/log.md` summarizing what you fixed, in the existing log format.
- Make surgical edits — do not rewrite whole files.
"""


def sh(*args, cwd=REPO_ROOT, check=True):
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--dry-run", action="store_true", help="Report findings and the prompt that would be sent, but don't invoke the LLM")
    args = parser.parse_args(argv)

    check_result = sh(
        sys.executable, str(REPO_ROOT / "scripts" / "wiki_check.py"),
        "--base-ref", args.base_ref, "--json",
        check=False,
    )
    import json
    findings = json.loads(check_result.stdout or "[]")

    if not findings:
        print("wiki_sync_ci: no gaps found, nothing to do.")
        return 0

    print(f"wiki_sync_ci: {len(findings)} gap(s) found:")
    for f in findings:
        print(f"  - {f}")

    findings_text = "\n".join(f"- {f}" for f in findings)
    prompt = PROMPT_TEMPLATE.format(findings=findings_text)

    if args.dry_run:
        print("\n--- prompt that would be sent (--dry-run) ---\n")
        print(prompt)
        return 0

    from app.provider import run_command

    try:
        output = run_command(
            prompt=prompt,
            project_path=str(REPO_ROOT),
            allowed_tools=["Read", "Edit", "Write", "Grep", "Glob", "Bash(git log:*)"],
            model_key="chat",
            max_turns=20,
            timeout=280,
            max_turns_source=None,
        )
    except (RuntimeError, OSError) as e:
        print(f"wiki_sync_ci: LLM fixer failed: {e}", file=sys.stderr)
        return 1

    print("wiki_sync_ci: LLM fixer output:")
    print(output)

    # Report remaining gaps (informational — the workflow commits whatever changed either way)
    recheck = sh(
        sys.executable, str(REPO_ROOT / "scripts" / "wiki_check.py"),
        "--base-ref", args.base_ref,
        check=False,
    )
    print("wiki_sync_ci: post-fix check:")
    print(recheck.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
