#!/usr/bin/env python3
"""
spec_change_guard.py — block PRs that change a durable design contract without an
explicit architectural-change declaration.

Kōan's specs discipline treats `specs/components/**` and `specs/skills/**` as durable
design *contracts* (as opposed to the ephemeral `specs/<NNN-slug>/` speckit planning
folders). Changing one is an architectural change: it must be deliberate, rare, and
declared in the PR so a human reviews the new architecture *before* approval — never a
retroactive edit to make the spec mirror whatever code was written. See
docs/design/spec-changes-are-architectural.md and .specify/memory/constitution.md
(Principle II).

This guard is the load-bearing half of that discipline (Constitution Principle V:
prose is advisory; only a git-enforced control has teeth). It decides WHETHER a PR
touches a durable contract and, if so, whether the PR body declares it. It never edits
anything.

A valid declaration is a checked Markdown task line whose text contains the phrase
"architectural change" (case-insensitive), e.g.:

    - [x] **Architectural change** — this PR modifies a durable design contract.

Usage:
    python3 scripts/spec_change_guard.py [--base-ref <ref>]
        [--pr-body-file <path> | --pr-body -]
        [--changed-file <path> ...]

Examples:
    python3 scripts/spec_change_guard.py --base-ref origin/main --pr-body-file body.md
    python3 scripts/spec_change_guard.py --pr-body - --changed-file specs/components/core.md

Exit codes:
    0  no durable contract changed, OR changed with a valid declaration
    1  durable contract changed without a declaration (or no PR body supplied)
    2  usage error
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass, field

# A durable contract lives directly under specs/components/ or anywhere under
# specs/skills/. Numbered speckit folders (specs/<NNN-slug>/**) are neither, so they
# are excluded by construction.
_COMPONENTS_RE = re.compile(r"^specs/components/[^/]+\.md$")
_SKILLS_RE = re.compile(r"^specs/skills/.+\.md$")

# Never a live contract: per-directory index bookkeeping and the skill-spec template.
_EXCLUDED_BASENAMES = {"index.md"}
_EXCLUDED_PATHS = {"specs/skills/SKILL_SPEC_TEMPLATE.md"}

# A checked task line ("- [x] ... architectural change ...") anywhere in the PR body.
_DECLARATION_RE = re.compile(
    r"^\s*[-*]\s*\[x\]\s*.*architectural change",
    re.IGNORECASE | re.MULTILINE,
)

DECLARATION_LINE = (
    "- [x] **Architectural change** — this PR modifies a durable design contract "
    "(`specs/components/**` or `specs/skills/**`). The new architecture needs review "
    "before approval. Rationale: <one line>"
)


def is_durable_contract(path: str) -> bool:
    """True iff ``path`` is a durable design contract spec.

    Matches ``specs/components/<name>.md`` or ``specs/skills/**/<name>.md``, excluding
    any ``index.md`` and the skill-spec template. Paths are normalised to POSIX form so
    Windows-style separators don't slip past the check.
    """
    norm = path.replace("\\", "/").strip()
    if norm in _EXCLUDED_PATHS:
        return False
    if posixpath.basename(norm) in _EXCLUDED_BASENAMES:
        return False
    return bool(_COMPONENTS_RE.match(norm) or _SKILLS_RE.match(norm))


def _body_below_frontmatter(text: str) -> str:
    """Return the Markdown body with any leading YAML frontmatter block stripped.

    Frontmatter is a block that starts on line 1 with a ``---`` delimiter and ends
    at the next ``---`` delimiter. A file with no such block is all body. An
    unterminated block is treated as body (nothing stripped) so a malformed file
    can never masquerade as a body-less bookkeeping change.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


def is_frontmatter_only_change(old_text: str, new_text: str) -> bool:
    """True iff ``old_text`` -> ``new_text`` leaves the Markdown body unchanged.

    The body below the YAML frontmatter is the durable contract; a diff that only
    touches frontmatter (e.g. a ``/brain sync`` or wiki-sync ``updated:`` date bump)
    is bookkeeping, explicitly exempt from the architectural-change declaration per
    the constitution's wiki-bookkeeping exemption (Principle I) and CLAUDE.md. Such
    a change must not trip this guard — otherwise the wiki-sync backstop's own
    frontmatter auto-commit to a contract file would spuriously block the PR.
    """
    if old_text == new_text:
        return True
    return _body_below_frontmatter(old_text) == _body_below_frontmatter(new_text)


def has_architecture_declaration(pr_body: str | None) -> bool:
    """True iff ``pr_body`` contains a checked architectural-change declaration."""
    if not pr_body:
        return False
    return bool(_DECLARATION_RE.search(pr_body))


@dataclass
class GuardVerdict:
    ok: bool
    undeclared_contracts: list[str] = field(default_factory=list)
    fail_closed: bool = False


def evaluate(changed_files: list[str], pr_body: str | None) -> GuardVerdict:
    """Decide pass/fail from a changed-file set and a PR body. Pure function."""
    contracts = sorted({f for f in changed_files if is_durable_contract(f)})
    if not contracts:
        return GuardVerdict(ok=True)
    if pr_body is None:
        # Fail closed: contracts changed but we couldn't read the PR body to check.
        return GuardVerdict(ok=False, undeclared_contracts=contracts, fail_closed=True)
    if has_architecture_declaration(pr_body):
        return GuardVerdict(ok=True)
    return GuardVerdict(ok=False, undeclared_contracts=contracts)


def _file_at_ref(ref: str, path: str) -> str | None:
    """Content of ``path`` at ``ref``, or None if it doesn't exist there (impure)."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def _merge_base(base_ref: str) -> str:
    """Merge base of ``base_ref`` and HEAD, matching the ``base...HEAD`` diff (impure)."""
    result = subprocess.run(
        ["git", "merge-base", base_ref, "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or base_ref


def _is_bookkeeping_only(path: str, old_ref: str) -> bool:
    """True iff ``path`` is a contract whose diff since ``old_ref`` is frontmatter-only.

    A newly added contract (absent at ``old_ref``) is never bookkeeping.
    """
    if not is_durable_contract(path):
        return False
    old = _file_at_ref(old_ref, path)
    if old is None:
        return False
    new = _file_at_ref("HEAD", path)
    if new is None:
        return False
    return is_frontmatter_only_change(old, new)


def changed_files(base_ref: str) -> list[str]:
    """Added/modified/deleted files vs ``base_ref`` (impure; not unit-tested).

    Uses ``--diff-filter=AMD`` (not just ``AM``): **deleting** or **retiring** a
    durable contract is at least as architectural as editing one, so a bare
    ``git rm specs/components/core.md`` must not slip past the gate undeclared.
    ``--no-renames`` decomposes a rename into a delete of the old path plus an add of
    the new one, so both sides of a moved contract are evaluated. Deleting an
    ephemeral speckit file, an ``index.md``, or the template is harmless — those are
    already excluded by ``is_durable_contract()``.

    Durable-contract files whose diff is frontmatter-only (a bookkeeping date bump
    from ``/brain sync`` or the wiki-sync backstop, exempt per Principle I) are
    dropped so the guard doesn't demand an architectural-change declaration for them.
    """
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AMD", "--no-renames", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    paths = [line for line in out.stdout.splitlines() if line.strip()]
    old_ref = _merge_base(base_ref)
    return [p for p in paths if not _is_bookkeeping_only(p, old_ref)]


def _read_pr_body(args: argparse.Namespace) -> str | None:
    if args.pr_body == "-":
        return sys.stdin.read()
    if args.pr_body_file:
        with open(args.pr_body_file, encoding="utf-8") as fh:
            return fh.read()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and __doc__.strip().splitlines()[0])
    parser.add_argument("--base-ref", default="origin/main", help="diff base (default: origin/main)")
    parser.add_argument("--pr-body-file", help="path to a file containing the PR body")
    parser.add_argument("--pr-body", help="'-' to read the PR body from stdin")
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        dest="changed_file",
        help="explicit changed path (repeatable); bypasses git diff when given",
    )
    args = parser.parse_args(argv)

    if args.pr_body not in (None, "-"):
        parser.error("--pr-body only accepts '-' (stdin); use --pr-body-file for a path")

    changed = args.changed_file if args.changed_file else changed_files(args.base_ref)
    pr_body = _read_pr_body(args)
    verdict = evaluate(changed, pr_body)

    if verdict.ok:
        print("spec-change guard: OK (no undeclared durable-contract change).")
        return 0

    print("spec-change guard: FAILED", file=sys.stderr)
    print("", file=sys.stderr)
    print("This PR changes durable design contract(s):", file=sys.stderr)
    for path in verdict.undeclared_contracts:
        print(f"  - {path}", file=sys.stderr)
    print("", file=sys.stderr)
    if verdict.fail_closed:
        print(
            "No PR body was supplied, so the architectural-change declaration could not "
            "be verified (failing closed).",
            file=sys.stderr,
        )
    print(
        "Changing a durable contract is an architectural change. Declare it in the PR "
        "body by checking this line:",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print(f"    {DECLARATION_LINE}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "See docs/design/spec-changes-are-architectural.md. If this is a retroactive "
        "edit to make the spec match code, revert it and make the code conform instead.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
