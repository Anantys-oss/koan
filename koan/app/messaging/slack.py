"""Slack messaging provider.

Uses the Slack SDK (WebClient for sending, SocketModeClient for receiving).
Requires slack-sdk package: pip install slack-sdk

Environment variables:
    KOAN_SLACK_BOT_TOKEN    — Bot User OAuth Token (xoxb-...)
    KOAN_SLACK_APP_TOKEN    — App-Level Token for Socket Mode (xapp-...)
    KOAN_SLACK_CHANNEL_ID   — Channel ID to operate in (C...)
"""

import itertools
import os
import queue
import re
import sys
import threading
import time
from collections import OrderedDict
from typing import List, Optional

from app.messaging.base import DEFAULT_MAX_MESSAGE_SIZE, Message, MessagingProvider, Update
from app.messaging import register_provider


# Rate limit: Slack allows ~1 msg/sec for chat.postMessage
SLACK_RATE_LIMIT_SECONDS = 1.0
MAX_MESSAGE_SIZE = DEFAULT_MAX_MESSAGE_SIZE

# Bounded memory for threading/dedup state. These cap unbounded growth on a
# long-running bridge; oldest entries are evicted FIFO.
_MAX_THREAD_TOKENS = 1000   # int token -> thread_ts, for routing replies
_MAX_ENGAGED_THREADS = 1000  # thread_ts the bot is participating in
_MAX_SEEN_TS = 2000          # event ts already processed (dedup)
_MAX_TS_TOKENS = 1000        # int token -> message ts, for reactions

# Unicode emoji -> Slack shortname (reactions.add wants a name, not the glyph).
_SLACK_EMOJI_NAMES = {
    "✅": "white_check_mark",
    "👍": "+1",
    "❌": "x",
}


@register_provider("slack")
class SlackProvider(MessagingProvider):
    """Slack provider using Bot API and Socket Mode.

    Socket Mode maintains a persistent WebSocket connection for receiving
    events. Messages are buffered in a thread-safe queue and returned
    by poll_updates().
    """

    def __init__(self):
        self._bot_token: str = ""
        self._app_token: str = ""
        self._channel_id: str = ""
        self._web_client = None
        self._socket_client = None
        self._bot_user_id: str = ""

        # Thread-safe message buffer for poll_updates()
        self._message_queue: queue.Queue = queue.Queue()
        self._update_counter = itertools.count(1)
        self._send_lock = threading.Lock()
        self._last_send_time: float = 0.0
        self._connect_lock = threading.Lock()
        self._connected: bool = False

        # Threading state. Slack threads are keyed by ``thread_ts`` (a string),
        # but the bridge's reply-context plumbing carries an int
        # ``reply_to_message_id``. We bridge the two with a token map: each
        # inbound message gets an int token (used as ``message_id`` in the
        # Telegram-shaped envelope) that maps back to its ``thread_ts`` here.
        self._state_lock = threading.Lock()
        self._thread_by_token: "OrderedDict[int, str]" = OrderedDict()
        # Token -> the message's own ts (not the thread root), so reactions.add
        # lands on the exact user message even for in-thread replies.
        self._ts_by_token: "OrderedDict[int, str]" = OrderedDict()
        # thread_ts values the bot is engaged in (was mentioned / replied in),
        # so follow-up messages in the thread are handled without re-mention.
        self._engaged_threads: "OrderedDict[str, None]" = OrderedDict()
        # Slack delivers both ``app_mention`` and ``message`` for a channel
        # mention; dedup by event ts so we only act once.
        self._seen_ts: "OrderedDict[str, None]" = OrderedDict()
        # Warn only once if the assistant status API is unavailable.
        self._status_warned: bool = False

    # -- MessagingProvider interface ------------------------------------------

    def configure(self) -> bool:
        from app.utils import load_dotenv
        load_dotenv()

        self._bot_token = os.environ.get("KOAN_SLACK_BOT_TOKEN", "")
        self._app_token = os.environ.get("KOAN_SLACK_APP_TOKEN", "")
        self._channel_id = os.environ.get("KOAN_SLACK_CHANNEL_ID", "")

        if not self._bot_token:
            print("[slack] KOAN_SLACK_BOT_TOKEN not set.", file=sys.stderr)
            return False
        if not self._app_token:
            print("[slack] KOAN_SLACK_APP_TOKEN not set (required for Socket Mode).",
                  file=sys.stderr)
            return False
        if not self._channel_id:
            print("[slack] KOAN_SLACK_CHANNEL_ID not set.", file=sys.stderr)
            return False

        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
        except ImportError:
            print("[slack] slack-sdk not installed. Run: pip install slack-sdk",
                  file=sys.stderr)
            return False

        self._web_client = WebClient(token=self._bot_token)

        # Resolve bot user ID for stripping @mentions
        try:
            auth = self._web_client.auth_test()
            self._bot_user_id = auth.get("user_id", "")
        except Exception as e:
            print(f"[slack] Auth test failed: {e}", file=sys.stderr)
            return False

        # Set up Socket Mode client
        self._socket_client = SocketModeClient(
            app_token=self._app_token,
            web_client=self._web_client,
        )
        self._socket_client.socket_mode_request_listeners.append(
            self._handle_socket_event
        )

        return True

    def get_provider_name(self) -> str:
        return "slack"

    def get_channel_id(self) -> str:
        return self._channel_id

    def send_message(self, text: str, reply_to_message_id: int = 0) -> bool:
        """Send a message to the configured Slack channel with rate limiting.

        Applies rate limiting between chunks to comply with Slack's ~1 msg/sec limit.

        When ``reply_to_message_id`` maps to a known thread (set by the bridge's
        reply context after an inbound message), the reply is posted into that
        Slack thread via ``thread_ts``. Otherwise it goes to the channel root —
        which is the case for asynchronous agent notifications (outbox).

        Returns:
            True if all chunks sent successfully, False otherwise.
        """
        if not self._web_client:
            print("[slack] Not configured — cannot send.", file=sys.stderr)
            return False

        thread_ts = ""
        if reply_to_message_id:
            with self._state_lock:
                thread_ts = self._thread_by_token.get(reply_to_message_id, "")

        ok = True
        for chunk in self.chunk_message(text, max_size=MAX_MESSAGE_SIZE):
            with self._send_lock:
                self._apply_rate_limit()

                kwargs = {"channel": self._channel_id, "text": chunk}
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts
                try:
                    resp = self._web_client.chat_postMessage(**kwargs)
                    if not resp.get("ok"):
                        print(f"[slack] API error: {resp.get('error', 'unknown')}",
                              file=sys.stderr)
                        ok = False
                except Exception as e:
                    print(f"[slack] Send error: {e}", file=sys.stderr)
                    ok = False
                finally:
                    self._last_send_time = time.time()
        return ok

    def poll_updates(self, offset: Optional[int] = None) -> List[Update]:
        """Return buffered updates from Socket Mode.

        Socket Mode receives events asynchronously in a background thread.
        This method drains the queue and returns all buffered updates.
        """
        if not self._connected and self._socket_client:
            with self._connect_lock:
                if not self._connected:
                    self._start_socket_mode()

        updates: List[Update] = []
        while not self._message_queue.empty():
            try:
                updates.append(self._message_queue.get_nowait())
            except queue.Empty:
                break
        return updates

    def send_typing(self, reply_to_message_id: int = 0, status: str = "") -> bool:
        """Show a "thinking" status in the message's thread.

        Uses Slack's assistant status API (``assistant.threads.setStatus``),
        which renders greyed italic text under the bot name. It accepts the
        ``chat:write`` scope the app already has and needs only the channel and
        a ``thread_ts``. Slack auto-clears the status when the bot posts its
        reply; ``stop_typing()`` covers error/early-exit paths.

        Failures (e.g. the thread is not assistant-enabled) are swallowed: the
        status is best-effort UX and must never affect the actual reply.
        """
        thread_ts = self._thread_for_token(reply_to_message_id)
        if not thread_ts or not self._web_client:
            return True
        return self._set_status(thread_ts, status or "is thinking…")

    def stop_typing(self, reply_to_message_id: int = 0) -> bool:
        """Clear the assistant status for the message's thread."""
        thread_ts = self._thread_for_token(reply_to_message_id)
        if not thread_ts or not self._web_client:
            return True
        return self._set_status(thread_ts, "")

    def add_reaction(self, reply_to_message_id: int, emoji: str) -> bool:
        """Add an emoji reaction to the user's original message.

        Reacts on the message's own ts (not the thread root), so an in-thread
        command is acknowledged on the right message. Best-effort: a missing
        token or API failure returns False so the caller falls back to text.
        """
        ts = self._ts_for_token(reply_to_message_id)
        if not ts or not self._web_client:
            return False
        name = self._slack_reaction_name(emoji)
        try:
            resp = self._web_client.reactions_add(
                channel=self._channel_id, timestamp=ts, name=name,
            )
            if resp.get("ok"):
                return True
            print(f"[slack] reactions_add error: {resp.get('error', 'unknown')}",
                  file=sys.stderr)
            return False
        except Exception as e:
            if "already_reacted" in str(e):
                return True  # idempotent — the reaction is present
            print(f"[slack] reactions_add failed: {e}", file=sys.stderr)
            return False

    def reaction_acknowledges_mission(self) -> bool:
        """On Slack a ✅ reaction is a complete ack; suppress the text reply."""
        return True

    # -- Internal helpers -----------------------------------------------------

    def _thread_for_token(self, token: int) -> str:
        """Resolve an inbound message token back to its Slack thread_ts."""
        if not token:
            return ""
        with self._state_lock:
            return self._thread_by_token.get(token, "")

    def _ts_for_token(self, token: int) -> str:
        """Resolve an inbound message token back to that message's own ts."""
        if not token:
            return ""
        with self._state_lock:
            return self._ts_by_token.get(token, "")

    @staticmethod
    def _slack_reaction_name(emoji: str) -> str:
        """Translate a Unicode emoji to a Slack reaction shortname."""
        name = _SLACK_EMOJI_NAMES.get(emoji)
        if name:
            return name
        return emoji.strip(":")  # tolerate a shortname passed directly

    def _set_status(self, thread_ts: str, status: str) -> bool:
        """Best-effort assistant status update; swallow API/SDK errors."""
        try:
            self._web_client.assistant_threads_setStatus(
                channel_id=self._channel_id,
                thread_ts=thread_ts,
                status=status,
            )
            return True
        except Exception as e:
            # Common when the thread is not assistant-enabled — non-fatal.
            # Warn once per process so a 4s refresh loop doesn't spam stderr.
            if not self._status_warned:
                self._status_warned = True
                print(f"[slack] assistant status unavailable (skipping): {e}",
                      file=sys.stderr)
            return True

    def _apply_rate_limit(self):
        """Sleep if needed to comply with Slack's rate limit (~1 msg/sec)."""
        elapsed = time.time() - self._last_send_time
        if elapsed < SLACK_RATE_LIMIT_SECONDS:
            time.sleep(SLACK_RATE_LIMIT_SECONDS - elapsed)

    def _start_socket_mode(self):
        """Start Socket Mode connection in a background thread."""
        try:
            self._socket_client.connect()
            self._connected = True
            print("[slack] Socket Mode connected.", file=sys.stderr)
        except Exception as e:
            print(f"[slack] Socket Mode connection failed: {e}", file=sys.stderr)

    def _handle_socket_event(self, client, req):
        """Handle incoming Socket Mode events.
        
        Processes message and app_mention events from the configured channel,
        strips bot mentions, and queues updates for poll_updates().
        """
        # Acknowledge the event immediately
        self._acknowledge_event(client, req)

        payload = req.payload or {}
        event = payload.get("event", {})

        # Only process relevant events from configured channel
        if not self._should_process_event(event):
            return

        # Dedup: Slack delivers both ``app_mention`` and ``message`` for a
        # channel mention. Act on the first; ignore the redelivery.
        ts = event.get("ts", "")
        if ts and self._is_duplicate(ts):
            return

        text = self._extract_message_text(event)
        if not text:
            return

        self._queue_update(text, event, payload)

    def _acknowledge_event(self, client, req):
        """Send acknowledgement for Socket Mode event."""
        try:
            from slack_sdk.socket_mode.response import SocketModeResponse
            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
        except ImportError:
            pass

    def _should_process_event(self, event: dict) -> bool:
        """Decide whether an incoming event is for the bot.

        Accepts: message/app_mention events, in the configured channel, not from
        a bot or our own user, that either (a) @mention the bot / are
        app_mentions, or (b) are replies in a thread the bot is already engaged
        in. This keeps the bot quiet in shared channels until it is pinged, then
        lets a conversation continue in-thread without re-mentioning.
        """
        event_type = event.get("type", "")
        if event_type not in ("message", "app_mention"):
            return False

        # Filter to configured channel only
        if event.get("channel", "") != self._channel_id:
            return False

        # Skip bot messages, subtypes (edits, joins, etc.), and our own messages
        if event.get("bot_id") or event.get("subtype"):
            return False
        if self._bot_user_id and event.get("user", "") == self._bot_user_id:
            return False

        return self._is_addressed_to_bot(event)

    def _is_addressed_to_bot(self, event: dict) -> bool:
        """Return True for app_mentions, direct @mentions, commands, or engaged threads.

        A message whose text begins with ``/`` followed by a letter (e.g.
        ``/help``) is treated as a command addressed to the bot, just like an
        explicit ``@bot /help`` — no mention required. A leading slash followed
        by a non-letter (``//`` comments, dotfile paths like ``/.bashrc``,
        numeric/symbol prefixes) is ignored. Note that this heuristic cannot
        distinguish a command from a letter-initial path: a pasted path like
        ``/Users/foo/log.txt`` at message start *is* treated as a command and
        falls through to an (unrecognized-command) help reply.

        Side effect: when the bot is addressed, the conversation's thread root is
        marked engaged so subsequent replies in that thread are handled too.
        """
        text = event.get("text", "")
        mentioned = bool(self._bot_user_id) and f"<@{self._bot_user_id}>" in text
        is_command = bool(re.match(r"/[a-zA-Z]", text.lstrip()))
        # For a channel-root message Slack omits thread_ts; the message's own ts
        # is the root of the thread the bot will reply into.
        thread_root = event.get("thread_ts") or event.get("ts", "")

        if event.get("type") == "app_mention" or mentioned or is_command:
            if thread_root:
                self._mark_engaged(thread_root)
            return True

        # Continuation: a reply within a thread the bot is already part of.
        event_thread = event.get("thread_ts", "")
        with self._state_lock:
            return bool(event_thread) and event_thread in self._engaged_threads

    # -- bounded-state helpers -------------------------------------------------

    @staticmethod
    def _bounded_add(store: "OrderedDict", key, value, limit: int) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > limit:
            store.popitem(last=False)

    def _mark_engaged(self, thread_ts: str) -> None:
        with self._state_lock:
            self._bounded_add(self._engaged_threads, thread_ts, None, _MAX_ENGAGED_THREADS)

    def _is_duplicate(self, ts: str) -> bool:
        with self._state_lock:
            if ts in self._seen_ts:
                return True
            self._bounded_add(self._seen_ts, ts, None, _MAX_SEEN_TS)
            return False

    def _remember_thread(self, token: int, thread_ts: str) -> None:
        with self._state_lock:
            self._bounded_add(self._thread_by_token, token, thread_ts, _MAX_THREAD_TOKENS)

    def _extract_message_text(self, event: dict) -> str:
        """Extract and clean message text from event."""
        text = event.get("text", "")
        if not text:
            return ""

        # Strip @bot mentions from text
        if self._bot_user_id:
            text = re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()

        return text

    def _queue_update(self, text: str, event: dict, payload: dict):
        """Create and queue an Update from processed event data.

        ``raw_data`` is built in the Telegram-shaped envelope the bridge main
        loop expects (``message.text`` / ``message.chat.id`` / ``message.message_id``)
        so a single provider-agnostic loop handles every provider. The int
        ``message_id`` is a token mapping back to this message's ``thread_ts`` so
        the bridge's reply context can route the reply into the right thread.
        """
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        token = next(self._update_counter)
        if thread_ts:
            self._remember_thread(token, thread_ts)
        message_ts = event.get("ts", "")
        if message_ts:
            with self._state_lock:
                self._bounded_add(self._ts_by_token, token, message_ts, _MAX_TS_TOKENS)

        envelope = {
            "message": {
                "text": text,
                "chat": {"id": self._channel_id},
                "message_id": token,
            }
        }
        update = Update(
            update_id=token,
            message=Message(
                text=text,
                role="user",
                timestamp=event.get("ts", ""),
                raw_data=event,
            ),
            raw_data=envelope,
        )
        self._message_queue.put(update)

    def _send_raw(self, text: str) -> bool:
        """Send a message without rate limiting.
        
        Internal method for testing purposes. Production code should use
        send_message() which includes proper rate limiting.
        
        Returns:
            True if message sent successfully, False otherwise.
        """
        if not self._web_client:
            return False
        try:
            resp = self._web_client.chat_postMessage(
                channel=self._channel_id,
                text=text,
            )
            return resp.get("ok", False)
        except Exception as e:
            print(f"[slack] Send error: {e}", file=sys.stderr)
            return False
