"""Kōan -- Pull-Request activity report engine.

Builds a per-project + global digest of Kōan's GitHub Pull-Request activity
over a 7-day (week) or 30-day (month) window, suitable for posting to the
communication channel as a fenced markdown code block.

Metrics (all scoped to Kōan's own ``gh`` user, ``get_gh_username()``):

* **created**             — PRs authored by Kōan, created in the window.
* **merged**              — cohort: of the created-in-window set, how many are
                            merged *now*. The percentage is ``merged / created``.
* **interacted**          — PRs Kōan was *involved* in (``involves:USER``),
                            updated in the window. Includes human-authored PRs
                            Kōan commented on.
* **interacted_merged**   — PRs Kōan interacted with (any time) that **merged
                            during the window** (``involves:USER merged:WINDOW``).

PR counts come from GitHub (the dashboard "usage page" tracks tokens/cost only).
We use a single aliased GraphQL ``search`` call per chunk of repos — extending
the pattern in :func:`app.github.batch_count_open_prs` — so the whole report
costs a handful of API calls instead of one request per repo per metric, which
would blow the search API rate limit across many projects.

Known limitations of the ``involves:`` source:

* A pure force-push / rebase by Kōan on a *human-authored* PR without an
  accompanying comment does not surface in ``involves:`` — Kōan's own PRs are
  always caught via ``author``/``involves``.
* ``interacted`` may *overcount*: ``involves:USER updated:WINDOW`` matches any
  PR where Kōan was *ever* involved that was *updated by anyone* in the window
  (e.g. a CI bump or someone else's comment), not strictly PRs Kōan acted on
  during the window. GitHub search cannot time-scope the involvement itself.
"""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Repos per GraphQL call. Each repo emits 4 aliased search fields, so this keeps
# us well under GraphQL node/complexity limits while minimising round-trips.
_REPOS_PER_QUERY = 15

# The four metrics, in display order. Each maps to a GitHub search query
# template parameterised by repo/user/window.
_METRICS = ("created", "merged", "interacted", "interacted_merged")


def _window_dates(days: int) -> Tuple[date, date]:
    """Return (start, end) for a trailing window of ``days`` ending today."""
    end = date.today()
    start = end - timedelta(days=days - 1)
    return start, end


def _search_query(metric: str, repo: str, user: str, start: date, end: date) -> str:
    """Build the GitHub search query string for one metric/repo."""
    window = f"{start.isoformat()}..{end.isoformat()}"
    base = f"repo:{repo} type:pr"
    if metric == "created":
        return f"{base} author:{user} created:{window}"
    if metric == "merged":
        return f"{base} author:{user} created:{window} is:merged"
    if metric == "interacted":
        return f"{base} involves:{user} updated:{window}"
    if metric == "interacted_merged":
        return f"{base} involves:{user} merged:{window}"
    raise ValueError(f"unknown metric: {metric}")


def resolve_repos(koan_root) -> List[Tuple[str, str]]:
    """Return ``[(project_name, owner/repo), ...]`` for known projects.

    Reads each project's ``github_url`` from ``projects.yaml`` (merged config),
    falling back to the ``origin`` git remote when the field is absent.
    Projects without a resolvable repo are skipped.
    """
    from app.utils import get_known_projects
    from app.projects_config import load_projects_config, get_project_config

    config = load_projects_config(str(koan_root)) or {}
    repos: List[Tuple[str, str]] = []
    seen = set()
    for name, path in get_known_projects():
        repo = (get_project_config(config, name) or {}).get("github_url")
        if not repo and path:
            try:
                from app.github import origin_repo
                repo = origin_repo(path)
            except Exception as e:
                print(f"[pr_report] origin_repo failed for {name}: {e}",
                      file=sys.stderr)
                repo = None
        if not repo:
            continue
        key = (name, repo)
        if key in seen:
            continue
        seen.add(key)
        repos.append((name, repo))
    return repos


def _empty_counts() -> Dict[str, int]:
    return {m: 0 for m in _METRICS}


def _run_search_batch(
    batch: List[Tuple[str, str]],
    safe_user: str,
    start: date,
    end: date,
) -> Optional[Dict[str, Dict[str, int]]]:
    """Run one aliased GraphQL search call for ``batch`` of ``(name, repo)``.

    Returns ``{name: {metric: count}}`` on success, or ``None`` when the call
    itself fails (network error, timeout, or a GraphQL error — ``gh api
    graphql`` exits non-zero when the response carries an ``errors`` array,
    e.g. an inaccessible repo). Missing/invalid per-alias nodes default to 0.
    """
    from app.github import run_gh

    fragments = []
    alias_map: Dict[str, Tuple[str, str]] = {}  # alias -> (project, metric)
    for i, (name, repo) in enumerate(batch):
        safe_repo = repo.replace('"', '\\"')
        for metric in _METRICS:
            alias = f"r{i}_{metric}"
            alias_map[alias] = (name, metric)
            query = _search_query(metric, safe_repo, safe_user, start, end)
            fragments.append(
                f'{alias}: search(query: "{query}", type: ISSUE, first: 0) '
                f'{{ issueCount }}'
            )

    gql = "query { " + " ".join(fragments) + " }"
    try:
        output = run_gh("api", "graphql", "-f", f"query={gql}", timeout=30)
        data = json.loads(output).get("data", {}) or {}
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError,
            OSError, TypeError, ValueError):
        return None

    result: Dict[str, Dict[str, int]] = {name: _empty_counts() for name, _ in batch}
    for alias, (name, metric) in alias_map.items():
        node = data.get(alias)
        if isinstance(node, dict):
            count = node.get("issueCount")
            if isinstance(count, int) and count >= 0:
                result[name][metric] = count
    return result


def fetch_pr_counts(
    repos: List[Tuple[str, str]],
    user: str,
    start: date,
    end: date,
) -> Tuple[Dict[str, Dict[str, int]], bool]:
    """Fetch PR metric counts per project via aliased GraphQL search.

    Args:
        repos: ``[(project_name, owner/repo), ...]``.
        user: Kōan's GitHub username.
        start, end: inclusive window bounds.

    Returns:
        ``(counts, partial)`` where ``counts`` maps project name to a dict with
        the four metric keys, and ``partial`` is True when at least one repo
        could not be fetched (it is reported as zeros).

    Repos are fetched in chunks of ``_REPOS_PER_QUERY``. A whole-chunk failure
    (one bad repo makes ``gh api graphql`` exit non-zero) is isolated by
    retrying that chunk's repos one at a time, so a single inaccessible repo
    only zeroes itself instead of its chunk-mates.
    """
    counts: Dict[str, Dict[str, int]] = {name: _empty_counts() for name, _ in repos}
    if not repos or not user:
        return counts, bool(repos)

    safe_user = user.replace('"', '\\"')
    partial = False

    for chunk_start in range(0, len(repos), _REPOS_PER_QUERY):
        chunk = repos[chunk_start:chunk_start + _REPOS_PER_QUERY]
        result = _run_search_batch(chunk, safe_user, start, end)
        if result is not None:
            counts.update(result)
            continue

        # Chunk failed: retry each repo individually so one bad repo doesn't
        # zero its chunk-mates. Only repos that still fail stay at zero.
        for one in chunk:
            single = _run_search_batch([one], safe_user, start, end)
            if single is not None:
                counts.update(single)
            else:
                partial = True

    return counts, partial


def _pct(numerator: int, denominator: int) -> int:
    """Integer percentage, guarded against zero denominator."""
    if denominator <= 0:
        return 0
    return round(numerator / denominator * 100)


def _fmt_tokens(n: int) -> str:
    """Compact human token count, e.g. 4200000 -> '4.2M', 51000 -> '51K'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _table_row(label: str, c: Dict[str, int]) -> str:
    """Format one fixed-width table row (a project row or the TOTAL row)."""
    return (
        f"{label:<14} {c['created']:>5} {c['merged']:>5} "
        f"{_pct(c['merged'], c['created']):>3}% {c['interacted']:>6} "
        f"{c['interacted_merged']:>5}"
    )


def format_report(
    counts: Dict[str, Dict[str, int]],
    usage_by_project: Optional[dict],
    days: int,
    start: date,
    end: date,
    partial: bool = False,
) -> str:
    """Assemble the report as a fenced markdown code block."""
    window_label = "month" if days == 30 else "week"
    usage_by_project = usage_by_project or {}

    # Global totals.
    g = _empty_counts()
    for c in counts.values():
        for m in _METRICS:
            g[m] += c.get(m, 0)

    g_tokens = 0
    g_cost = 0.0
    for u in usage_by_project.values():
        g_tokens += (u.get("input_tokens", 0) or 0) + (u.get("output_tokens", 0) or 0)
        g_cost += u.get("total_cost_usd", 0.0) or 0.0

    lines: List[str] = []
    lines.append(f"PR Report — {window_label} ({start.isoformat()} .. {end.isoformat()})")
    lines.append("")
    lines.append("GLOBAL")
    lines.append(f"  Created:            {g['created']}")
    lines.append(
        f"  Merged:             {g['merged']}  "
        f"({_pct(g['merged'], g['created'])}% of created)"
    )
    lines.append(f"  Interacted:         {g['interacted']}")
    lines.append(f"  Interacted+merged:  {g['interacted_merged']}")
    if g_tokens > 0 or g_cost > 0:
        usage_parts = []
        if g_tokens > 0:
            usage_parts.append(f"{_fmt_tokens(g_tokens)} tok")
        if g_cost > 0:
            usage_parts.append(f"${g_cost:.2f}")
        lines.append("  Usage:              " + "  |  ".join(usage_parts))

    # Per-project table.
    rows = sorted(counts.items(), key=lambda kv: (-kv[1].get("created", 0), kv[0]))
    if rows:
        lines.append("")
        header = f"{'project':<14} {'creat':>5} {'merg':>5} {'%':>4} {'inter':>6} {'i+m':>5}"
        sep = "-" * len(header)
        lines.append(header)
        lines.append(sep)
        for name, c in rows:
            display = name[:13] + ("…" if len(name) > 13 else "")
            lines.append(_table_row(display, c))
        lines.append(sep)
        lines.append(_table_row("TOTAL", g))

    if partial:
        lines.append("")
        lines.append("(partial — some projects failed to fetch and count as 0)")

    inner = "\n".join(lines)
    return f"```\n{inner}\n```"


def build_report(koan_root, days: int) -> str:
    """Build the full PR activity report for the trailing ``days`` window.

    Returns a fenced markdown code block, or a friendly plain message when no
    repos are configured or Kōan's GitHub user can't be resolved.
    """
    from app.github import get_gh_username

    repos = resolve_repos(koan_root)
    if not repos:
        return "No GitHub-backed projects configured — nothing to report."

    user = ""
    try:
        user = get_gh_username()
    except Exception as e:
        print(f"[pr_report] get_gh_username failed: {e}", file=sys.stderr)
        user = ""
    if not user:
        return "Could not resolve the GitHub user (gh auth). Cannot build the PR report."

    start, end = _window_dates(days)
    counts, partial = fetch_pr_counts(repos, user, start, end)

    usage_by_project = {}
    try:
        from app import cost_tracker
        instance_dir = Path(koan_root) / "instance"
        usage_by_project = cost_tracker.summarize_by_project(instance_dir, days=days)
    except Exception as e:
        print(f"[pr_report] usage summary failed: {e}", file=sys.stderr)
        usage_by_project = {}

    return format_report(counts, usage_by_project, days, start, end, partial)
