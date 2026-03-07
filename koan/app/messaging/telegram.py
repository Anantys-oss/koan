"""Telegram messaging provider.

Encapsulates all Telegram-specific logic: sending messages,
polling updates, chunking, flood protection, and credential validation.
"""

import os
import sys
import threading
import time
from typing import List, Optional

import requests

from app.messaging.base import DEFAULT_MAX_MESSAGE_SIZE, Message, MessagingProvider, Update
from app.messaging import register_provider


FLOOD_WINDOW_SECONDS = 300  # 5 minutes
MAX_MESSAGE_SIZE = DEFAULT_MAX_MESSAGE_SIZE


@register_provider("telegram")
class TelegramProvider(MessagingProvider):
    """Telegram Bot API provider.

    Uses the Bot API for both sending and long-polling.
    Credentials are read from KOAN_TELEGRAM_TOKEN and KOAN_TELEGRAM_CHAT_ID.
    """

    def __init__(self):
        self._bot_token: str = ""
        self._chat_id: str = ""
        self._api_base: str = ""

        # Flood protection state
        self._flood_lock = threading.Lock()
        self._flood_last_message: str = ""
        self._flood_last_sent_at: float = 0.0
        self._flood_warning_sent: bool = False

    # -- MessagingProvider interface ------------------------------------------

    def configure(self) -> bool:
        from app.utils import load_dotenv
        load_dotenv()

        self._bot_token = os.environ.get("KOAN_TELEGRAM_TOKEN", "")
        self._chat_id = os.environ.get("KOAN_TELEGRAM_CHAT_ID", "")

        if not self._bot_token or not self._chat_id:
            print(
                "[telegram] KOAN_TELEGRAM_TOKEN or KOAN_TELEGRAM_CHAT_ID not set.",
                file=sys.stderr,
            )
            return False

        self._api_base = f"https://api.telegram.org/bot{self._bot_token}"
        return True

    def get_provider_name(self) -> str:
        return "telegram"

    def get_channel_id(self) -> str:
        return self._chat_id

    def send_message(self, text: str) -> bool:
        """Send a message with flood protection and chunking.
        
        Empty messages bypass flood protection but are still sent
        (e.g., for clearing chat state in tests).
        
        Returns:
            True if message was sent OR suppressed (both count as success).
            False only on actual send failure.
        """
        if not text:
            return self._send_raw(text)

        now = time.time()
        action = "send"  # "send", "warn", or "suppress"

        with self._flood_lock:
            if (
                text == self._flood_last_message
                and (now - self._flood_last_sent_at) < FLOOD_WINDOW_SECONDS
            ):
                if not self._flood_warning_sent:
                    self._flood_warning_sent = True
                    action = "warn"
                else:
                    action = "suppress"
            else:
                self._flood_last_message = text
                self._flood_last_sent_at = now
                self._flood_warning_sent = False

        if action == "suppress":
            print("[telegram] Flood suppression: duplicate message dropped.",
                  file=sys.stderr)
            return True
        if action == "warn":
            self._send_raw(
                "[flood] Duplicate message detected — suppressing repeats for 5 min."
            )
            return True

        return self._send_raw(text)

    def poll_updates(self, offset: Optional[int] = None) -> List[Update]:
        """Long-poll the Telegram Bot API for new updates."""
        params: dict = {"timeout": 30}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(
                f"{self._api_base}/getUpdates",
                params=params,
                timeout=35,
            )
            data = resp.json()
            raw_updates = data.get("result", [])
        except (requests.RequestException, ValueError) as e:
            # Network error or JSON parse error - return empty to retry next poll
            return []

        updates: List[Update] = []
        for raw in raw_updates:
            msg_data = raw.get("message", {})
            message = None
            if msg_data:
                message = Message(
                    text=msg_data.get("text", ""),
                    role="user",
                    timestamp=str(msg_data.get("date", "")),
                    raw_data=msg_data,
                )
            updates.append(
                Update(
                    update_id=raw.get("update_id", 0),
                    message=message,
                    raw_data=raw,
                )
            )
        return updates

    # -- Internal helpers -----------------------------------------------------

    def _send_raw(self, text: str) -> bool:
        """Send text to the Telegram API (no flood check).
        
        Internal method exposed for notify.py's test-only _send_raw_bypass_flood().
        Normal callers should use send_message() which includes flood protection.
        """
        if not self._bot_token or not self._chat_id:
            print("[telegram] Not configured — cannot send.", file=sys.stderr)
            return False

        ok = True
        for chunk in self.chunk_message(text, max_size=MAX_MESSAGE_SIZE):
            try:
                resp = requests.post(
                    f"{self._api_base}/sendMessage",
                    json={"chat_id": self._chat_id, "text": chunk},
                    timeout=10,
                )
                data = resp.json()
                if not data.get("ok"):
                    print(
                        f"[telegram] API error: {resp.text[:200]}",
                        file=sys.stderr,
                    )
                    ok = False
            except (requests.RequestException, ValueError) as e:
                print(f"[telegram] Send error: {e}", file=sys.stderr)
                ok = False
        return ok

    def send_typing(self) -> bool:
        """Send 'typing...' indicator to the Telegram chat."""
        if not self._bot_token or not self._chat_id:
            return False
        try:
            resp = requests.post(
                f"{self._api_base}/sendChatAction",
                json={"chat_id": self._chat_id, "action": "typing"},
                timeout=5,
            )
            return resp.json().get("ok", False)
        except (requests.RequestException, ValueError):
            return False

    def reset_flood_state(self):
        """Reset flood protection state.
        
        Public method intended for testing and explicit flood state management.
        Called by notify.reset_flood_state() facade and test fixtures.
        """
        with self._flood_lock:
            self._flood_last_message = ""
            self._flood_last_sent_at = 0.0
            self._flood_warning_sent = False
