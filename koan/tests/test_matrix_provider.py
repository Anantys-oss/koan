"""Tests for MatrixProvider — config, send, poll, sync cursor handling."""

from unittest.mock import patch, MagicMock

import pytest
import requests


@pytest.fixture
def provider():
    """Create a pre-configured MatrixProvider."""
    from app.messaging.matrix import MatrixProvider
    p = MatrixProvider()
    p._homeserver = "https://matrix.example"
    p._access_token = "syt_token"
    p._user_id = "@koan:matrix.example"
    p._room_id = "!room:matrix.example"
    return p


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfigure:
    def _set_all(self, monkeypatch):
        monkeypatch.setenv("KOAN_MATRIX_HOMESERVER", "https://matrix.example")
        monkeypatch.setenv("KOAN_MATRIX_ACCESS_TOKEN", "syt_token")
        monkeypatch.setenv("KOAN_MATRIX_USER_ID", "@koan:matrix.example")
        monkeypatch.setenv("KOAN_MATRIX_ROOM_ID", "!room:matrix.example")

    @patch("app.utils.load_dotenv")
    def test_valid_credentials(self, mock_dotenv, monkeypatch):
        self._set_all(monkeypatch)
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.configure() is True
        assert p._homeserver == "https://matrix.example"
        assert p._access_token == "syt_token"
        assert p._user_id == "@koan:matrix.example"
        assert p._room_id == "!room:matrix.example"

    @patch("app.utils.load_dotenv")
    def test_trailing_slash_stripped(self, mock_dotenv, monkeypatch):
        self._set_all(monkeypatch)
        monkeypatch.setenv("KOAN_MATRIX_HOMESERVER", "https://matrix.example/")
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.configure() is True
        assert p._homeserver == "https://matrix.example"

    @pytest.mark.parametrize("var", [
        "KOAN_MATRIX_HOMESERVER",
        "KOAN_MATRIX_ACCESS_TOKEN",
        "KOAN_MATRIX_USER_ID",
        "KOAN_MATRIX_ROOM_ID",
    ])
    @patch("app.utils.load_dotenv")
    def test_missing_var_fails(self, mock_dotenv, monkeypatch, var):
        self._set_all(monkeypatch)
        monkeypatch.delenv(var, raising=False)
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.configure() is False

    @patch("app.utils.load_dotenv")
    def test_invalid_homeserver_scheme(self, mock_dotenv, monkeypatch):
        self._set_all(monkeypatch)
        monkeypatch.setenv("KOAN_MATRIX_HOMESERVER", "matrix.example")
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.configure() is False


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------


class TestGetters:
    def test_provider_name(self, provider):
        assert provider.get_provider_name() == "matrix"

    def test_channel_id(self, provider):
        assert provider.get_channel_id() == "!room:matrix.example"


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @patch("app.messaging.matrix.requests.put")
    def test_short_message(self, mock_put, provider):
        mock_put.return_value = MagicMock(status_code=200)
        assert provider.send_message("hello") is True
        assert mock_put.call_count == 1
        call = mock_put.call_args
        assert call[1]["json"]["body"] == "hello"
        assert call[1]["json"]["msgtype"] == "m.text"
        assert call[1]["headers"]["Authorization"] == "Bearer syt_token"

    @patch("app.messaging.matrix.requests.put")
    def test_long_message_chunked(self, mock_put, provider):
        mock_put.return_value = MagicMock(status_code=200)
        assert provider.send_message("x" * 8500) is True
        assert mock_put.call_count == 3  # 4000 + 4000 + 500

    @patch("app.messaging.matrix.requests.put")
    def test_url_contains_url_encoded_room_id(self, mock_put, provider):
        mock_put.return_value = MagicMock(status_code=200)
        provider.send_message("hi")
        url = mock_put.call_args[0][0]
        # ! and : must be percent-encoded in the path segment
        assert "%21room%3Amatrix.example" in url
        assert "/send/m.room.message/" in url

    @patch("app.messaging.matrix.requests.put")
    def test_4xx_returns_false(self, mock_put, provider):
        mock_put.return_value = MagicMock(status_code=403, text="forbidden")
        assert provider.send_message("hi") is False

    @patch("app.messaging.matrix.time.sleep")
    @patch("app.messaging.matrix.requests.put")
    def test_5xx_retries_then_fails(self, mock_put, mock_sleep, provider):
        # 5xx raises RequestException → retried 3 times → final failure
        mock_put.return_value = MagicMock(status_code=502, text="bad gateway")
        assert provider.send_message("hi") is False
        assert mock_put.call_count == 3

    def test_not_configured(self):
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.send_message("test") is False

    def test_empty_message_noop(self, provider):
        # Empty messages don't hit the API, just succeed (matches Telegram behavior).
        with patch("app.messaging.matrix.requests.put") as mock_put:
            assert provider.send_message("") is True
            assert mock_put.call_count == 0

    @patch("app.messaging.matrix.requests.put")
    def test_intentional_repeat_sends_both(self, mock_put, provider):
        """Two intentional sends of identical text must produce two distinct events.

        The per-send counter in the txn_id hash ensures the second send is not
        silently dropped by Matrix's idempotency dedup."""
        mock_put.return_value = MagicMock(status_code=200)

        provider.send_message("same body")
        provider.send_message("same body")
        txns = [c[0][0].rsplit("/", 1)[-1] for c in mock_put.call_args_list]
        assert txns[0] != txns[1]

    @patch("app.messaging.matrix.requests.put")
    def test_txn_id_differs_for_different_content(self, mock_put, provider):
        """Different bodies must get different transaction ids (no false dedup)."""
        mock_put.return_value = MagicMock(status_code=200)

        provider.send_message("body A")
        txn_a = mock_put.call_args[0][0].rsplit("/", 1)[-1]

        mock_put.reset_mock()
        provider.send_message("body B")
        txn_b = mock_put.call_args[0][0].rsplit("/", 1)[-1]

        assert txn_a != txn_b

    @patch("app.messaging.matrix.requests.put")
    def test_chunks_get_distinct_txn_ids(self, mock_put, provider):
        """Chunks of one message must not collide on txn id (would drop a chunk)."""
        mock_put.return_value = MagicMock(status_code=200)
        provider.send_message("x" * 8500)  # 3 chunks
        txns = [c[0][0].rsplit("/", 1)[-1] for c in mock_put.call_args_list]
        assert len(txns) == 3
        assert len(set(txns)) == 3


# ---------------------------------------------------------------------------
# poll_updates / sync
# ---------------------------------------------------------------------------


class TestPollUpdates:
    @patch("app.messaging.matrix.requests.get")
    def test_initial_sync_discards_events(self, mock_get, provider):
        """First sync only records next_batch — historical events are ignored."""
        mock_get.return_value = MagicMock(json=lambda: {
            "next_batch": "s100",
            "rooms": {"join": {"!room:matrix.example": {
                "timeline": {"events": [
                    {"type": "m.room.message", "sender": "@alice:matrix.example",
                     "content": {"msgtype": "m.text", "body": "old msg"}},
                ]}
            }}}
        })
        updates = provider.poll_updates()
        assert updates == []
        assert provider._sync_token == "s100"
        assert provider._sync_initialized is True

    @patch("app.messaging.matrix.requests.get")
    def test_subsequent_sync_returns_messages(self, mock_get, provider):
        provider._sync_token = "s100"
        provider._sync_initialized = True
        mock_get.return_value = MagicMock(json=lambda: {
            "next_batch": "s101",
            "rooms": {"join": {"!room:matrix.example": {
                "timeline": {"events": [
                    {"type": "m.room.message", "sender": "@alice:matrix.example",
                     "content": {"msgtype": "m.text", "body": "hello bot"},
                     "origin_server_ts": 123},
                ]}
            }}}
        })
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "hello bot"
        assert updates[0].message.role == "user"
        assert provider._sync_token == "s101"

    @patch("app.messaging.matrix.requests.get")
    def test_filters_own_messages(self, mock_get, provider):
        provider._sync_token = "s100"
        provider._sync_initialized = True
        mock_get.return_value = MagicMock(json=lambda: {
            "next_batch": "s101",
            "rooms": {"join": {"!room:matrix.example": {
                "timeline": {"events": [
                    {"type": "m.room.message", "sender": "@koan:matrix.example",
                     "content": {"msgtype": "m.text", "body": "self"}},
                    {"type": "m.room.message", "sender": "@alice:matrix.example",
                     "content": {"msgtype": "m.text", "body": "from alice"}},
                ]}
            }}}
        })
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "from alice"

    @patch("app.messaging.matrix.requests.get")
    def test_filters_non_text_messages(self, mock_get, provider):
        provider._sync_token = "s100"
        provider._sync_initialized = True
        mock_get.return_value = MagicMock(json=lambda: {
            "next_batch": "s101",
            "rooms": {"join": {"!room:matrix.example": {
                "timeline": {"events": [
                    {"type": "m.room.message", "sender": "@alice:matrix.example",
                     "content": {"msgtype": "m.image", "body": "photo.png"}},
                    {"type": "m.room.member", "sender": "@bob:matrix.example",
                     "content": {"membership": "join"}},
                    {"type": "m.room.message", "sender": "@alice:matrix.example",
                     "content": {"msgtype": "m.text", "body": "real msg"}},
                ]}
            }}}
        })
        updates = provider.poll_updates()
        assert len(updates) == 1
        assert updates[0].message.text == "real msg"

    @patch("app.messaging.matrix.requests.get")
    def test_ignores_events_from_other_rooms(self, mock_get, provider):
        provider._sync_token = "s100"
        provider._sync_initialized = True
        mock_get.return_value = MagicMock(json=lambda: {
            "next_batch": "s101",
            "rooms": {"join": {"!other:matrix.example": {
                "timeline": {"events": [
                    {"type": "m.room.message", "sender": "@alice:matrix.example",
                     "content": {"msgtype": "m.text", "body": "wrong room"}},
                ]}
            }}}
        })
        updates = provider.poll_updates()
        assert updates == []

    @patch("app.messaging.matrix.requests.get")
    def test_network_error_returns_empty(self, mock_get, provider):
        mock_get.side_effect = requests.RequestException("boom")
        assert provider.poll_updates() == []

    @patch("app.messaging.matrix.requests.get")
    def test_passes_since_token_after_init(self, mock_get, provider):
        provider._sync_token = "s100"
        provider._sync_initialized = True
        mock_get.return_value = MagicMock(json=lambda: {"next_batch": "s101"})
        provider.poll_updates()
        assert mock_get.call_args[1]["params"]["since"] == "s100"

    @patch("app.messaging.matrix.requests.get")
    def test_initial_sync_uses_zero_timeout(self, mock_get, provider):
        mock_get.return_value = MagicMock(json=lambda: {"next_batch": "s100"})
        provider.poll_updates()
        assert mock_get.call_args[1]["params"]["timeout"] == 0
        assert "since" not in mock_get.call_args[1]["params"]

    def test_no_token_returns_empty(self):
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.poll_updates() == []


# ---------------------------------------------------------------------------
# send_typing
# ---------------------------------------------------------------------------


class TestSendTyping:
    @patch("app.messaging.matrix.requests.put")
    def test_send_typing(self, mock_put, provider):
        mock_put.return_value = MagicMock(status_code=200)
        assert provider.send_typing() is True
        url = mock_put.call_args[0][0]
        assert "/typing/" in url
        assert mock_put.call_args[1]["json"]["typing"] is True

    def test_send_typing_not_configured(self):
        from app.messaging.matrix import MatrixProvider
        p = MatrixProvider()
        assert p.send_typing() is False

    @patch("app.messaging.matrix.requests.put")
    def test_send_typing_network_error(self, mock_put, provider):
        mock_put.side_effect = requests.RequestException("boom")
        assert provider.send_typing() is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_matrix_registered(self):
        """Matrix should auto-register when the messaging package loads providers.

        Runs in a fresh subprocess: ``_providers`` is process-wide module
        state that other tests (notably ``clean_registry`` in
        ``test_messaging_provider.py``) can clear *after* the provider
        modules are already cached in ``sys.modules``.  Once that happens
        no in-process call to ``_ensure_providers_loaded`` can repopulate
        the registry — the decorators won't re-run for cached modules.
        A clean subprocess sidesteps the whole ordering problem.
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        koan_pkg = Path(__file__).resolve().parents[1]  # …/koan
        script = (
            "from app.messaging import _ensure_providers_loaded, _providers\n"
            "_ensure_providers_loaded()\n"
            "assert 'matrix' in _providers, sorted(_providers)\n"
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
