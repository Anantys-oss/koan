"""Tests for SlackProvider — configuration, sending, polling, event handling."""

from unittest.mock import patch, MagicMock, PropertyMock
import queue

import pytest


class TestConfigure:
    def test_missing_bot_token(self, monkeypatch):
        monkeypatch.delenv("KOAN_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.setenv("KOAN_SLACK_APP_TOKEN", "xapp-test")
        monkeypatch.setenv("KOAN_SLACK_CHANNEL_ID", "C123")
        from app.messaging.slack import SlackProvider
        p = SlackProvider()
        assert p.configure() is False

    def test_missing_app_token(self, monkeypatch):
        monkeypatch.setenv("KOAN_SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.delenv("KOAN_SLACK_APP_TOKEN", raising=False)
        monkeypatch.setenv("KOAN_SLACK_CHANNEL_ID", "C123")
        from app.messaging.slack import SlackProvider
        p = SlackProvider()
        assert p.configure() is False

    def test_missing_channel_id(self, monkeypatch):
        monkeypatch.setenv("KOAN_SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("KOAN_SLACK_APP_TOKEN", "xapp-test")
        monkeypatch.delenv("KOAN_SLACK_CHANNEL_ID", raising=False)
        from app.messaging.slack import SlackProvider
        p = SlackProvider()
        assert p.configure() is False

    def test_missing_slack_sdk(self, monkeypatch):
        monkeypatch.setenv("KOAN_SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("KOAN_SLACK_APP_TOKEN", "xapp-test")
        monkeypatch.setenv("KOAN_SLACK_CHANNEL_ID", "C123")
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if "slack_sdk" in name:
                raise ImportError("no slack_sdk")
            return real_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=mock_import):
            from app.messaging.slack import SlackProvider
            p = SlackProvider()
            assert p.configure() is False


@pytest.fixture
def provider():
    """Create a pre-configured SlackProvider with mocked SDK."""
    from app.messaging.slack import SlackProvider
    p = SlackProvider()
    p._bot_token = "xoxb-test"
    p._app_token = "xapp-test"
    p._channel_id = "C123"
    p._bot_user_id = "U999"
    p._web_client = MagicMock()
    p._socket_client = MagicMock()
    return p


class TestGetters:
    def test_provider_name(self, provider):
        assert provider.get_provider_name() == "slack"

    def test_channel_id(self, provider):
        assert provider.get_channel_id() == "C123"


class TestSendMessage:
    def test_short_message(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        assert provider.send_message("hello") is True
        provider._web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="hello"
        )

    def test_long_message_chunked(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        # Bypass rate-limit sleeps (1s between chunks) to keep test fast
        with patch("app.messaging.slack.time.sleep"):
            assert provider.send_message("x" * 8500) is True
        assert provider._web_client.chat_postMessage.call_count == 3

    def test_api_error(self, provider):
        provider._web_client.chat_postMessage.return_value = {
            "ok": False, "error": "channel_not_found"
        }
        assert provider.send_message("test") is False

    def test_exception(self, provider):
        provider._web_client.chat_postMessage.side_effect = Exception("network")
        assert provider.send_message("test") is False

    def test_not_configured(self):
        from app.messaging.slack import SlackProvider
        p = SlackProvider()
        assert p.send_message("test") is False

    def test_empty_message(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        assert provider.send_message("") is True


class TestPollUpdates:
    def test_empty_queue(self, provider):
        provider._connected = True
        updates = provider.poll_updates()
        assert updates == []

    def test_drains_queue(self, provider):
        from app.messaging.base import Update, Message
        provider._connected = True
        provider._message_queue.put(
            Update(update_id=1, message=Message(text="hi", role="user"))
        )
        provider._message_queue.put(
            Update(update_id=2, message=Message(text="hey", role="user"))
        )
        updates = provider.poll_updates()
        assert len(updates) == 2
        assert updates[0].message.text == "hi"
        assert updates[1].message.text == "hey"

    def test_starts_socket_if_not_connected(self, provider):
        provider._connected = False
        provider.poll_updates()
        provider._socket_client.connect.assert_called_once()

    def test_handles_connection_failure(self, provider):
        provider._connected = False
        provider._socket_client.connect.side_effect = Exception("connection failed")
        updates = provider.poll_updates()
        assert updates == []


class TestHandleSocketEvent:
    """Test Socket Mode event handling and filtering logic."""
    
    def _make_request(self, event_type, channel, text,
                      bot_id=None, subtype=None, ts="123.456",
                      user="U100", thread_ts=None):
        """Create a mock Socket Mode request with the given event properties."""
        req = MagicMock()
        req.envelope_id = "env-1"
        event = {
            "type": event_type,
            "channel": channel,
            "text": text,
            "ts": ts,
        }
        if user:
            event["user"] = user
        if bot_id:
            event["bot_id"] = bot_id
        if subtype:
            event["subtype"] = subtype
        if thread_ts:
            event["thread_ts"] = thread_ts
        req.payload = {"event": event}
        return req

    def test_app_mention_event(self, provider):
        req = self._make_request("app_mention", "C123", "<@U999> hello")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.qsize() == 1
        update = provider._message_queue.get_nowait()
        assert update.message.text == "hello"

    def test_app_mention_strips_bot_mention(self, provider):
        req = self._make_request("app_mention", "C123", "<@U999> do something")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        assert update.message.text == "do something"

    def test_plain_message_without_mention_ignored(self, provider):
        # Mention-gating: ordinary channel chatter must not trigger the bot.
        req = self._make_request("message", "C123", "just chatting")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_slash_command_without_mention_processed(self, provider):
        # A /-prefixed message is a command — handled without @bot mention.
        req = self._make_request("message", "C123", "/help")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        assert update.message.text == "/help"

    def test_slash_command_with_leading_whitespace_processed(self, provider):
        req = self._make_request("message", "C123", "  /status")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        assert update.message.text == "/status"

    def test_slash_command_reply_routes_to_thread(self, provider):
        # The command at channel root engages its thread; the reply posts there.
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        req = self._make_request("message", "C123", "/help", ts="200.5")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        token = update.raw_data["message"]["message_id"]

        assert provider.send_message("here you go", reply_to_message_id=token) is True
        provider._web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="here you go", thread_ts="200.5"
        )

    def test_non_command_slash_in_middle_ignored(self, provider):
        # Only a leading slash counts — "a/b" chatter must not trigger the bot.
        req = self._make_request("message", "C123", "see foo/bar for details")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_leading_slash_non_letter_not_command(self, provider):
        # A leading slash followed by a non-letter (// comment, dotfile path) is
        # ignored. Note: letter-initial paths like /etc/hosts DO match /[a-zA-Z]
        # and are treated as commands, so they are not valid examples here.
        for text in ("//deploy note: ship it", "/.config/app.toml is the culprit"):
            req = self._make_request("message", "C123", text)
            provider._handle_socket_event(MagicMock(), req)
            assert provider._message_queue.empty()

    def test_leading_slash_letter_path_is_command(self, provider):
        # Documents the real limitation: a letter-initial path at message start
        # cannot be distinguished from a command, so it IS treated as one.
        req = self._make_request("message", "C123", "/Users/foo/log.txt")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        assert update.message.text == "/Users/foo/log.txt"

    def test_inline_mention_in_message_processed(self, provider):
        req = self._make_request("message", "C123", "hey <@U999> ship it")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        assert update.message.text == "hey ship it"

    def test_ignores_other_channels(self, provider):
        req = self._make_request("app_mention", "C999", "<@U999> wrong channel")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_ignores_bot_messages(self, provider):
        req = self._make_request("message", "C123", "<@U999> bot msg", bot_id="B123")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_ignores_own_messages(self, provider):
        req = self._make_request("app_mention", "C123", "<@U999> echo", user="U999")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_ignores_subtypes(self, provider):
        req = self._make_request("message", "C123", "<@U999> edited",
                                 subtype="message_changed")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_ignores_empty_text(self, provider):
        req = self._make_request("app_mention", "C123", "")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_ignores_non_message_events(self, provider):
        req = self._make_request("reaction_added", "C123", "")
        provider._handle_socket_event(MagicMock(), req)
        assert provider._message_queue.empty()

    def test_duplicate_ts_processed_once(self, provider):
        # app_mention + message double-delivery share a ts; act once.
        mention = self._make_request("app_mention", "C123", "<@U999> go", ts="9.1")
        echo = self._make_request("message", "C123", "<@U999> go", ts="9.1")
        provider._handle_socket_event(MagicMock(), mention)
        provider._handle_socket_event(MagicMock(), echo)
        assert provider._message_queue.qsize() == 1

    def test_thread_continuation_without_mention(self, provider):
        # First a mention at channel root (ts=10.0) engages the thread...
        root = self._make_request("app_mention", "C123", "<@U999> start", ts="10.0")
        provider._handle_socket_event(MagicMock(), root)
        provider._message_queue.get_nowait()
        # ...then a plain reply in that thread is handled without re-mention.
        reply = self._make_request("message", "C123", "and continue",
                                   ts="11.0", thread_ts="10.0")
        provider._handle_socket_event(MagicMock(), reply)
        update = provider._message_queue.get_nowait()
        assert update.message.text == "and continue"

    def test_envelope_is_telegram_shaped(self, provider):
        # The bridge main loop reads message.text / message.chat.id / message_id.
        req = self._make_request("app_mention", "C123", "<@U999> hi")
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        env = update.raw_data
        assert env["message"]["text"] == "hi"
        assert env["message"]["chat"]["id"] == "C123"
        assert isinstance(env["message"]["message_id"], int)
        assert env["message"]["message_id"] != 0

    def test_update_counter_increments(self, provider):
        req1 = self._make_request("app_mention", "C123", "<@U999> first", ts="1.0")
        req2 = self._make_request("app_mention", "C123", "<@U999> second", ts="2.0")
        provider._handle_socket_event(MagicMock(), req1)
        provider._handle_socket_event(MagicMock(), req2)
        u1 = provider._message_queue.get_nowait()
        u2 = provider._message_queue.get_nowait()
        assert u1.update_id == 1
        assert u2.update_id == 2

    def test_acknowledges_event(self, provider):
        req = self._make_request("message", "C123", "hello")
        mock_client = MagicMock()
        # Patch the slack_sdk import to succeed (even without slack_sdk installed)
        mock_response_cls = MagicMock()
        with patch.dict("sys.modules", {
            "slack_sdk": MagicMock(),
            "slack_sdk.socket_mode": MagicMock(),
            "slack_sdk.socket_mode.response": MagicMock(SocketModeResponse=mock_response_cls),
        }):
            provider._handle_socket_event(mock_client, req)
        mock_client.send_socket_mode_response.assert_called_once()


class TestThreadedSend:
    """Replies route into the Slack thread of the originating message."""

    def test_reply_uses_thread_ts(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        # Simulate an inbound mention that registered token 1 -> thread_ts.
        req = MagicMock()
        req.envelope_id = "e1"
        req.payload = {"event": {
            "type": "app_mention", "channel": "C123",
            "text": "<@U999> hi", "ts": "100.5", "user": "U100",
        }}
        provider._handle_socket_event(MagicMock(), req)
        update = provider._message_queue.get_nowait()
        token = update.raw_data["message"]["message_id"]

        assert provider.send_message("reply", reply_to_message_id=token) is True
        provider._web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="reply", thread_ts="100.5"
        )

    def test_unknown_token_posts_to_channel_root(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        assert provider.send_message("hi", reply_to_message_id=4242) is True
        provider._web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="hi"
        )

    def test_no_reply_context_posts_to_channel_root(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        assert provider.send_message("async note") is True
        provider._web_client.chat_postMessage.assert_called_once_with(
            channel="C123", text="async note"
        )


class TestTypingStatus:
    """Slack 'thinking' status via assistant.threads.setStatus."""

    def _register_token(self, provider, token, thread_ts):
        provider._thread_by_token[token] = thread_ts

    def test_send_typing_sets_status_in_thread(self, provider):
        self._register_token(provider, 5, "100.5")
        provider._web_client.assistant_threads_setStatus.return_value = {"ok": True}
        assert provider.send_typing(reply_to_message_id=5, status="Thinking…") is True
        provider._web_client.assistant_threads_setStatus.assert_called_once_with(
            channel_id="C123", thread_ts="100.5", status="Thinking…"
        )

    def test_send_typing_defaults_status_text(self, provider):
        self._register_token(provider, 5, "100.5")
        provider._web_client.assistant_threads_setStatus.return_value = {"ok": True}
        provider.send_typing(reply_to_message_id=5)
        _, kwargs = provider._web_client.assistant_threads_setStatus.call_args
        assert kwargs["status"]  # non-empty fallback

    def test_send_typing_unknown_token_is_noop(self, provider):
        assert provider.send_typing(reply_to_message_id=999, status="Thinking…") is True
        provider._web_client.assistant_threads_setStatus.assert_not_called()

    def test_send_typing_zero_token_is_noop(self, provider):
        assert provider.send_typing(reply_to_message_id=0, status="Thinking…") is True
        provider._web_client.assistant_threads_setStatus.assert_not_called()

    def test_stop_typing_clears_status(self, provider):
        self._register_token(provider, 5, "100.5")
        provider._web_client.assistant_threads_setStatus.return_value = {"ok": True}
        assert provider.stop_typing(reply_to_message_id=5) is True
        provider._web_client.assistant_threads_setStatus.assert_called_once_with(
            channel_id="C123", thread_ts="100.5", status=""
        )

    def test_api_errors_are_swallowed(self, provider):
        self._register_token(provider, 5, "100.5")
        provider._web_client.assistant_threads_setStatus.side_effect = Exception("nope")
        # Best-effort UX: never propagate, never block the reply.
        assert provider.send_typing(reply_to_message_id=5, status="Thinking…") is True
        assert provider.stop_typing(reply_to_message_id=5) is True

    def test_not_configured_is_noop(self):
        from app.messaging.slack import SlackProvider
        p = SlackProvider()
        p._thread_by_token[5] = "100.5"
        assert p.send_typing(reply_to_message_id=5, status="Thinking…") is True
        assert p.stop_typing(reply_to_message_id=5) is True


class TestSendRaw:
    def test_send_raw(self, provider):
        provider._web_client.chat_postMessage.return_value = {"ok": True}
        assert provider._send_raw("test") is True

    def test_send_raw_not_configured(self):
        from app.messaging.slack import SlackProvider
        p = SlackProvider()
        assert p._send_raw("test") is False

    def test_send_raw_error(self, provider):
        provider._web_client.chat_postMessage.side_effect = Exception("fail")
        assert provider._send_raw("test") is False
