#!/usr/bin/env python3
"""instance_guard.py — block commits that leak private runtime state into the repo.

The rules live in ``app.instance_leak_rules`` (see that module for what leaks and
why); this script is the standalone CLI around them:

  • **pre-push** — ``app.mission_runner.commit_instance`` imports the same pure core
    and runs ``find_violations`` over the staged changes before committing,
    unstaging any hit so it is never committed or pushed.
  • **post-push** — the CI workflow (``.github/workflows/instance-guard.yml``) runs
    this script over all tracked files as the backstop.

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
from pathlib import Path

# The pure core lives in the installed package, so the daemon's pre-push gate
# imports it instead of exec'ing a file out of the agent-writable tree. A
# standalone CI run has no package on sys.path — add the repo's koan/ dir.
_KOAN_PKG = Path(__file__).resolve().parents[1] / "koan"
if str(_KOAN_PKG) not in sys.path:
    sys.path.insert(0, str(_KOAN_PKG))

from app.instance_leak_rules import REASONS, find_violations  # noqa: E402


def tracked_files() -> list[str]:
    """All git-tracked paths in the repo (impure; not unit-tested).

    ``-z`` so non-ASCII names arrive unquoted: line-oriented ``git ls-files``
    C-quotes them (core.quotePath), and the prefix rules would miss a quoted
    name.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in out.stdout.split("\0") if p]


# --- output ----------------------------------------------------------------

_RULE = "━" * 62


def render_success(stream) -> None:
    print(_RULE, file=stream)
    print("  📁  Instance Guard", file=stream)
    print(_RULE, file=stream)
    print("", file=stream)
    print("  ✅ PASSED — no private instance/, instance.example/skills/ or projects.yaml leak.", file=stream)
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
