"""Google Chat notifications — webhooks, Cards v2, threading, queue.

Sends notifications to a Google Chat space via incoming webhook URL.
Supports threading, grouping, rate limiting, and retry queue.
"""

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from app.utils import atomic_write
from app.watcher.helpers import get_watcher_config, save_yaml

logger = logging.getLogger("watcher.notifier")

MAX_SENT_HISTORY = 500

_webhook_url_cache: tuple[str | None, float] = (None, 0.0)
_URL_CACHE_TTL = 300  # 5 minutes


# ── Sending ──────────────────────────────────────────────────────────

def _get_webhook_url() -> str | None:
    """Load the Google Chat webhook URL from env var or GSM (cached 5 min)."""
    global _webhook_url_cache
    cached_value, cached_at = _webhook_url_cache
    if cached_value and (time.time() - cached_at) < _URL_CACHE_TTL:
        return cached_value

    env_url = os.environ.get("GCHAT_WEBHOOK_URL")
    if env_url:
        _webhook_url_cache = (env_url, time.time())
        return env_url

    try:
        from app.credential_vault.helpers import get_gsm
        config = get_watcher_config()
        secret_name = config.get("notifications", {}).get(
            "google_chat_webhook_gsm", ""
        )
        if not secret_name:
            return None
        gsm = get_gsm()
        value = gsm.access_secret(secret_name)
        _webhook_url_cache = (value, time.time())
        return value
    except (ImportError, ValueError, OSError) as e:
        logger.error("Failed to load Google Chat webhook URL: %s", e)
        return None


def send_notification(text: str, thread_key: str | None = None,
                      cards: list | None = None) -> bool:
    """Send a message to Google Chat via webhook.

    Supports text, Cards v2, and threading.
    Returns True on success, False on failure.
    """
    url = _get_webhook_url()
    if not url:
        logger.warning("No Google Chat webhook URL configured")
        return False

    payload = {}
    if text:
        payload["text"] = text
    if cards:
        payload["cardsV2"] = cards
    if thread_key:
        payload["thread"] = {"threadKey": thread_key}
        url += "&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"

    # Use circuit breaker for Google Chat calls
    try:
        from app.circuit_breakers import get_breaker
        chat_breaker = get_breaker("google_chat")
    except ImportError:
        chat_breaker = None

    for attempt in range(3):
        try:
            if chat_breaker:
                resp = chat_breaker.call(requests.post, url, json=payload, timeout=10)
            else:
                resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2))
                logger.warning("Rate limited, retrying in %ds", retry_after)
                time.sleep(retry_after)
                continue
            elif 400 <= resp.status_code < 500:
                logger.error(
                    "Client error sending notification: %d %s",
                    resp.status_code, resp.text[:200],
                )
                return False
            else:
                logger.warning(
                    "Server error %d, retrying...", resp.status_code
                )
                time.sleep(2 ** attempt)
                continue
        except requests.RequestException as e:
            logger.error("Network error sending notification: %s", e)
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return False
        except Exception as e:
            # Circuit breaker open — use queue (caller handles retry)
            try:
                from pybreaker import CircuitBreakerError
                if isinstance(e, CircuitBreakerError):
                    logger.warning("Google Chat circuit breaker open, notification queued")
                    return False
            except ImportError:
                pass
            raise

    return False


# ── Card templates ───────────────────────────────────────────────────

def build_credential_alert_card(event) -> list:
    return [{
        "cardId": "credential-alert",
        "card": {
            "header": {
                "title": "Credential détectée",
                "subtitle": f"{event.repo} — {event.author} ({event.author_type})",
            },
            "sections": [{
                "widgets": [
                    {"decoratedText": {"topLabel": "Repo", "text": event.repo or "?"}},
                    {"decoratedText": {"topLabel": "Auteur", "text": f"{event.author} ({event.author_type})"}},
                    {"decoratedText": {"topLabel": "Résumé", "text": event.summary[:200]}},
                ],
            }],
        },
    }]


def build_unknown_author_card(event) -> list:
    return [{
        "cardId": "unknown-author",
        "card": {
            "header": {
                "title": "Auteur inconnu",
                "subtitle": f"{event.author} sur {event.repo}",
            },
            "sections": [{
                "widgets": [
                    {"decoratedText": {"topLabel": "Login", "text": event.author}},
                    {"decoratedText": {"topLabel": "Plateforme", "text": event.platform}},
                    {"decoratedText": {"topLabel": "Repo", "text": event.repo or "?"}},
                    {"textParagraph": {
                        "text": f"Ajouter au registre : /governor.watcher register {event.author} {event.platform} &lt;type&gt;",
                    }},
                ],
            }],
        },
    }]


def build_new_repo_card(event) -> list:
    return [{
        "cardId": "new-repo",
        "card": {
            "header": {
                "title": "Nouveau repo créé",
                "subtitle": f"{event.repo} par {event.author}",
            },
            "sections": [{
                "widgets": [
                    {"decoratedText": {"topLabel": "Repo", "text": event.repo or "?"}},
                    {"decoratedText": {"topLabel": "Créateur", "text": event.author}},
                    {"decoratedText": {"topLabel": "Plateforme", "text": event.platform}},
                ],
            }],
        },
    }]


def build_push_summary_card(events: list) -> list:
    if not events:
        return []

    first = events[0]
    repo = first.get("repo", "?")
    author = first.get("author", "?")
    count = len(events)
    summaries = "\n".join(
        f"• {e.get('summary', '')[:60]}" for e in events[:5]
    )
    if count > 5:
        summaries += f"\n... et {count - 5} autres"

    return [{
        "cardId": "push-summary",
        "card": {
            "header": {
                "title": f"{count} push par {author}",
                "subtitle": repo,
            },
            "sections": [{
                "widgets": [
                    {"textParagraph": {"text": summaries}},
                ],
            }],
        },
    }]


# ── Threading & grouping ─────────────────────────────────────────────

def get_thread_key(event) -> str:
    """Generate a thread key: {type}-{repo}-{date}."""
    repo = getattr(event, "repo", None) or "unknown"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    event_type = getattr(event, "type", "event")
    return f"{event_type}-{repo}-{date}"


def group_events(events: list[dict], window_minutes: int = 5) -> list[list[dict]]:
    """Group consecutive push events from the same author/repo within a time window."""
    if not events:
        return []

    groups = []
    current_group = [events[0]]

    for event in events[1:]:
        prev = current_group[-1]
        same_author = event.get("author") == prev.get("author")
        same_repo = event.get("repo") == prev.get("repo")
        same_type = event.get("type") == prev.get("type") == "push"

        within_window = True
        if same_author and same_repo and same_type and window_minutes > 0:
            try:
                prev_ts = prev.get("timestamp", "")
                curr_ts = event.get("timestamp", "")
                if prev_ts and curr_ts:
                    prev_dt = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                    curr_dt = datetime.fromisoformat(curr_ts.replace("Z", "+00:00"))
                    delta_minutes = abs((curr_dt - prev_dt).total_seconds()) / 60
                    within_window = delta_minutes <= window_minutes
            except (ValueError, TypeError):
                pass

        if same_author and same_repo and same_type and within_window:
            current_group.append(event)
        else:
            groups.append(current_group)
            current_group = [event]

    groups.append(current_group)
    return groups


# ── Notification queue ───────────────────────────────────────────────

def _queue_path(instance_dir: Path) -> Path:
    return instance_dir / "watcher" / "notification_queue.yaml"


def _load_queue(instance_dir: Path) -> dict:
    path = _queue_path(instance_dir)
    if not path.exists():
        return {"pending": [], "sent": []}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {"pending": [], "sent": []}
    except (yaml.YAMLError, OSError):
        return {"pending": [], "sent": []}


def _save_queue(instance_dir: Path, data: dict) -> None:
    # Bound the sent history
    sent = data.get("sent", [])
    if len(sent) > MAX_SENT_HISTORY:
        data["sent"] = sent[-MAX_SENT_HISTORY:]
    save_yaml(_queue_path(instance_dir), data)


def queue_notification(instance_dir: Path, notif_type: str, event,
                       text: str | None = None, cards: list | None = None) -> None:
    """Add a notification to the queue.

    Checks the notification router to see if this author's group is active.
    If not, the notification is logged but not queued.
    """
    # Check notification router — should we notify for this author?
    try:
        from app.notification_router import get_router
        router = get_router()
        author_login = getattr(event, "author", "")
        if author_login and not router.should_notify(notif_type, author_login):
            # Governors always get alert events
            alert_events = {"credential_detected", "unknown_author", "force_push", "new_repo"}
            if notif_type not in alert_events:
                logger.debug("Notification skipped for %s (not in active rollout group)", author_login)
                return
    except ImportError:
        pass

    queue = _load_queue(instance_dir)

    thread_key = get_thread_key(event)
    event_id = getattr(event, "id", "?")

    if not text and not cards:
        if notif_type == "credential_detected":
            cards = build_credential_alert_card(event)
            text = f"Credential potentielle détectée dans *{event.repo}*"
        elif notif_type == "unknown_author":
            cards = build_unknown_author_card(event)
            text = f"Auteur inconnu *{event.author}* sur _{event.repo}_"
        elif notif_type == "new_repo":
            cards = build_new_repo_card(event)
            text = f"Nouveau repo créé : *{event.repo}* par _{event.author}_"
        elif notif_type == "force_push":
            text = (
                f"Force-push détecté sur *{event.repo}* branche `{event.branch}` "
                f"par _{event.author}_ ({event.author_type})"
            )

    notif_id = f"notif-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{event_id[-8:]}"

    queue.setdefault("pending", []).append({
        "id": notif_id,
        "type": notif_type,
        "thread_key": thread_key,
        "payload": {
            "text": text,
            "cards": cards,
        },
        "events": [event_id],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "retries": 0,
    })

    _save_queue(instance_dir, queue)


def process_queue(instance_dir: Path) -> dict:
    """Process pending notifications with rate limiting (1 msg/sec, max 3 retries)."""
    queue = _load_queue(instance_dir)
    pending = queue.get("pending", [])
    sent = queue.setdefault("sent", [])
    summary = {"sent": 0, "failed": 0, "remaining": 0}

    still_pending = []
    for notif in pending:
        payload = notif.get("payload", {})
        text = payload.get("text")
        cards = payload.get("cards")
        thread_key = notif.get("thread_key")

        success = send_notification(
            text=text, thread_key=thread_key, cards=cards
        )

        if success:
            notif["sent_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            sent.append(notif)
            summary["sent"] += 1
        else:
            notif["retries"] = notif.get("retries", 0) + 1
            if notif["retries"] >= 3:
                notif["status"] = "dropped"
                sent.append(notif)
                summary["failed"] += 1
            else:
                still_pending.append(notif)
                summary["remaining"] += 1

        time.sleep(1)

    queue["pending"] = still_pending
    _save_queue(instance_dir, queue)

    return summary
