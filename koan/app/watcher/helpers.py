"""Shared helpers for watcher module and skills."""

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.utils import load_config, KOAN_ROOT, INSTANCE_DIR, append_to_outbox, atomic_write

logger = logging.getLogger("watcher.helpers")


def save_yaml(path: Path, data: dict) -> None:
    """Save YAML data atomically to prevent corruption."""
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    atomic_write(path, content)


def get_watcher_config() -> dict:
    """Load the watcher section from config.yaml."""
    return load_config().get("watcher", {})


def load_user_registry() -> dict:
    """Load user_registry.yaml from instance/watcher/."""
    path = INSTANCE_DIR / "watcher" / "user_registry.yaml"
    if not path.exists():
        return {"users": [], "aliases": {}}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return {
            "users": data.get("users", []),
            "aliases": data.get("aliases", {}),
        }
    except (yaml.YAMLError, OSError) as e:
        logger.error("Error loading user registry: %s", e)
        return {"users": [], "aliases": {}}


def resolve_author_login(identifier: str, platform: str) -> str | None:
    """Resolve an email or name to a login via aliases."""
    registry = load_user_registry()
    aliases = registry.get("aliases", {})

    if identifier in aliases:
        return aliases[identifier]

    for user in registry.get("users", []):
        if user.get("login") == identifier and user.get("platform") == platform:
            return identifier
        if user.get("email") == identifier:
            return user.get("login")

    return None


def classify_author(login: str, platform: str) -> tuple[str, str | None]:
    """Classify an author as citizen/tech/governor/unknown.

    Returns (author_type, author_name).
    First tries direct login match, then alias resolution.
    """
    registry = load_user_registry()
    aliases = registry.get("aliases", {})

    resolved_login = aliases.get(login, login)

    for user in registry.get("users", []):
        if user.get("login") == resolved_login and user.get("platform") == platform:
            return user.get("type", "unknown"), user.get("name")

    for user in registry.get("users", []):
        if user.get("login") == resolved_login:
            return user.get("type", "unknown"), user.get("name")

    return "unknown", None


def notify(message: str) -> None:
    """Write a message to outbox.md."""
    outbox = INSTANCE_DIR / "outbox.md"
    append_to_outbox(outbox, f"\n{message}\n")


def load_repos() -> list[dict]:
    """Load repos.yaml."""
    path = INSTANCE_DIR / "watcher" / "repos.yaml"
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("repos", [])
    except (yaml.YAMLError, OSError) as e:
        logger.error("Error loading repos: %s", e)
        return []


def save_repos(repos: list[dict]) -> None:
    """Save repos.yaml atomically."""
    save_yaml(INSTANCE_DIR / "watcher" / "repos.yaml", {"repos": repos})


def update_repo_in_list(repos: list[dict], repo_name: str, platform: str,
                        author: str, timestamp: str) -> None:
    """Update a repo entry in an already-loaded repos list (in-memory mutation).

    Use this in batch operations to avoid N+1 load/save cycles.
    """
    for repo in repos:
        if repo.get("name") == repo_name and repo.get("platform") == platform:
            repo["last_activity"] = timestamp
            if author and author not in repo.get("contributors", []):
                repo.setdefault("contributors", []).append(author)
            repo["status"] = "active"
            return

    repos.append({
        "name": repo_name,
        "platform": platform,
        "url": _build_repo_url(repo_name, platform),
        "status": "active",
        "language": None,
        "last_activity": timestamp,
        "contributors": [author] if author else [],
        "webhook_active": platform == "github",
    })


def update_repo_activity(repo_name: str, platform: str, author: str, timestamp: str) -> None:
    """Update repos.yaml with latest activity for a repo.

    Creates the entry if the repo is new. For single-event updates (webhook handler).
    For batch operations (scanner), use update_repo_in_list + save_repos instead.
    """
    repos = load_repos()
    update_repo_in_list(repos, repo_name, platform, author, timestamp)
    save_repos(repos)


def _build_repo_url(repo_name: str, platform: str) -> str:
    config = get_watcher_config()
    if platform == "github":
        org = config.get("github", {}).get("org", "YourArtOfficial")
        return f"https://github.com/{org}/{repo_name}"
    elif platform == "gitlab":
        group = config.get("gitlab", {}).get("group", "yourart")
        return f"https://gitlab.com/{group}/{repo_name}"
    return ""


def get_repos_summary(platform: str | None = None, status: str | None = None) -> list[dict]:
    """Get filtered repos list."""
    repos = load_repos()
    if platform:
        repos = [r for r in repos if r.get("platform") == platform]
    if status:
        repos = [r for r in repos if r.get("status") == status]

    _detect_dormant_repos(repos)
    return repos


def _detect_dormant_repos(repos: list[dict]) -> None:
    """Mark repos as dormant if no activity for >30 days."""
    now = datetime.now(timezone.utc)
    for repo in repos:
        if repo.get("status") == "archived":
            continue
        last = repo.get("last_activity")
        if not last:
            continue
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if (now - last_dt).days > 30:
                repo["status"] = "dormant"
            elif repo.get("status") == "dormant":
                repo["status"] = "active"
        except (ValueError, TypeError):
            pass


def save_user_registry(registry: dict) -> None:
    """Save user_registry.yaml atomically."""
    save_yaml(INSTANCE_DIR / "watcher" / "user_registry.yaml", registry)


def add_user_to_registry(login: str, platform: str, user_type: str,
                         name: str | None = None) -> None:
    """Add a user to the registry."""
    registry = load_user_registry()
    for user in registry.get("users", []):
        if user.get("login") == login and user.get("platform") == platform:
            user["type"] = user_type
            if name:
                user["name"] = name
            save_user_registry(registry)
            return

    registry.setdefault("users", []).append({
        "login": login,
        "platform": platform,
        "name": name,
        "type": user_type,
    })
    save_user_registry(registry)


def handle_unknown_author(login: str, platform: str, repo: str, event) -> None:
    """Log alert for unknown author (notification via queue only)."""
    logger.warning(
        "Unknown author %s (%s) on repo %s", login, platform, repo
    )


def check_and_notify(instance_dir: Path, event) -> None:
    """Check if an event needs a notification and route to notifier queue.

    Detects: credential patterns, unknown authors, force-push, new repos.
    """
    notifications = []

    if event.author_type == "unknown":
        handle_unknown_author(event.author, event.platform, event.repo or "?", event)
        notifications.append(("unknown_author", event))

    if event.forced:
        notifications.append(("force_push", event))

    if event.type == "repo" and "created" in event.summary.lower():
        notifications.append(("new_repo", event))

    if _contains_credential_pattern(event.summary):
        notifications.append(("credential_detected", event))

    if notifications:
        try:
            from app.watcher.notifier import queue_notification
            for notif_type, evt in notifications:
                queue_notification(instance_dir, notif_type, evt)
        except ImportError:
            pass

    # Load config once for both hooks
    try:
        config = load_config()
    except Exception:
        config = {}

    # Advisor hook: analyze citizen commits for duplications
    if event.author_type == "citizen" and event.type == "push":
        try:
            advisor_config = config.get("advisor", {})
            if advisor_config.get("enabled") and advisor_config.get("scan_on_event"):
                from app.advisor.analyzer import analyze_commit
                event_dict = {
                    "id": getattr(event, "id", ""),
                    "type": event.type,
                    "repo": event.repo,
                    "platform": event.platform,
                    "author": event.author,
                    "author_name": event.author_name,
                    "author_type": event.author_type,
                    "summary": event.summary,
                    "title": getattr(event, "title", event.summary[:200]),
                    "diff": getattr(event, "diff", event.summary),
                    "timestamp": event.timestamp,
                }
                analyze_commit(event_dict, advisor_config)
        except ImportError:
            pass
        except Exception as e:
            _cb_error = False
            try:
                from pybreaker import CircuitBreakerError
                _cb_error = isinstance(e, CircuitBreakerError)
            except ImportError:
                pass
            if _cb_error:
                logger.warning("Advisor analysis skipped (circuit breaker open): %s", e)
            else:
                logger.warning("Advisor analysis failed for event: %s", e)

    # Smart notification hook (spec 009)
    try:
        smart_config = config.get("smart_notifications", {})
        if smart_config.get("enabled"):
            from app.commit_analyzer import analyze_and_notify
            analyze_and_notify(event, smart_config)
    except ImportError:
        pass
    except Exception as e:
        logger.warning("Smart notification error: %s", e)


_CREDENTIAL_PATTERNS = [p.lower() for p in [
    "AKIA",           # AWS Access Key
    "ASIA",           # AWS STS
    "sk-",            # OpenAI / Stripe
    "ghp_",           # GitHub PAT
    "gho_",           # GitHub OAuth
    "ghs_",           # GitHub App
    "glpat-",         # GitLab PAT
    "xoxb-",          # Slack Bot
    "xoxp-",          # Slack User
    "password",
    "secret",
    "private_key",
    "-----BEGIN RSA",
    "-----BEGIN OPENSSH",
]]


def _contains_credential_pattern(text: str) -> bool:
    """Check if text contains known credential patterns."""
    text_lower = text.lower()
    return any(p in text_lower for p in _CREDENTIAL_PATTERNS)
