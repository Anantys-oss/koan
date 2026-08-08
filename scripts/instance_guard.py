#!/usr/bin/env python3
"""instance_guard.py — block commits that leak private runtime state into the repo.

Two directories are easy to pollute by accident, and both break the repo when they are:

  • ``instance/`` is a **private, gitignored** runtime tree, hydrated from
    ``instance.example/`` on setup. Nothing under it may ever be tracked — a file only
    lands there via ``git add -f`` past the ``.gitignore``, and once committed it leaks
    an operator's private state (missions, memory, secrets-adjacent config) forever.
  • ``instance.example/skills/`` must stay empty: ``instance.example/`` is the *template*
    for a fresh instance, and skills belong in ``koan/skills/`` (core) or an operator's
    private ``instance/skills/`` (custom) — never in the shared template. A skill dropped
    into the example tree gets copied into every new instance and is easy to mistake for
    a core skill.

The guard scans the set of git-tracked paths and fails if any match. Pure ``classify``
+ ``find_violations`` do the deciding; the impure ``tracked_files`` shells out to git.

Usage:
    python3 scripts/instance_guard.py                 # scan all tracked files
    python3 scripts/instance_guard.py --path a --path b   # scan explicit paths (tests)

Exit codes:
    0  no tracked file violates either rule
    1  at least one violating path found
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Why exact-prefix matching and not a glob: ``instance.example/`` also starts with
# ``instance`` but is a *tracked* template, so we must match ``instance/`` (the private
# tree) without swallowing its sibling.
_INSTANCE = "instance"
_EXAMPLE_SKILLS = "instance.example/skills"

# violation reason -> human-readable explanation for the failure report.
REASONS = {
    "instance": (
        "tracked file inside the private, gitignored instance/ tree "
        "(it was force-added past .gitignore)"
    ),
    "instance-example-skills": (
        "skill placed in instance.example/ — the template must stay skill-free; "
        "put core skills in koan/skills/ or custom ones in your private instance/skills/"
    ),
}


def classify(path: str) -> str | None:
    """Return the violation reason for ``path``, or None if it is allowed.

    ``instance.example/`` (the tracked template) is deliberately *not* an ``instance``
    violation — only ``instance`` itself or paths under ``instance/`` are.
    """
    norm = path.replace("\\", "/").strip()
    if norm == _EXAMPLE_SKILLS or norm.startswith(_EXAMPLE_SKILLS + "/"):
        return "instance-example-skills"
    if norm == _INSTANCE or norm.startswith(_INSTANCE + "/"):
        return "instance"
    return None


def find_violations(paths: list[str]) -> list[tuple[str, str]]:
    """Return ``(path, reason)`` for every violating path, sorted by path. Pure."""
    hits = [(p, r) for p in paths if (r := classify(p)) is not None]
    return sorted(hits, key=lambda pr: pr[0])


def tracked_files() -> list[str]:
    """All git-tracked paths in the repo (impure; not unit-tested)."""
    out = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


# --- output ----------------------------------------------------------------

_RULE = "━" * 62


def render_success(stream) -> None:
    print(_RULE, file=stream)
    print("  📁  Instance Guard", file=stream)
    print(_RULE, file=stream)
    print("", file=stream)
    print("  ✅ PASSED — no private instance/ or instance.example/skills/ leak.", file=stream)
    print("", file=stream)
    print(_RULE, file=stream)


def render_failure(violations: list[tuple[str, str]], stream) -> None:
    print(_RULE, file=stream)
    print("  📁  Instance Guard", file=stream)
    print(_RULE, file=stream)
    print("", file=stream)
    print("  ❌ FAILED — files were committed where they must never live.", file=stream)
    for reason in sorted({r for _, r in violations}):
        print("", file=stream)
        print(f"  ⚠️  {REASONS.get(reason, reason)}:", file=stream)
        for path, r in violations:
            if r == reason:
                print(f"       • {path}", file=stream)
    print("", file=stream)
    print("  To fix: remove the file from git tracking, e.g.", file=stream)
    print("      git rm --cached <path>   # keep it locally, stop tracking it", file=stream)
    print("", file=stream)
    print(_RULE, file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__ and __doc__.strip().splitlines()[0]
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        dest="paths",
        help="explicit path to check (repeatable); bypasses git ls-files when given",
    )
    args = parser.parse_args(argv)

    paths = args.paths if args.paths else tracked_files()
    violations = find_violations(paths)

    if not violations:
        render_success(sys.stdout)
        return 0

    render_failure(violations, sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
