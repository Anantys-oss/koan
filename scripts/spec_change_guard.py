#!/usr/bin/env python3
"""
spec_change_guard.py — block PRs that *remove or rewrite* a durable design contract
without an explicit architectural-change declaration.

Kōan's specs discipline treats `specs/components/**` and `specs/skills/**` as durable
design *contracts* (as opposed to the ephemeral `specs/<NNN-slug>/` speckit planning
folders). The guard distinguishes two kinds of contract change:

  • **Additive** — a brand-new contract file, or new lines/paragraphs appended to an
    existing one. Growing the specs is encouraged; it never invalidates prior design,
    so it passes freely, no declaration required.
  • **Destructive** — deleting a contract, or rewriting/removing existing body lines.
    This can silently contradict a previously reviewed contract, so it is an
    architectural change: it must be deliberate, rare, and declared in the PR body so a
    human reviews the new architecture *before* approval — never a retroactive edit to
    make the spec mirror whatever code was written. See
    docs/design/spec-changes-are-architectural.md and .specify/memory/constitution.md
    (Principle II).

This guard is the load-bearing half of that discipline (Constitution Principle V:
prose is advisory; only a git-enforced control has teeth). It decides WHETHER a PR
destructively touches a durable contract and, if so, whether the PR body declares it.
It never edits anything.

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
    0  no durable contract destructively changed, OR changed with a valid declaration
    1  durable contract removed/rewritten without a declaration (or no PR body supplied)
    2  usage error
"""

from __future__ import annotations

import argparse
import difflib
import io
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
    the constitution's wiki-bookkeeping exemption (Principle I) and CLAUDE.md.
    """
    if old_text == new_text:
        return True
    return _body_below_frontmatter(old_text) == _body_below_frontmatter(new_text)


def is_addition_only(old_text: str, new_text: str) -> bool:
    """True iff the body ``old_text`` -> ``new_text`` only *inserts* lines.

    Compares the Markdown bodies below the frontmatter line-by-line. A change is
    addition-only when every diff hunk is an ``equal`` or ``insert`` — i.e. no existing
    body line is deleted or replaced. Appending a paragraph or inserting a new section
    between existing ones is addition-only; rewriting, deleting, or reordering a line is
    not. Additive growth can never contradict a previously reviewed contract, so it is
    exempt from the architectural-change declaration.
    """
    old_body = _body_below_frontmatter(old_text).splitlines()
    new_body = _body_below_frontmatter(new_text).splitlines()
    matcher = difflib.SequenceMatcher(a=old_body, b=new_body, autojunk=False)
    return all(tag in ("equal", "insert") for tag, *_ in matcher.get_opcodes())


def has_architecture_declaration(pr_body: str | None) -> bool:
    """True iff ``pr_body`` contains a checked architectural-change declaration."""
    if not pr_body:
        return False
    return bool(_DECLARATION_RE.search(pr_body))


# --- change classification -------------------------------------------------

ADDED = "added"
DELETED = "deleted"
REWRITTEN = "rewritten"  # existing body lines removed/altered — needs a declaration


@dataclass
class ContractChange:
    """One durable-contract change in a PR diff.

    ``destructive`` is the gate signal: a deletion or a rewrite (existing body lines
    removed/altered) needs an architectural-change declaration; a pure addition does
    not. ``kind`` is carried for human-readable output only.
    """

    path: str
    kind: str = REWRITTEN
    destructive: bool = True


@dataclass
class GuardVerdict:
    ok: bool
    undeclared_contracts: list[str] = field(default_factory=list)
    allowed_additions: list[ContractChange] = field(default_factory=list)
    flagged: list[ContractChange] = field(default_factory=list)
    fail_closed: bool = False


def evaluate(changes: list, pr_body: str | None) -> GuardVerdict:
    """Decide pass/fail from a set of contract changes and a PR body. Pure function.

    ``changes`` accepts either :class:`ContractChange` objects or bare path strings.
    A bare string carries no diff information, so it is treated conservatively as a
    destructive rewrite (the manual ``--changed-file`` override and tests use this).
    Non-contract paths are ignored.
    """
    contract_changes_list: list[ContractChange] = []
    for item in changes:
        if isinstance(item, ContractChange):
            if is_durable_contract(item.path):
                contract_changes_list.append(item)
        elif is_durable_contract(item):
            contract_changes_list.append(ContractChange(item))

    additions = sorted(
        (c for c in contract_changes_list if not c.destructive), key=lambda c: c.path
    )
    destructive = sorted(
        (c for c in contract_changes_list if c.destructive), key=lambda c: c.path
    )

    if not destructive:
        # Additive-only (or nothing): always OK, no declaration needed.
        return GuardVerdict(ok=True, allowed_additions=additions)

    if pr_body is None:
        # Fail closed: destructive change but we couldn't read the PR body to check.
        return GuardVerdict(
            ok=False,
            undeclared_contracts=[c.path for c in destructive],
            allowed_additions=additions,
            flagged=destructive,
            fail_closed=True,
        )
    if has_architecture_declaration(pr_body):
        return GuardVerdict(ok=True, allowed_additions=additions, flagged=destructive)
    return GuardVerdict(
        ok=False,
        undeclared_contracts=[c.path for c in destructive],
        allowed_additions=additions,
        flagged=destructive,
    )


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


def _classify(path: str, status: str, old_ref: str) -> ContractChange | None:
    """Classify one contract ``path`` given its diff ``status`` letter (impure).

    Returns None when the change is not a durable contract or is frontmatter-only
    bookkeeping (dropped). ``status`` is the ``git diff --name-status`` letter:
    ``A`` added, ``D`` deleted, ``M`` modified.
    """
    if not is_durable_contract(path):
        return None
    if status == "A":
        return ContractChange(path, kind=ADDED, destructive=False)
    if status == "D":
        return ContractChange(path, kind=DELETED, destructive=True)
    # Modified: compare merge-base body to HEAD body.
    old = _file_at_ref(old_ref, path)
    new = _file_at_ref("HEAD", path)
    if old is None:
        return ContractChange(path, kind=ADDED, destructive=False)
    if new is None:
        return ContractChange(path, kind=DELETED, destructive=True)
    if is_frontmatter_only_change(old, new):
        return None  # bookkeeping date bump — exempt
    if is_addition_only(old, new):
        return ContractChange(path, kind=ADDED, destructive=False)
    return ContractChange(path, kind=REWRITTEN, destructive=True)


def contract_changes(base_ref: str) -> list[ContractChange]:
    """Durable-contract changes vs ``base_ref`` (impure; not unit-tested).

    Uses ``--diff-filter=AMD`` (not just ``AM``): **deleting** a durable contract is
    destructive, so a bare ``git rm specs/components/core.md`` must not slip past the
    gate. ``--no-renames`` decomposes a rename into a delete of the old path plus an add
    of the new one, so both sides of a moved contract are evaluated. Each contract is
    then classified as additive (new file / appended lines — allowed) or destructive
    (deletion / rewrite — needs a declaration); frontmatter-only bookkeeping diffs are
    dropped.
    """
    out = subprocess.run(
        ["git", "diff", "--name-status", "--diff-filter=AMD", "--no-renames", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    old_ref = _merge_base(base_ref)
    changes: list[ContractChange] = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0].strip(), parts[-1].strip()
        change = _classify(path, status[:1], old_ref)
        if change is not None:
            changes.append(change)
    return changes


def _read_pr_body(args: argparse.Namespace) -> str | None:
    if args.pr_body == "-":
        return sys.stdin.read()
    if args.pr_body_file:
        with open(args.pr_body_file, encoding="utf-8") as fh:
            return fh.read()
    return None


# --- output ----------------------------------------------------------------

_RULE = "━" * 62
_KIND_LABEL = {ADDED: "new / appended", DELETED: "deleted", REWRITTEN: "rewritten"}


def _header(stream) -> None:
    print(_RULE, file=stream)
    print("  🏛️  Spec-Change Guard", file=stream)
    print(_RULE, file=stream)


def _print_additions(additions: list[ContractChange], stream) -> None:
    if not additions:
        return
    print("", file=stream)
    print("  ➕ Additive spec changes (allowed, no declaration needed):", file=stream)
    for change in additions:
        print(f"       • {change.path}", file=stream)


def render_success(verdict: GuardVerdict) -> str:
    """Human-readable success block written to stdout on exit 0."""
    buf = io.StringIO()
    _header(buf)
    print("", file=buf)
    if verdict.allowed_additions:
        print("  ✅ PASSED — additive spec changes only.", file=buf)
        _print_additions(verdict.allowed_additions, buf)
    else:
        print("  ✅ PASSED — no durable-contract change requires a declaration.", file=buf)
    print("", file=buf)
    print(_RULE, file=buf)
    return buf.getvalue()


def _print_failure(verdict: GuardVerdict, stream) -> None:
    _header(stream)
    print("", file=stream)
    print("  ❌ FAILED — a durable design contract was removed or rewritten", file=stream)
    print("             without an architectural-change declaration.", file=stream)
    print("", file=stream)
    print("  ⚠️  Requires human approval (conflicts with a reviewed contract):", file=stream)
    for change in verdict.flagged:
        label = _KIND_LABEL.get(change.kind, change.kind)
        print(f"       • {change.path}  ({label})", file=stream)
    _print_additions(verdict.allowed_additions, stream)
    print("", file=stream)
    if verdict.fail_closed:
        print(
            "  ⚙️  No PR body was supplied, so the architectural-change declaration",
            file=stream,
        )
        print("      could not be verified (failing closed).", file=stream)
        print("", file=stream)
    print("  To proceed, declare the change by checking this line in the PR body:", file=stream)
    print("", file=stream)
    print(f"      {DECLARATION_LINE}", file=stream)
    print("", file=stream)
    print(
        "  ℹ️  See docs/design/spec-changes-are-architectural.md. If this is a",
        file=stream,
    )
    print(
        "      retroactive edit to make the spec match code, revert it and make",
        file=stream,
    )
    print("      the code conform to the contract instead.", file=stream)
    print("", file=stream)
    print(_RULE, file=stream)


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

    changed = args.changed_file if args.changed_file else contract_changes(args.base_ref)
    pr_body = _read_pr_body(args)
    verdict = evaluate(changed, pr_body)

    if verdict.ok:
        sys.stdout.write(render_success(verdict))
        return 0

    _print_failure(verdict, sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
