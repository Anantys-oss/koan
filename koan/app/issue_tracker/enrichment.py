"""PR-review issue tracker enrichment.

Parses tracker references out of a pull request body and fetches a short
summary block to inject into the review prompt as the ``{ISSUE_CONTEXT}``
variable. The backend (Jira or GitHub) is selected from the project's
``issue_tracker`` configuration in ``projects.yaml`` via
:func:`app.issue_tracker.config.get_tracker_for_project`.

Best-effort by contract: every fetch path returns ``""`` on any failure
(missing config, 404, auth error, timeout, ``gh`` unavailable) so a tracker
problem can never abort a code review. Output is capped so injected ticket
text cannot balloon the review prompt.
"""

import logging
import re
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Jira issue keys: 2-10 uppercase letters/digits (starting with a letter),
# a hyphen, then digits. e.g. PROJ-42, ABC-7. Branch-name false positives such
# as FEATURE-99 are tolerated — a 404 from Jira is handled gracefully.
_JIRA_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")

# Cross-repo GitHub references: owner/repo#number. In-repo "#123" refs are
# intentionally excluded — they are ambiguous without knowing the current repo.
_GITHUB_REF_RE = re.compile(
    r"\b([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)#(\d+)\b"
)

# Per-ticket description excerpt cap and total injected-context cap (chars).
MAX_EXCERPT_CHARS = 500
MAX_TOTAL_CHARS = 1000
# Upper bound on the number of tracker references fetched per review. Each ref
# costs one bounded network/subprocess round-trip (up to *_TIMEOUT_SECONDS each),
# so an uncapped PR body (a changelog listing dozens of tickets, or an adversarial
# author seeding ``a/b#1 a/b#2 …``) could otherwise add N×5s of latency and burn
# API quota / spawn dozens of ``gh`` subprocesses. Bounding the *fetch count*
# (not just the output size) keeps worst-case review latency bounded at roughly
# MAX_REFS × *_TIMEOUT_SECONDS regardless of PR body content. This bound only
# holds because each ref is one bounded request: the Jira path uses the
# title/body-only fetch (no comment pagination) with a JIRA_TIMEOUT_SECONDS cap.
MAX_REFS = 5
JIRA_TIMEOUT_SECONDS = 5
GH_TIMEOUT_SECONDS = 5


def parse_jira_ticket_ids(text: str) -> List[str]:
    """Extract unique Jira issue keys (``PROJ-123``) from ``text``.

    Order-preserving de-duplication so repeated mentions fetch once.
    """
    if not text:
        return []
    seen: set = set()
    result: List[str] = []
    for match in _JIRA_RE.findall(text):
        if match not in seen:
            seen.add(match)
            result.append(match)
    return result


def parse_github_issue_refs(text: str) -> List[Tuple[str, str, int]]:
    """Extract cross-repo GitHub issue refs as ``(owner, repo, number)``.

    In-repo ``#123`` references are intentionally not matched.
    """
    if not text:
        return []
    seen: set = set()
    result: List[Tuple[str, str, int]] = []
    for owner, repo, number in _GITHUB_REF_RE.findall(text):
        key = (owner, repo, number)
        if key in seen:
            continue
        seen.add(key)
        result.append((owner, repo, int(number)))
    return result


def _excerpt(body: str) -> str:
    """Collapse whitespace and cap a description to one excerpt."""
    text = " ".join((body or "").split())
    if len(text) > MAX_EXCERPT_CHARS:
        text = text[:MAX_EXCERPT_CHARS].rstrip() + "…"
    return text


def _format_block(lines: List[str]) -> str:
    """Wrap formatted per-issue lines in a heading, capped at the total budget.

    Returns ``""`` when no lines were produced. The leading newline lets the
    block sit directly after another inline placeholder in the prompt template
    without forcing a blank line when this block is empty.
    """
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > MAX_TOTAL_CHARS:
        body = body[:MAX_TOTAL_CHARS].rstrip() + "…"
    return "\n## Issue Tracker Context\n\n" + body + "\n"


def fetch_jira_issues(ticket_ids: List[str]) -> str:
    """Fetch and format Jira issue summaries. Returns ``""`` on any failure."""
    if not ticket_ids:
        return ""
    if len(ticket_ids) > MAX_REFS:
        logger.info(
            "[enrichment] capping Jira fetch from %d to %d references",
            len(ticket_ids), MAX_REFS,
        )
        ticket_ids = ticket_ids[:MAX_REFS]
    # Use the title/body-only fetch: enrichment never reads comments, so the
    # heavyweight fetch_jira_issue() (which paginates every comment) would waste
    # several round-trips per busy ticket. The lighter call makes exactly one
    # bounded request per ticket, honoring JIRA_TIMEOUT_SECONDS.
    from app.jira_notifications import fetch_jira_issue_summary

    lines: List[str] = []
    for ticket in ticket_ids:
        try:
            title, body = fetch_jira_issue_summary(ticket, timeout=JIRA_TIMEOUT_SECONDS)
        except (RuntimeError, OSError, ValueError) as e:
            logger.debug("[enrichment] Jira fetch failed for %s: %s", ticket, e)
            continue
        title = (title or "").strip()
        lines.append(f"- {ticket}: {title}".rstrip())
        excerpt = _excerpt(body)
        if excerpt:
            lines.append(f"  > {excerpt}")
    return _format_block(lines)


def fetch_github_issues(refs: List[Tuple[str, str, int]]) -> str:
    """Fetch and format GitHub issue summaries via ``gh``.

    Returns ``""`` on any failure (``gh`` missing, non-zero exit, bad JSON).
    """
    if not refs:
        return ""
    if len(refs) > MAX_REFS:
        logger.info(
            "[enrichment] capping GitHub fetch from %d to %d references",
            len(refs), MAX_REFS,
        )
        refs = refs[:MAX_REFS]
    import json

    from app.github import run_gh

    lines: List[str] = []
    for owner, repo, number in refs:
        slug = f"{owner}/{repo}#{number}"
        try:
            stdout = run_gh(
                "issue", "view", str(number),
                "--repo", f"{owner}/{repo}",
                "--json", "title,body",
                timeout=GH_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            logger.warning("[enrichment] gh CLI unavailable; skipping GitHub issue enrichment")
            return ""
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            logger.debug("[enrichment] gh fetch failed for %s: %s", slug, e)
            continue
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            continue
        title = (data.get("title") or "").strip()
        lines.append(f"- {slug}: {title}".rstrip())
        excerpt = _excerpt(data.get("body") or "")
        if excerpt:
            lines.append(f"  > {excerpt}")
    return _format_block(lines)


def fetch_issue_context(
    pr_body: str,
    project_name: str = "",
    project_path: str = "",
) -> str:
    """Build the ``{ISSUE_CONTEXT}`` block for a PR review.

    Resolves the project's configured tracker provider, parses matching
    references out of ``pr_body``, fetches them, and returns a formatted block
    (or ``""`` when nothing is configured/found). Never raises.
    """
    if not pr_body:
        return ""
    try:
        from app.issue_tracker.config import get_tracker_for_project

        tracker = get_tracker_for_project(project_name)
        provider = (tracker or {}).get("provider", "")
        block = ""
        if provider == "jira":
            # Only enrich when a Jira project is actually mapped — otherwise the
            # default-github fallback would mis-route and ALLCAPS branch tokens
            # would hammer Jira pointlessly.
            if not tracker.get("jira_project"):
                return ""
            block = fetch_jira_issues(parse_jira_ticket_ids(pr_body))
        elif provider == "github":
            block = fetch_github_issues(parse_github_issue_refs(pr_body))
        return _fence(block)
    except Exception as e:  # never let enrichment abort a review
        logger.warning("[enrichment] issue context fetch failed: %s", e)
    return ""


def _fence(block: str) -> str:
    """Wrap the fetched tracker block as untrusted external data.

    The titles/excerpts come from issues that may live in repos not under
    review (GitHub cross-repo refs) or from arbitrary Jira tickets named by the
    PR author, so the content is third-party text. Fencing it (with injection
    scanning) keeps the reviewer agent from treating an embedded prompt-injection
    payload with the same trust as the diff. Empty input passes through as ``""``
    so the ``{ISSUE_CONTEXT}`` placeholder stays blank when nothing was fetched.
    """
    if not block:
        return ""
    from app.prompt_guard import fence_external_data

    return "\n" + fence_external_data(block.strip(), "tracker issue context") + "\n"
