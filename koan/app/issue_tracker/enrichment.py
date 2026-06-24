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
    from app.jira_notifications import fetch_jira_issue

    lines: List[str] = []
    for ticket in ticket_ids:
        try:
            title, body, _comments = fetch_jira_issue(ticket)
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
    import json

    lines: List[str] = []
    for owner, repo, number in refs:
        slug = f"{owner}/{repo}#{number}"
        try:
            proc = subprocess.run(
                [
                    "gh", "issue", "view", str(number),
                    "--repo", f"{owner}/{repo}",
                    "--json", "title,body",
                ],
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError:
            logger.warning("[enrichment] gh CLI unavailable; skipping GitHub issue enrichment")
            return ""
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.debug("[enrichment] gh fetch failed for %s: %s", slug, e)
            continue
        if proc.returncode != 0:
            logger.debug("[enrichment] gh non-zero for %s: %s", slug, proc.stderr.strip())
            continue
        try:
            data = json.loads(proc.stdout)
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
        if provider == "jira":
            # Only enrich when a Jira project is actually mapped — otherwise the
            # default-github fallback would mis-route and ALLCAPS branch tokens
            # would hammer Jira pointlessly.
            if not tracker.get("jira_project"):
                return ""
            return fetch_jira_issues(parse_jira_ticket_ids(pr_body))
        if provider == "github":
            return fetch_github_issues(parse_github_issue_refs(pr_body))
    except Exception as e:  # never let enrichment abort a review
        logger.warning("[enrichment] issue context fetch failed: %s", e)
    return ""
