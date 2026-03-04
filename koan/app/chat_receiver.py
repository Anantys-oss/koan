"""Google Chat Pub/Sub subscriber — receive messages, dispatch commands, respond.

Connects to Google Cloud Pub/Sub to receive Google Chat events (messages,
slash commands, button clicks, lifecycle). Dispatches to chat_dispatcher
and sends responses via the Chat API.

Usage:
    python -m app.chat_receiver [--debug]
    (requires KOAN_ROOT env var set, run from koan/ directory)
"""

import argparse
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable

import os
import platform

from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config

logger = logging.getLogger("chat_receiver")


# ── ChatEvent dataclass ──────────────────────────────────────────────

@dataclass
class ChatEvent:
    """Raw event received from Google Chat via Pub/Sub."""
    event_type: str
    space_name: str
    space_type: str
    thread_name: str
    message_name: str
    sender_email: str
    sender_display_name: str
    text: str
    argument_text: str
    slash_command_id: Optional[int] = None
    action_name: str = ""
    action_params: dict = field(default_factory=dict)
    timestamp: str = ""
    raw: dict = field(default_factory=dict)


# ── Parsing ──────────────────────────────────────────────────────────

def parse_pubsub_message(data: bytes) -> ChatEvent:
    """Parse a Pub/Sub message payload into a ChatEvent.

    Raises ValueError if the payload is invalid.
    """
    try:
        raw = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Invalid JSON in Pub/Sub message: {e}")

    event_type = raw.get("type", "")
    if event_type not in ("MESSAGE", "CARD_CLICKED", "ADDED_TO_SPACE", "REMOVED_FROM_SPACE"):
        raise ValueError(f"Unknown event type: {event_type}")

    space = raw.get("space", {})
    space_name = space.get("name", "")
    space_type = "DM" if space.get("type") == "DM" else "ROOM"

    message = raw.get("message", {})
    message_name = message.get("name", "")
    thread = message.get("thread", {})
    thread_name = thread.get("name", "")

    user = raw.get("user", {})
    sender = message.get("sender", user)
    sender_email = sender.get("email", "")
    sender_display_name = sender.get("displayName", "")

    text = message.get("text", "")
    argument_text = message.get("argumentText", "").strip()

    slash_command_id = None
    slash_cmd = message.get("slashCommand")
    if slash_cmd:
        try:
            slash_command_id = int(slash_cmd.get("commandId", 0))
        except (ValueError, TypeError):
            pass

    action_name = ""
    action_params = {}
    if event_type == "CARD_CLICKED":
        action = raw.get("action", {})
        action_name = action.get("actionMethodName", "") or action.get("function", "")
        action_params = {
            p["key"]: p["value"]
            for p in action.get("parameters", [])
            if "key" in p and "value" in p
        }

    timestamp = raw.get("eventTime", datetime.now(timezone.utc).isoformat())

    return ChatEvent(
        event_type=event_type,
        space_name=space_name,
        space_type=space_type,
        thread_name=thread_name,
        message_name=message_name,
        sender_email=sender_email,
        sender_display_name=sender_display_name,
        text=text,
        argument_text=argument_text,
        slash_command_id=slash_command_id,
        action_name=action_name,
        action_params=action_params,
        timestamp=timestamp,
        raw=raw,
    )


# ── Deduplication ────────────────────────────────────────────────────

_SEEN_MESSAGES: OrderedDict[str, float] = OrderedDict()
_SEEN_LOCK = threading.Lock()
_MAX_SEEN = 10_000


def is_duplicate(message_name: str, ttl: int = 600) -> bool:
    """Check if this message was already processed (at-least-once dedup).

    Thread-safe: Pub/Sub callbacks run in a thread pool.
    Uses OrderedDict for O(k) expiry instead of O(n) full scan.
    """
    if not message_name:
        return False

    now = time.time()

    with _SEEN_LOCK:
        # Purge expired from the front (oldest first)
        while _SEEN_MESSAGES:
            oldest_key, oldest_ts = next(iter(_SEEN_MESSAGES.items()))
            if now - oldest_ts > ttl:
                del _SEEN_MESSAGES[oldest_key]
            else:
                break

        if message_name in _SEEN_MESSAGES:
            return True

        # Hard cap to bound memory
        if len(_SEEN_MESSAGES) >= _MAX_SEEN:
            _SEEN_MESSAGES.popitem(last=False)

        _SEEN_MESSAGES[message_name] = now
        return False


# ── Chat API client ──────────────────────────────────────────────────

def create_chat_client():
    """Create an authenticated Google Chat API client.

    Uses Application Default Credentials (ADC) or service account
    credentials from Google Secret Manager.
    """
    try:
        from google.apps import chat_v1 as google_chat
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.error(
            "google-apps-chat not installed. Run: pip install google-apps-chat google-auth"
        )
        return None

    scopes = ["https://www.googleapis.com/auth/chat.bot"]

    # Try ADC first (set via GOOGLE_APPLICATION_CREDENTIALS env var)
    try:
        import google.auth
        creds, _ = google.auth.default(scopes=scopes)
        return google_chat.ChatServiceClient(credentials=creds)
    except Exception:
        pass

    # Fallback: load from Secret Manager
    config = load_config().get("chat_app", {})
    secret_name = config.get("service_account_secret", "")
    if secret_name:
        try:
            from app.credential_vault.helpers import get_gsm
            gsm = get_gsm()
            sa_json = gsm.access_secret(secret_name)
            import json as _json
            sa_info = _json.loads(sa_json)
            creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
            return google_chat.ChatServiceClient(credentials=creds)
        except Exception as e:
            logger.error("Failed to load Chat SA from Secret Manager: %s", e)

    logger.error("No credentials available for Chat API")
    return None


def send_response(client, response) -> Optional[str]:
    """Send a ChatResponse via the Google Chat API.

    Returns the message name (for updates) or None on failure.
    """
    if client is None:
        logger.warning("Chat client not available, response not sent: %s", response.text[:100])
        return None

    try:
        from google.apps import chat_v1 as google_chat
    except ImportError:
        return None

    message_body = {}
    if response.cards:
        message_body["cards_v2"] = response.cards
    elif response.text:
        message_body["text"] = response.text
    try:
        if response.is_update and response.update_message_name:
            message_body["name"] = response.update_message_name
            request = google_chat.UpdateMessageRequest(
                message=message_body,
                update_mask="text,cards_v2",
            )
            result = client.update_message(request)
            return result.name
        else:
            request = google_chat.CreateMessageRequest(
                parent=response.space_name,
                message=message_body,
            )
            result = client.create_message(request)
            return result.name
    except Exception as e:
        logger.error("Failed to send response to Chat: %s", e)
        return None


# ── Event handler ────────────────────────────────────────────────────

def on_event(event: ChatEvent, chat_client) -> None:
    """Main event handler — dispatch by event type."""
    from app.chat_dispatcher import (
        parse_command, dispatch, handle_card_click, ChatResponse,
    )
    from app.chat_cards import build_command_response_card

    is_dm = event.space_type == "DM"

    if event.event_type == "MESSAGE":
        command = parse_command(event)

        # Check if this is a deferred message (governor was offline)
        try:
            event_time = datetime.fromisoformat(event.timestamp.replace("Z", "+00:00"))
            delay = (datetime.now(timezone.utc) - event_time).total_seconds()
        except (ValueError, TypeError):
            delay = 0

        def _send_fn(resp):
            resp.is_dm = is_dm
            return send_response(chat_client, resp)

        response = dispatch(command, send_fn=_send_fn)
        response.is_dm = is_dm

        # Add deferred notice if response is significantly delayed
        if delay > 60 and not response.is_update:
            mins = int(delay / 60)
            notice = f"[Réponse différée — le governor était hors-ligne ({mins} min)]\n\n"
            response.text = notice + response.text

        send_response(chat_client, response)

    elif event.event_type == "CARD_CLICKED":
        response = handle_card_click(event)
        response.is_dm = is_dm
        send_response(chat_client, response)

    elif event.event_type == "ADDED_TO_SPACE":
        if event.space_type == "DM":
            text = "AI Governor prêt. Tapez help pour la liste des commandes."
        else:
            text = (
                "AI Governor connecté à cet espace.\n\n"
                "Utilisez @AiGovernor <commande> ou les slash commands /status, /scan, etc.\n"
                "Tapez @AiGovernor help pour la liste complète."
            )
        response = ChatResponse(
            space_name=event.space_name,
            thread_name=event.thread_name,
            text=text,
        )
        send_response(chat_client, response)

    elif event.event_type == "REMOVED_FROM_SPACE":
        logger.info("Removed from space %s", event.space_name)


# ── Pub/Sub subscriber ───────────────────────────────────────────────

def start_subscriber(project_id: str, subscription_id: str, chat_client,
                     dedup_ttl: int = 600) -> None:
    """Start the Pub/Sub pull subscriber (blocking).

    Receives messages from Google Chat via the configured Pub/Sub subscription,
    parses them, deduplicates, and dispatches to on_event.
    """
    try:
        from google.cloud import pubsub_v1
    except ImportError:
        logger.error("google-cloud-pubsub not installed. Run: pip install google-cloud-pubsub")
        return

    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(project_id, subscription_id)

    def callback(message):
        try:
            event = parse_pubsub_message(message.data)

            msg_id = event.message_name or message.message_id
            if is_duplicate(msg_id, ttl=dedup_ttl):
                logger.debug("Duplicate message skipped: %s", msg_id)
                message.ack()
                return

            logger.info(
                "Event: type=%s sender=%s space=%s",
                event.event_type, event.sender_email, event.space_name,
            )
            on_event(event, chat_client)

        except ValueError as e:
            logger.warning("Invalid event payload: %s", e)
        except Exception as e:
            logger.error("Error processing event: %s", e, exc_info=True)
        finally:
            message.ack()

    streaming_pull = subscriber.subscribe(subscription_path, callback=callback)
    logger.info("Listening on %s", subscription_path)

    try:
        streaming_pull.result()
    except KeyboardInterrupt:
        logger.info("Shutting down subscriber...")
        streaming_pull.cancel()
        streaming_pull.result()


# ── Startup notification ─────────────────────────────────────────────

def send_startup_notification():
    """Send a startup notification to Google Chat via webhook."""
    webhook_url = os.environ.get("GCHAT_WEBHOOK_URL", "")
    if not webhook_url:
        logger.debug("No GCHAT_WEBHOOK_URL set, skipping startup notification")
        return

    try:
        import requests
    except ImportError:
        logger.debug("requests not available, skipping startup notification")
        return

    env = "Cloud Run" if os.environ.get("INSTANCE_DATA_DIR") else platform.node()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = f"AI Governor started on {env} — {timestamp}"

    try:
        requests.post(webhook_url, json={"text": text}, timeout=5)
        logger.info("Startup notification sent")
    except Exception as e:
        logger.warning("Failed to send startup notification: %s", e)


# ── Entry point ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Governor Google Chat receiver")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    config = load_config().get("chat_app", {})
    if not config.get("enabled", False):
        logger.error("Chat app is disabled in config.yaml (chat_app.enabled: false)")
        return

    project_id = config.get("gcp_project_id", "")
    subscription_id = config.get("pubsub_subscription", "")
    if not project_id or not subscription_id:
        logger.error("Missing chat_app.gcp_project_id or chat_app.pubsub_subscription in config.yaml")
        return

    logger.info("Starting AI Governor Chat receiver...")
    logger.info("Project: %s | Subscription: %s", project_id, subscription_id)

    chat_client = create_chat_client()
    if chat_client is None:
        logger.error("Failed to create Chat API client. Check credentials.")
        return

    send_startup_notification()

    dedup_ttl = config.get("dedup_ttl_seconds", 600)
    start_subscriber(project_id, subscription_id, chat_client, dedup_ttl=dedup_ttl)


if __name__ == "__main__":
    main()
