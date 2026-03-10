"""Event normalizer — unified format for GitHub and GitLab events.

Converts platform-specific payloads into WatcherEvent dataclass instances.
"""

import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger("watcher.normalizer")


@dataclass
class WatcherEvent:
    id: str
    platform: str              # "github" | "gitlab"
    type: str                  # "push" | "pr" | "issue" | "create" | "member" | "repo" | "org"
    repo: str
    author: str
    author_type: str           # "citizen" | "tech" | "governor" | "unknown"
    author_name: str | None
    branch: str | None
    summary: str
    commits_count: int | None = None
    forced: bool = False
    timestamp: str = ""
    delivery_id: str | None = None
    raw_event_type: str = ""
    commits: list | None = None  # [{sha, message, timestamp, author_name, author_email}]

    def to_dict(self) -> dict:
        return asdict(self)


def generate_event_id() -> str:
    """Generate a unique event ID: evt-YYYYMMDD-<uuid4_short>."""
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"evt-{date_str}-{uuid.uuid4().hex[:8]}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_branch(ref: str) -> str | None:
    """Extract branch name from refs/heads/... format."""
    if ref and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    if ref and ref.startswith("refs/tags/"):
        return ref[len("refs/tags/"):]
    return ref


# ── Shared helpers ───────────────────────────────────────────────────

def _classify_github_sender(payload: dict, classify_fn) -> tuple[str, str, str | None]:
    """Extract and classify GitHub event sender. Returns (login, type, name)."""
    login = payload.get("sender", {}).get("login", "unknown")
    author_type, author_name = ("unknown", None)
    if classify_fn:
        author_type, author_name = classify_fn(login, "github")
    return login, author_type, author_name


def _build_github_event(payload: dict, delivery_id: str | None, classify_fn,
                        *, type: str, summary: str, raw_event_type: str,
                        repo: str | None = None, branch: str | None = None,
                        commits_count: int | None = None,
                        forced: bool = False) -> WatcherEvent:
    """Build a WatcherEvent from a GitHub payload with common fields."""
    author, author_type, author_name = _classify_github_sender(payload, classify_fn)
    return WatcherEvent(
        id=generate_event_id(),
        platform="github",
        type=type,
        repo=repo or payload.get("repository", {}).get("name", "unknown"),
        author=author,
        author_type=author_type,
        author_name=author_name,
        branch=branch,
        summary=summary,
        commits_count=commits_count,
        forced=forced,
        timestamp=_utcnow_iso(),
        delivery_id=delivery_id,
        raw_event_type=raw_event_type,
    )


# ── GitHub normalizers ───────────────────────────────────────────────

def normalize_github_push(payload: dict, delivery_id: str | None = None,
                          classify_fn=None) -> WatcherEvent:
    commits = payload.get("commits", [])
    head_commit = payload.get("head_commit", {})
    message = (head_commit.get("message", "") or "")[:80]

    commits_detail = [
        {
            "sha": c.get("id", ""),
            "message": (c.get("message", "") or "")[:200],
            "timestamp": c.get("timestamp", ""),
            "author_name": c.get("author", {}).get("name", ""),
            "author_email": c.get("author", {}).get("email", ""),
            "added": len(c.get("added", [])),
            "modified": len(c.get("modified", [])),
            "removed": len(c.get("removed", [])),
        }
        for c in commits
    ] or None

    event = _build_github_event(
        payload, delivery_id, classify_fn,
        type="push",
        branch=_extract_branch(payload.get("ref", "")),
        summary=f"{len(commits)} commits: {message}",
        commits_count=len(commits),
        forced=payload.get("forced", False),
        raw_event_type="push",
    )
    event.commits = commits_detail
    return event


def normalize_github_pr(payload: dict, delivery_id: str | None = None,
                        classify_fn=None) -> WatcherEvent:
    pr = payload.get("pull_request", {})
    action = payload.get("action", "unknown")
    number = pr.get("number", "?")
    title = pr.get("title", "")[:80]

    if action == "closed" and pr.get("merged"):
        action = "merged"

    head_ref = pr.get("head", {}).get("ref", "")
    base_ref = pr.get("base", {}).get("ref", "")
    branch = f"{head_ref} → {base_ref}" if head_ref and base_ref else head_ref

    return _build_github_event(
        payload, delivery_id, classify_fn,
        type="pr",
        branch=branch,
        summary=f"PR #{number} {action}: {title}",
        raw_event_type="pull_request",
    )


def normalize_github_issue(payload: dict, delivery_id: str | None = None,
                           classify_fn=None) -> WatcherEvent:
    issue = payload.get("issue", {})
    action = payload.get("action", "unknown")
    number = issue.get("number", "?")
    title = issue.get("title", "")[:80]

    return _build_github_event(
        payload, delivery_id, classify_fn,
        type="issue",
        summary=f"Issue #{number} {action}: {title}",
        raw_event_type="issues",
    )


def normalize_github_create(payload: dict, delivery_id: str | None = None,
                            classify_fn=None) -> WatcherEvent:
    ref = payload.get("ref", "")
    ref_type = payload.get("ref_type", "branch")

    return _build_github_event(
        payload, delivery_id, classify_fn,
        type="create",
        branch=ref,
        summary=f"{ref_type} created: {ref}",
        raw_event_type="create",
    )


def normalize_github_member(payload: dict, delivery_id: str | None = None,
                            classify_fn=None) -> WatcherEvent:
    action = payload.get("action", "unknown")
    member_login = payload.get("member", {}).get("login", "unknown")

    return _build_github_event(
        payload, delivery_id, classify_fn,
        type="member",
        summary=f"Member {action}: {member_login}",
        raw_event_type="member",
    )


def normalize_github_repo(payload: dict, delivery_id: str | None = None,
                          classify_fn=None) -> WatcherEvent:
    action = payload.get("action", "unknown")
    repo_name = payload.get("repository", {}).get("name", "unknown")

    return _build_github_event(
        payload, delivery_id, classify_fn,
        type="repo",
        repo=repo_name,
        summary=f"Repository {action}: {repo_name}",
        raw_event_type="repository",
    )


def normalize_github_org(payload: dict, delivery_id: str | None = None,
                         classify_fn=None) -> WatcherEvent:
    action = payload.get("action", "unknown")
    membership = payload.get("membership", {})
    user_login = membership.get("user", {}).get("login", "unknown")

    return _build_github_event(
        payload, delivery_id, classify_fn,
        type="org",
        repo=None,
        summary=f"Org {action}: {user_login}",
        raw_event_type="organization",
    )


# ── GitLab normalizers ───────────────────────────────────────────────

def normalize_gitlab_commit(commit: dict, repo_name: str, branch: str | None = None,
                            classify_fn=None) -> WatcherEvent:
    author_email = commit.get("author_email", "")
    author_name_raw = commit.get("author_name", "unknown")

    author_type, author_name = ("unknown", None)
    if classify_fn:
        author_type, author_name = classify_fn(author_email, "gitlab")
        if author_type == "unknown":
            author_type, author_name = classify_fn(author_name_raw, "gitlab")

    title = (commit.get("title", "") or commit.get("message", ""))[:80]
    timestamp = commit.get("created_at") or _utcnow_iso()

    resolved_author = author_email
    if classify_fn:
        from app.watcher.helpers import resolve_author_login
        resolved_author = resolve_author_login(author_email, "gitlab") or author_email

    commits_detail = [{
        "sha": commit.get("id", commit.get("short_id", "")),
        "message": title,
        "timestamp": timestamp,
        "author_name": author_name_raw,
        "author_email": author_email,
    }]

    return WatcherEvent(
        id=generate_event_id(),
        platform="gitlab",
        type="push",
        repo=repo_name,
        author=resolved_author,
        author_type=author_type,
        author_name=author_name,
        branch=branch,
        summary=f"1 commit: {title}",
        commits_count=1,
        forced=False,
        timestamp=timestamp,
        delivery_id=None,
        raw_event_type="push",
        commits=commits_detail,
    )


def normalize_gitlab_mr(mr: dict, classify_fn=None) -> WatcherEvent:
    author_username = mr.get("author", {}).get("username", "unknown")
    author_type, author_name = ("unknown", None)
    if classify_fn:
        author_type, author_name = classify_fn(author_username, "gitlab")

    iid = mr.get("iid", "?")
    state = mr.get("state", "unknown")
    title = (mr.get("title", "") or "")[:80]
    source = mr.get("source_branch", "")
    target = mr.get("target_branch", "")
    branch = f"{source} → {target}" if source and target else source

    web_url = mr.get("web_url", "")
    repo_name = _extract_repo_from_gitlab_url(web_url)
    timestamp = mr.get("updated_at") or mr.get("created_at") or _utcnow_iso()

    return WatcherEvent(
        id=generate_event_id(),
        platform="gitlab",
        type="pr",
        repo=repo_name,
        author=author_username,
        author_type=author_type,
        author_name=author_name,
        branch=branch,
        summary=f"MR !{iid} {state}: {title}",
        timestamp=timestamp,
        raw_event_type="merge_request",
    )


def normalize_gitlab_issue(issue: dict, classify_fn=None) -> WatcherEvent:
    author_username = issue.get("author", {}).get("username", "unknown")
    author_type, author_name = ("unknown", None)
    if classify_fn:
        author_type, author_name = classify_fn(author_username, "gitlab")

    iid = issue.get("iid", "?")
    state = issue.get("state", "unknown")
    title = (issue.get("title", "") or "")[:80]

    web_url = issue.get("web_url", "")
    repo_name = _extract_repo_from_gitlab_url(web_url)
    timestamp = issue.get("updated_at") or issue.get("created_at") or _utcnow_iso()

    return WatcherEvent(
        id=generate_event_id(),
        platform="gitlab",
        type="issue",
        repo=repo_name,
        author=author_username,
        author_type=author_type,
        author_name=author_name,
        branch=None,
        summary=f"Issue #{iid} {state}: {title}",
        timestamp=timestamp,
        raw_event_type="issue",
    )


def _extract_repo_from_gitlab_url(web_url: str) -> str:
    """Extract project name from GitLab web_url like https://gitlab.com/yourart/project/-/..."""
    if not web_url:
        return "unknown"
    parts = web_url.split("/")
    for i, part in enumerate(parts):
        if part == "-" and i > 0:
            return parts[i - 1]
    if len(parts) >= 5:
        return parts[4]
    return "unknown"
