"""Instance leak rules — the pure core shared by both guard layers.

A few paths must never be git-tracked, because what lives there is private
operator state:

  • ``instance/`` is a **private, gitignored** runtime tree, hydrated from
    ``instance.example/`` on setup. Nothing under it may ever be tracked — a file
    only lands there via ``git add -f`` past the ``.gitignore``, and once
    committed it leaks an operator's private state (missions, memory,
    secrets-adjacent config) forever.
  • ``instance.example/skills/`` must stay empty: ``instance.example/`` is the
    *template* for a fresh instance, and skills belong in ``koan/skills/`` (core)
    or an operator's private ``instance/skills/`` (custom) — never in the shared
    template. A skill dropped into the example tree gets copied into every new
    instance and is easy to mistake for a core skill.
  • ``projects.yaml`` and its backups (``projects.yaml.*`` — e.g. the
    timestamped ``projects.yaml.bak-YYYYMMDD-HHMMSS`` files some setup tooling
    writes next to it) hold operator paths and private repo config. They are
    gitignored by design; only the tracked template ``projects.example.yaml``
    may live in the repo. A ``git add -A`` (as in ``commit_instance``) sweeps
    any of these in otherwise.

Two layers enforce the same rules through this module:

  • **pre-push** — ``app.mission_runner.commit_instance`` runs
    :func:`find_violations` over the staged changes before committing and
    unstages any hit, so a leak is neither committed nor pushed.
  • **post-push** — ``scripts/instance_guard.py`` (the CI workflow
    ``.github/workflows/instance-guard.yml``) runs the same core over all
    tracked files as the backstop.

This module is stdlib-only and importable (``app.instance_leak_rules``). It
lives in the installed package — *not* in the agent-writable instance tree —
so the daemon's pre-push gate imports its own code instead of executing a file
the agent can modify; ``scripts/instance_guard.py`` re-exports the core so the
standalone CLI checks the identical rules.
"""

from __future__ import annotations

# Why exact-prefix matching and not a glob: ``instance.example/`` also starts with
# ``instance`` but is a *tracked* template, so we must match ``instance/`` (the private
# tree) without swallowing its sibling.
_INSTANCE = "instance"
_EXAMPLE_SKILLS = "instance.example/skills"
# Live project config + any backup derived from it (projects.yaml.bak-*, .old, …).
# The tracked template projects.example.yaml does NOT start with "projects.yaml."
# and therefore stays allowed.
_PROJECTS = "projects.yaml"

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
    "projects-config": (
        "live projects.yaml (or a projects.yaml.* backup) tracked in the repo — "
        "it holds operator paths and private repo config; it must stay gitignored, "
        "only the template projects.example.yaml may be committed"
    ),
}


def classify(path: str) -> str | None:
    """Return the violation reason for ``path``, or None if it is allowed.

    ``instance.example/`` (the tracked template) is deliberately *not* an ``instance``
    violation — only ``instance`` itself or paths under ``instance/`` are.
    Likewise ``projects.example.yaml`` (the tracked template config) is *not* a
    ``projects-config`` violation: only ``projects.yaml`` itself or names derived
    from it (``projects.yaml.*``) are.
    """
    norm = path.replace("\\", "/").strip()
    if norm == _EXAMPLE_SKILLS or norm.startswith(_EXAMPLE_SKILLS + "/"):
        return "instance-example-skills"
    if norm == _INSTANCE or norm.startswith(_INSTANCE + "/"):
        return "instance"
    if norm == _PROJECTS or norm.startswith(_PROJECTS + "."):
        return "projects-config"
    return None


def find_violations(paths: list[str]) -> list[tuple[str, str]]:
    """Return ``(path, reason)`` for every violating path, sorted by path. Pure."""
    hits = [(p, r) for p in paths if (r := classify(p)) is not None]
    return sorted(hits, key=lambda pr: pr[0])
