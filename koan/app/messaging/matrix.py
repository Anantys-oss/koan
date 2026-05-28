"""Matrix messaging provider.

Talks to a Matrix homeserver via the Client-Server HTTP API. Synchronous
implementation using `requests`, mirroring the Telegram provider's style
(long-poll via /sync, send via /rooms/{roomId}/send).

Configuration is read from instance/config.yaml (recommended) under the
``messaging.matrix`` section, with environment variables as legacy/override
fallback.

config.yaml keys (under ``messaging.matrix``):
    homeserver, access_token, user_id, room_id

Environment variables (override config.yaml when set):
    KOAN_MATRIX_HOMESERVER   — Homeserver URL (e.g. https://matrix.org)
    KOAN_MATRIX_ACCESS_TOKEN — Access token for the bot account
    KOAN_MATRIX_USER_ID      — Bot's Matrix user ID (e.g. @koan:matrix.org)
    KOAN_MATRIX_ROOM_ID      — Room to operate in (e.g. !abc123:matrix.org)
"""

import itertools
import os
import sys
import threading
import time
import uuid
from typing import List, Optional
from urllib.parse import quote

import requests

from app.messaging.base import DEFAULT_MAX_MESSAGE_SIZE, Message, MessagingProvider, Update
from app.messaging import register_provider


MAX_MESSAGE_SIZE = DEFAULT_MAX_MESSAGE_SIZE
SYNC_TIMEOUT_MS = 30000  # 30s long-poll
SYNC_HTTP_TIMEOUT = 35   # leave 5s buffer over SYNC_TIMEOUT_MS


@register_provider("matrix")
class MatrixProvider(MessagingProvider):
    """Matrix Client-Server API provider.

    The first call to poll_updates() performs an initial /sync to fetch the
    current `next_batch` token without surfacing historical messages. Later
    calls long-poll for new events using that token.
    """

    def __init__(self):
        self._homeserver: str = ""
        self._access_token: str = ""
        self._user_id: str = ""
        self._room_id: str = ""

        self._sync_token: Optional[str] = None
        self._sync_initialized: bool = False
        self._update_counter = itertools.count(1)
        self._send_lock = threading.Lock()

    # -- MessagingProvider interface ------------------------------------------

    def configure(self) -> bool:
        from app.utils import load_config, load_dotenv
        load_dotenv()

        cfg: dict = {}
        messaging = load_config().get("messaging", {}) or {}
        if isinstance(messaging, dict):
            section = messaging.get("matrix", {}) or {}
            if isinstance(section, dict):
                cfg = section

        # env vars override config.yaml for backward compatibility
        self._homeserver = (
            os.environ.get("KOAN_MATRIX_HOMESERVER") or cfg.get("homeserver", "")
        ).rstrip("/")
        self._access_token = (
            os.environ.get("KOAN_MATRIX_ACCESS_TOKEN") or cfg.get("access_token", "")
        )
        self._user_id = (
            os.environ.get("KOAN_MATRIX_USER_ID") or cfg.get("user_id", "")
        )
        self._room_id = (
            os.environ.get("KOAN_MATRIX_ROOM_ID") or cfg.get("room_id", "")
        )

        missing = []
        if not self._homeserver:
            missing.append("homeserver")
        if not self._access_token:
            missing.append("access_token")
        if not self._user_id:
            missing.append("user_id")
        if not self._room_id:
            missing.append("room_id")
        if missing:
            print(
                f"[matrix] Missing required settings: {', '.join(missing)}. "
                f"Set in instance/config.yaml under messaging.matrix or via the "
                f"corresponding KOAN_MATRIX_* env vars.",
                file=sys.stderr,
            )
            return False

        if not self._homeserver.startswith(("http://", "https://")):
            print(
                "[matrix] KOAN_MATRIX_HOMESERVER must start with http:// or https://",
                file=sys.stderr,
            )
            return False

        return True

    def get_provider_name(self) -> str:
        return "matrix"

    def get_channel_id(self) -> str:
        return self._room_id

    def send_message(self, text: str) -> bool:
        """Send a message to the configured Matrix room, chunked if needed.

        Empty text is treated as a no-op success (matches Telegram behavior
        for clearing test state).
        """
        if not self._access_token or not self._room_id:
            print("[matrix] Not configured — cannot send.", file=sys.stderr)
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
        """Long-poll /sync for new room events.

        The `offset` parameter is unused — Matrix uses an opaque sync token
        stored on the provider instance. The first call discards historical
        events and only returns the current `next_batch`.
        """
        if not self._access_token:
            return []

        params: dict = {"timeout": SYNC_TIMEOUT_MS}
        if self._sync_token:
            params["since"] = self._sync_token
        else:
            # Initial sync: skip long-poll; we discard historical events.
            params["full_state"] = "false"
            params["timeout"] = 0

        headers = {"Authorization": f"Bearer {self._access_token}"}
        sync_http_timeout = SYNC_HTTP_TIMEOUT if self._sync_token else 10
        try:
            resp = requests.get(
                f"{self._homeserver}/_matrix/client/v3/sync",
                params=params,
                headers=headers,
                timeout=sync_http_timeout,
            )
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            print(f"[matrix] poll_updates error: {e}", file=sys.stderr)
            return []

        next_batch = data.get("next_batch")
        if not next_batch:
            return []

        # First sync — record the cursor, return nothing.
        if not self._sync_initialized:
            self._sync_token = next_batch
            self._sync_initialized = True
            return []

        updates = self._parse_room_events(data)
        self._sync_token = next_batch
        return updates

    # -- Internal helpers -----------------------------------------------------

    def _parse_room_events(self, sync_data: dict) -> List[Update]:
        """Extract m.room.message events from our configured room."""
        rooms = sync_data.get("rooms", {}).get("join", {})
        room = rooms.get(self._room_id, {})
        events = room.get("timeline", {}).get("events", [])

        updates: List[Update] = []
        for event in events:
            if event.get("type") != "m.room.message":
                continue
            sender = event.get("sender", "")
            # Skip our own messages
            if sender == self._user_id:
                continue

            content = event.get("content", {})
            msgtype = content.get("msgtype")
            if msgtype != "m.text":
                continue

            body = content.get("body", "")
            if not body:
                continue

            updates.append(
                Update(
                    update_id=next(self._update_counter),
                    message=Message(
                        text=body,
                        role="user",
                        timestamp=str(event.get("origin_server_ts", "")),
                        raw_data=event,
                    ),
                    raw_data=event,
                )
            )
        return updates

    def _send_chunk(self, text: str) -> bool:
        """PUT a single m.room.message to the homeserver."""
        from app.retry import retry_with_backoff

        txn_id = uuid.uuid4().hex
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/"
            f"{quote(self._room_id, safe='')}/send/m.room.message/{txn_id}"
        )
        payload = {"msgtype": "m.text", "body": text}
        headers = {"Authorization": f"Bearer {self._access_token}"}

        def _do_put():
            resp = requests.put(url, json=payload, headers=headers, timeout=10)
            if resp.status_code >= 400:
                # 4xx is not retryable; raise ValueError to short-circuit.
                if 400 <= resp.status_code < 500:
                    print(
                        f"[matrix] API error {resp.status_code}: {resp.text[:200]}",
                        file=sys.stderr,
                    )
                    return False
                # 5xx — surface as RequestException so retry_with_backoff retries.
                raise requests.RequestException(
                    f"matrix HTTP {resp.status_code}: {resp.text[:200]}"
                )
            return True

        try:
            return bool(
                retry_with_backoff(
                    _do_put,
                    retryable=(requests.RequestException,),
                    label="matrix send",
                )
            )
        except requests.RequestException as e:
            print(f"[matrix] Send error after retries: {e}", file=sys.stderr)
            return False

    def send_typing(self) -> bool:
        """Send a typing indicator to the room (auto-expires after ~10s)."""
        if not self._access_token or not self._room_id or not self._user_id:
            return False
        url = (
            f"{self._homeserver}/_matrix/client/v3/rooms/"
            f"{quote(self._room_id, safe='')}/typing/"
            f"{quote(self._user_id, safe='')}"
        )
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            resp = requests.put(
                url,
                json={"typing": True, "timeout": 10000},
                headers=headers,
                timeout=5,
            )
            return resp.status_code < 400
        except requests.RequestException:
            return False
