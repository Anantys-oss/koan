"""Tests for DiscordProvider — config, send, poll, cursor handling."""

from unittest.mock import patch, MagicMock, call

import pytest
import requests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """Create a pre-configured DiscordProvider (no configure() call)."""
    from app.messaging.discord import DiscordProvider
    p = DiscordProvider()
    p._bot_token = "Bot.token.here"
    p._channel_id = "111222333444555666"
    p._bot_user_id = "999000999000999"
    p._cursor_initialized = True
    return p


# ---------------------------------------------------------------------------
# TestConfigure
# ---------------------------------------------------------------------------


class TestConfigure:
    def _set_all(self, monkeypatch):
        monkeypatch.setenv("KOAN_DISCORD_BOT_TOKEN", "mytokenvalue")
        monkeypatch.setenv("KOAN_DISCORD_CHANNEL_ID", "123456789")

    @patch("app.utils.load_dotenv")
    @patch("app.messaging.discord.requests.get")
    def test_valid_credentials(self, mock_get, mock_dotenv, monkeypatch):
        self._set_all(monkeypatch)
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "88800012345"},
        )
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        assert p.configure() is True
        assert p._bot_token == "mytokenvalue"
        assert p._channel_id == "123456789"
        assert p._bot_user_id == "88800012345"

    @patch("app.utils.load_dotenv")
    def test_missing_token_fails(self, mock_dotenv, monkeypatch):
        monkeypatch.delenv("KOAN_DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.setenv("KOAN_DISCORD_CHANNEL_ID", "123456789")
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        with patch("app.utils.load_config", return_value={}):
            assert p.configure() is False

    @patch("app.utils.load_dotenv")
    def test_missing_channel_fails(self, mock_dotenv, monkeypatch):
        monkeypatch.setenv("KOAN_DISCORD_BOT_TOKEN", "tok")
        monkeypatch.delenv("KOAN_DISCORD_CHANNEL_ID", raising=False)
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        with patch("app.utils.load_config", return_value={}):
            assert p.configure() is False

    @patch("app.utils.load_dotenv")
    @patch("app.messaging.discord.requests.get")
    def test_invalid_token_401_fails(self, mock_get, mock_dotenv, monkeypatch):
        self._set_all(monkeypatch)
        mock_get.return_value = MagicMock(status_code=401, text="Unauthorized")
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        assert p.configure() is False

    @patch("app.utils.load_dotenv")
    @patch("app.messaging.discord.requests.get")
    def test_users_me_network_error_fails(self, mock_get, mock_dotenv, monkeypatch):
        self._set_all(monkeypatch)
        mock_get.side_effect = requests.RequestException("network error")
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        assert p.configure() is False

    @patch("app.utils.load_dotenv")
    @patch("app.messaging.discord.requests.get")
    def test_config_yaml_fallback(self, mock_get, mock_dotenv, monkeypatch):
        monkeypatch.delenv("KOAN_DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.delenv("KOAN_DISCORD_CHANNEL_ID", raising=False)
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "9999"},
        )
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        with patch("app.utils.load_config", return_value={
            "messaging": {"discord": {"bot_token": "yaml_token", "channel_id": "yaml_chan"}}
        }):
            result = p.configure()
        assert result is True
        assert p._bot_token == "yaml_token"
        assert p._channel_id == "yaml_chan"

    @patch("app.utils.load_dotenv")
    @patch("app.messaging.discord.requests.get")
    def test_env_overrides_config_yaml(self, mock_get, mock_dotenv, monkeypatch):
        monkeypatch.setenv("KOAN_DISCORD_BOT_TOKEN", "env_token")
        monkeypatch.setenv("KOAN_DISCORD_CHANNEL_ID", "env_chan")
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "42"},
        )
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        with patch("app.utils.load_config", return_value={
            "messaging": {"discord": {"bot_token": "yaml_token", "channel_id": "yaml_chan"}}
        }):
            p.configure()
        assert p._bot_token == "env_token"
        assert p._channel_id == "env_chan"


# ---------------------------------------------------------------------------
# TestGetters
# ---------------------------------------------------------------------------


class TestGetters:
    def test_provider_name(self, provider):
        assert provider.get_provider_name() == "discord"

    def test_channel_id(self, provider):
        assert provider.get_channel_id() == "111222333444555666"


# ---------------------------------------------------------------------------
# TestSendMessage
# ---------------------------------------------------------------------------


class TestSendMessage:
    @patch("app.messaging.discord.requests.post")
    def test_short_message(self, mock_post, provider):
        mock_post.return_value = MagicMock(status_code=200)
        assert provider.send_message("hello") is True
        assert mock_post.call_count == 1
        assert mock_post.call_args[1]["json"]["content"] == "hello"
        assert "Bot " in mock_post.call_args[1]["headers"]["Authorization"]

    @patch("app.messaging.discord.requests.post")
    def test_long_message_chunked_at_2000(self, mock_post, provider):
        mock_post.return_value = MagicMock(status_code=200)
        assert provider.send_message("x" * 5000) is True
        # 5000 chars → 3 chunks at 2000 limit
        assert mock_post.call_count == 3

    @patch("app.messaging.discord.requests.post")
    def test_4xx_returns_false(self, mock_post, provider):
        mock_post.return_value = MagicMock(status_code=403, text="Forbidden")
        assert provider.send_message("hi") is False

    @patch("app.retry.time.sleep")
    @patch("app.messaging.discord.requests.post")
    def test_5xx_retries_then_fails(self, mock_post, mock_sleep, provider):
        mock_post.return_value = MagicMock(status_code=503, text="unavailable")
        assert provider.send_message("hi") is False
        assert mock_post.call_count == 3

    @patch("app.messaging.discord.requests.post")
    def test_429_retries_with_retry_after(self, mock_post, provider):
        rate_limit_resp = MagicMock(
            status_code=429,
            headers={"Retry-After": "0.1"},
            text="rate limited",
        )
        ok_resp = MagicMock(status_code=200)
        mock_post.side_effect = [rate_limit_resp, ok_resp]
        assert provider.send_message("hi") is True
        assert mock_post.call_count == 2

    def test_empty_message_noop(self, provider):
        with patch("app.messaging.discord.requests.post") as mock_post:
            assert provider.send_message("") is True
            assert mock_post.call_count == 0

    def test_not_configured_returns_false(self):
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        assert p.send_message("test") is False

    def test_chunk_size_is_2000(self, provider):
        """Discord max is 2000 chars, not the default 4000."""
        from app.messaging.discord import MAX_MESSAGE_SIZE
        assert MAX_MESSAGE_SIZE == 2000
        with patch("app.messaging.discord.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            provider.send_message("a" * 2001)
            assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# TestPollUpdates
# ---------------------------------------------------------------------------


class TestPollUpdates:
    def _make_msg(self, msg_id, content, author_id="user123", bot=False):
        return {
            "id": msg_id,
            "content": content,
            "author": {"id": author_id, "bot": bot},
            "timestamp": "2026-01-01T00:00:00.000Z",
        }

    @patch("app.messaging.discord.requests.get")
    def test_bootstrap_sets_cursor_no_updates(self, mock_get, provider):
        """First poll (cursor_initialized=False) bootstraps cursor, returns []."""
        provider._cursor_initialized = False
        provider._last_message_id = None
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [self._make_msg("9999", "old message")],
        )
        updates = provider.poll_updates()
        assert updates == []
        assert provider._last_message_id == "9999"
        assert provider._cursor_initialized is True

    @patch("app.messaging.discord.requests.get")
    def test_returns_new_messages(self, mock_get, provider):
        provider._last_message_id = "1000"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [self._make_msg("1001", "hello bot")],
        )
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "hello bot"
        assert updates[0].message.role == "user"
        assert provider._last_message_id == "1001"

    @patch("app.messaging.discord.requests.get")
    def test_filters_own_messages(self, mock_get, provider):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                self._make_msg("2001", "my own message", author_id=provider._bot_user_id),
                self._make_msg("2002", "from user"),
            ],
        )
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "from user"

    @patch("app.messaging.discord.requests.get")
    def test_filters_other_bots(self, mock_get, provider):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                self._make_msg("3001", "bot says hi", author_id="other_bot", bot=True),
                self._make_msg("3002", "human says hi"),
            ],
        )
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "human says hi"

    @patch("app.messaging.discord.requests.get")
    def test_strips_bot_mention(self, mock_get, provider):
        bot_id = provider._bot_user_id
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                self._make_msg("4001", f"<@{bot_id}> /status"),
            ],
        )
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "/status"

    @patch("app.messaging.discord.requests.get")
    def test_strips_nickname_mention(self, mock_get, provider):
        bot_id = provider._bot_user_id
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                self._make_msg("4002", f"<@!{bot_id}> /help"),
            ],
        )
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "/help"

    @patch("app.messaging.discord.requests.get")
    def test_empty_response_no_cursor_change(self, mock_get, provider):
        provider._last_message_id = "5000"
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [],
        )
        updates = provider.poll_updates()
        assert updates == []
        assert provider._last_message_id == "5000"

    @patch("app.messaging.discord.requests.get")
    def test_messages_reversed_chronological(self, mock_get, provider):
        # Discord returns newest-first; provider must reverse to chronological.
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                self._make_msg("1003", "third"),
                self._make_msg("1002", "second"),
                self._make_msg("1001", "first"),
            ],
        )
        updates = provider.poll_updates()
        assert [u.message.text for u in updates] == ["first", "second", "third"]

    @patch("app.messaging.discord.requests.get")
    def test_advances_cursor_to_last_message(self, mock_get, provider):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                self._make_msg("1003", "third"),
                self._make_msg("1002", "second"),
                self._make_msg("1001", "first"),
            ],
        )
        provider.poll_updates()
        assert provider._last_message_id == "1003"

    @patch("app.messaging.discord.requests.get")
    def test_rate_limited_returns_empty(self, mock_get, provider):
        mock_get.return_value = MagicMock(
            status_code=429,
            headers={"Retry-After": "1"},
            text="rate limited",
        )
        assert provider.poll_updates() == []

    @patch("app.messaging.discord.requests.get")
    def test_network_error_returns_empty(self, mock_get, provider):
        mock_get.side_effect = requests.RequestException("boom")
        assert provider.poll_updates() == []

    @patch("app.messaging.discord.requests.get")
    def test_4xx_returns_empty(self, mock_get, provider):
        mock_get.return_value = MagicMock(status_code=403, text="Forbidden")
        assert provider.poll_updates() == []

    def test_not_configured_returns_empty(self):
        from app.messaging.discord import DiscordProvider
        p = DiscordProvider()
        assert p.poll_updates() == []

    @patch("app.messaging.discord.requests.get")
    def test_passes_after_param(self, mock_get, provider):
        provider._last_message_id = "7777"
        mock_get.return_value = MagicMock(status_code=200, json=lambda: [])
        provider.poll_updates()
        assert mock_get.call_args[1]["params"]["after"] == "7777"


# ---------------------------------------------------------------------------
# TestRegistry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_discord_registered(self):
        """Discord auto-registers when the messaging package loads providers."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        koan_pkg = Path(__file__).resolve().parents[1]
        script = (
            "from app.messaging import _ensure_providers_loaded, _providers\n"
            "_ensure_providers_loaded()\n"
            "assert 'discord' in _providers, sorted(_providers)\n"
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(koan_pkg),
            "KOAN_ROOT": os.environ.get("KOAN_ROOT", "/tmp/test-koan"),
        }
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
