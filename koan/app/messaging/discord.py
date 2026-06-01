"""Discord messaging provider.

Talks to the Discord REST API. Synchronous polling implementation using
`requests`, mirroring the Matrix provider's style. No WebSocket/Gateway
dependency — suitable for a single-channel bot.

Configuration is read from instance/config.yaml (recommended) under the
``messaging.discord`` section, with environment variables as override
fallback.

config.yaml keys (under ``messaging.discord``):
    bot_token, channel_id

Environment variables (override config.yaml when set):
    KOAN_DISCORD_BOT_TOKEN  — Bot token from the Discord Developer Portal
    KOAN_DISCORD_CHANNEL_ID — Numeric channel (snowflake) ID
"""

import itertools
import os
import sys
import threading
import time
from typing import List, Optional

import requests

from app.messaging.base import Message, MessagingProvider, Update
from app.messaging import register_provider


DISCORD_API_BASE = "https://discord.com/api/v10"

# Discord per-message character limit (2000, half the default 4000).
MAX_MESSAGE_SIZE = 2000


@register_provider("discord")
class DiscordProvider(MessagingProvider):
    """Discord REST API provider.

    Uses cursor-based polling: the last received message's snowflake ID is
    stored as ``_last_message_id`` and passed as the ``after`` query param
    on each poll. The first call fetches only the single most-recent message
    to bootstrap the cursor, then discards it.
    """

    def __init__(self):
        self._bot_token: str = ""
        self._channel_id: str = ""
        self._bot_user_id: str = ""

        self._last_message_id: Optional[str] = None
        self._cursor_initialized: bool = False
        self._backoff_until: float = 0.0
        self._update_counter = itertools.count(1)
        self._send_lock = threading.Lock()

    # -- MessagingProvider interface ------------------------------------------

    def configure(self) -> bool:
        from app.utils import load_config, load_dotenv
        load_dotenv()

        cfg: dict = {}
        try:
            messaging = load_config().get("messaging", {}) or {}
            if isinstance(messaging, dict):
                section = messaging.get("discord", {}) or {}
                if isinstance(section, dict):
                    cfg = section
        except Exception as e:
            print(f"[discord] Failed to load config: {e}", file=sys.stderr)

        self._bot_token = (
            os.environ.get("KOAN_DISCORD_BOT_TOKEN") or cfg.get("bot_token", "")
        ).strip()
        self._channel_id = (
            os.environ.get("KOAN_DISCORD_CHANNEL_ID") or cfg.get("channel_id", "")
        ).strip()

        missing = []
        if not self._bot_token:
            missing.append("bot_token")
        if not self._channel_id:
            missing.append("channel_id")
        if missing:
            print(
                f"[discord] Missing required settings: {', '.join(missing)}. "
                f"Set in instance/config.yaml under messaging.discord or via "
                f"KOAN_DISCORD_BOT_TOKEN / KOAN_DISCORD_CHANNEL_ID env vars.",
                file=sys.stderr,
            )
            return False

        token_hint = self._bot_token[-4:] if len(self._bot_token) >= 4 else "***"
        print(
            f"[discord] Configured (token ...{token_hint}, channel {self._channel_id})",
            file=sys.stderr,
        )

        bot_user_id = self._fetch_bot_user_id()
        if not bot_user_id:
            return False
        self._bot_user_id = bot_user_id
        return True

    def get_provider_name(self) -> str:
        return "discord"

    def get_channel_id(self) -> str:
        return self._channel_id

    def send_message(self, text: str, reply_to_message_id: int = 0) -> bool:
        if not self._bot_token or not self._channel_id:
            print("[discord] Not configured — cannot send.", file=sys.stderr)
            return False

        if not text:
            return True

        ok = True
        for chunk in self.chunk_message(text, max_size=MAX_MESSAGE_SIZE):
            with self._send_lock:
                if not self._send_chunk(chunk):
                    ok = False
        return ok

    def poll_updates(self, offset: Optional[int] = None) -> List[Update]:
        """Fetch new messages from the channel since the last seen snowflake.

        The ``offset`` parameter is unused — Discord uses snowflake IDs stored
        on the instance. The first call bootstraps the cursor by fetching only
        the most-recent message and discarding it (avoids replaying history).
        """
        if not self._bot_token or not self._channel_id:
            return []

        # Respect a prior 429 Retry-After before issuing another request.
        if self._backoff_until and time.time() < self._backoff_until:
            return []

        if not self._cursor_initialized:
            return self._bootstrap_cursor()

        return self._fetch_new_messages()

    # -- Internal helpers -----------------------------------------------------

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bot {self._bot_token}"}

    def _fetch_bot_user_id(self) -> Optional[str]:
        """Fetch the bot's own user ID via GET /users/@me."""
        url = f"{DISCORD_API_BASE}/users/@me"
        try:
            resp = requests.get(url, headers=self._auth_headers(), timeout=10)
            if resp.status_code == 401:
                print("[discord] Invalid bot token (401).", file=sys.stderr)
                return None
            if resp.status_code >= 400:
                print(
                    f"[discord] /users/@me returned {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                )
                return None
            return resp.json().get("id", "")
        except (requests.RequestException, ValueError) as e:
            print(f"[discord] Failed to fetch bot user ID: {e}", file=sys.stderr)
            return None

    def _bootstrap_cursor(self) -> List[Update]:
        """Fetch only the latest message to set the cursor; discard it."""
        url = f"{DISCORD_API_BASE}/channels/{self._channel_id}/messages"
        try:
            resp = requests.get(
                url,
                params={"limit": 1},
                headers=self._auth_headers(),
                timeout=10,
            )
            if resp.status_code >= 400:
                print(
                    f"[discord] Bootstrap poll returned {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                )
                # Leave cursor uninitialized so bootstrap retries next poll;
                # avoids replaying history with no `after` param.
                return []
            messages = resp.json()
            if messages:
                self._last_message_id = messages[0]["id"]
        except (requests.RequestException, ValueError) as e:
            print(f"[discord] Bootstrap poll error: {e}", file=sys.stderr)
            return []

        self._cursor_initialized = True
        return []

    def _fetch_new_messages(self) -> List[Update]:
        """GET /channels/{id}/messages?after={cursor}&limit=100."""
        url = f"{DISCORD_API_BASE}/channels/{self._channel_id}/messages"
        params: dict = {"limit": 100}
        if self._last_message_id:
            params["after"] = self._last_message_id

        try:
            resp = requests.get(
                url,
                params=params,
                headers=self._auth_headers(),
                timeout=10,
            )
            if resp.status_code == 429:
                # Retry-After may be a numeric seconds value or, per RFC 9110,
                # an HTTP-date string. Fall back to 1.0s if it isn't a number.
                try:
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                except (ValueError, TypeError):
                    retry_after = 1.0
                self._backoff_until = time.time() + retry_after
                print(
                    f"[discord] Rate limited — backing off {retry_after}s",
                    file=sys.stderr,
                )
                return []
            if resp.status_code >= 400:
                print(
                    f"[discord] poll_updates returned {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                )
                return []
            messages = resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[discord] poll_updates error: {e}", file=sys.stderr)
            return []

        if not messages:
            return []

        # Discord returns messages newest-first; reverse to chronological order.
        messages = list(reversed(messages))

        updates: List[Update] = []
        for msg in messages:
            msg_id = msg.get("id", "")
            if msg_id:
                self._last_message_id = msg_id

            # Skip bot's own messages
            author_id = msg.get("author", {}).get("id", "")
            if author_id == self._bot_user_id:
                continue

            # Skip other bots
            if msg.get("author", {}).get("bot", False):
                continue

            content = msg.get("content", "") or ""
            # Strip @mention of the bot (e.g. <@1234567890>)
            if self._bot_user_id:
                content = content.replace(f"<@{self._bot_user_id}>", "").strip()
                content = content.replace(f"<@!{self._bot_user_id}>", "").strip()

            if not content:
                continue

            updates.append(
                Update(
                    update_id=next(self._update_counter),
                    message=Message(
                        text=content,
                        role="user",
                        timestamp=msg.get("timestamp", ""),
                        raw_data=msg,
                    ),
                    raw_data=msg,
                )
            )
        return updates

    def _send_chunk(self, text: str) -> bool:
        """POST a single message to the channel, with 429 retry."""
        from app.retry import retry_with_backoff

        url = f"{DISCORD_API_BASE}/channels/{self._channel_id}/messages"
        headers = {**self._auth_headers(), "Content-Type": "application/json"}

        def _get_retry_delay(exc: BaseException) -> Optional[float]:
            if hasattr(exc, "response") and exc.response is not None:  # type: ignore[attr-defined]
                # Retry-After may be a numeric seconds value or, per RFC 9110,
                # an HTTP-date string. Defer to retry_with_backoff's default
                # backoff if it isn't a number.
                try:
                    return float(exc.response.headers.get("Retry-After", "1"))  # type: ignore[attr-defined]
                except (ValueError, TypeError):
                    return None
            return None

        class _RateLimitError(requests.RequestException):
            pass

        def _do_post():
            resp = requests.post(url, json={"content": text}, headers=headers, timeout=10)
            if resp.status_code == 429:
                err = _RateLimitError("discord 429 rate limit")
                err.response = resp  # type: ignore[attr-defined]
                raise err
            if resp.status_code >= 500:
                raise requests.RequestException(
                    f"discord HTTP {resp.status_code}: {resp.text[:200]}"
                )
            if resp.status_code >= 400:
                print(
                    f"[discord] API error {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                )
                return False
            return True

        try:
            return bool(
                retry_with_backoff(
                    _do_post,
                    retryable=(requests.RequestException,),
                    get_retry_delay=_get_retry_delay,
                    label="discord send",
                )
            )
        except requests.RequestException as e:
            print(f"[discord] Send error after retries: {e}", file=sys.stderr)
            return False
