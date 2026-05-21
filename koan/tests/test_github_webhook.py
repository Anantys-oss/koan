"""Tests for the GitHub webhook receiver (push-based notification triggering).

Covers:
- HMAC-SHA256 signature verification (valid / invalid / missing / malformed)
- Event + repo filtering (which events trigger an immediate poll)
- The check-notifications signal file is written on a triggering event
- End-to-end HTTP behavior: signature rejection, ping, and event handling
- Config helpers (enabled flag, port, host) and refusal to start without a secret
"""

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

import pytest

from app.signals import CHECK_NOTIFICATIONS_FILE

SECRET = "s3cr3t-test-key"


def _sign(payload: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# --- signature verification --------------------------------------------------


class TestVerifySignature:
    def test_valid_signature_accepted(self):
        from app.github_webhook import verify_signature

        body = b'{"hello":"world"}'
        assert verify_signature(body, _sign(body), SECRET) is True

    def test_wrong_secret_rejected(self):
        from app.github_webhook import verify_signature

        body = b'{"hello":"world"}'
        assert verify_signature(body, _sign(body, "other"), SECRET) is False

    def test_tampered_body_rejected(self):
        from app.github_webhook import verify_signature

        sig = _sign(b'{"hello":"world"}')
        assert verify_signature(b'{"hello":"evil"}', sig, SECRET) is False

    def test_missing_signature_rejected(self):
        from app.github_webhook import verify_signature

        assert verify_signature(b"x", "", SECRET) is False

    def test_missing_secret_rejected(self):
        from app.github_webhook import verify_signature

        body = b"x"
        assert verify_signature(body, _sign(body), "") is False

    def test_malformed_header_rejected(self):
        from app.github_webhook import verify_signature

        body = b"x"
        # No "sha256=" prefix → reject without crashing.
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(body, digest, SECRET) is False


# --- event / repo filtering --------------------------------------------------


class TestEventFiltering:
    def test_issue_comment_created_is_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event(
            "issue_comment", {"action": "created"}
        ) is True

    def test_issue_comment_deleted_not_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event(
            "issue_comment", {"action": "deleted"}
        ) is False

    def test_review_comment_created_is_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event(
            "pull_request_review_comment", {"action": "created"}
        ) is True

    def test_review_submitted_is_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event(
            "pull_request_review", {"action": "submitted"}
        ) is True

    def test_issue_assigned_is_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event("issues", {"action": "assigned"}) is True

    def test_issue_labeled_not_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event("issues", {"action": "labeled"}) is False

    def test_pr_review_requested_is_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event(
            "pull_request", {"action": "review_requested"}
        ) is True

    def test_pr_synchronize_not_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event(
            "pull_request", {"action": "synchronize"}
        ) is False

    def test_push_event_not_actionable(self):
        from app.github_webhook import is_actionable_event

        assert is_actionable_event("push", {}) is False

    def test_should_trigger_filters_unknown_repo(self):
        from app.github_webhook import should_trigger

        payload = {
            "action": "created",
            "repository": {"full_name": "stranger/repo"},
        }
        assert should_trigger(
            "issue_comment", payload, {"owner/known"}
        ) is False

    def test_should_trigger_allows_known_repo(self):
        from app.github_webhook import should_trigger

        payload = {
            "action": "created",
            "repository": {"full_name": "Owner/Known"},
        }
        # Known-repo set is lowercased; full_name comparison must be too.
        assert should_trigger(
            "issue_comment", payload, {"owner/known"}
        ) is True

    def test_should_trigger_no_known_repos_allows_all(self):
        from app.github_webhook import should_trigger

        payload = {"action": "created", "repository": {"full_name": "a/b"}}
        assert should_trigger("issue_comment", payload, None) is True


# --- signal writing ----------------------------------------------------------


class TestSignalWriting:
    def test_handle_event_writes_signal(self, tmp_path):
        from app.github_webhook import handle_event

        payload = {"action": "created", "repository": {"full_name": "a/b"}}
        wrote = handle_event("issue_comment", payload, str(tmp_path), None)

        assert wrote is True
        assert (tmp_path / CHECK_NOTIFICATIONS_FILE).exists()

    def test_handle_event_skips_non_actionable(self, tmp_path):
        from app.github_webhook import handle_event

        payload = {"action": "labeled", "repository": {"full_name": "a/b"}}
        wrote = handle_event("issues", payload, str(tmp_path), None)

        assert wrote is False
        assert not (tmp_path / CHECK_NOTIFICATIONS_FILE).exists()

    def test_write_check_signal_contents(self, tmp_path):
        from app.github_webhook import write_check_signal

        assert write_check_signal(str(tmp_path)) is True
        content = (tmp_path / CHECK_NOTIFICATIONS_FILE).read_text()
        assert "github webhook" in content


# --- config helpers ----------------------------------------------------------


class TestConfigHelpers:
    def test_webhook_disabled_by_default(self):
        from app.github_config import get_github_webhook_enabled

        assert get_github_webhook_enabled({}) is False
        assert get_github_webhook_enabled({"github": {}}) is False

    def test_webhook_enabled_flag(self):
        from app.github_config import get_github_webhook_enabled

        cfg = {"github": {"webhook": {"enabled": True}}}
        assert get_github_webhook_enabled(cfg) is True

    def test_webhook_port_default_and_override(self):
        from app.github_webhook import DEFAULT_WEBHOOK_PORT
        from app.github_config import get_github_webhook_port

        assert get_github_webhook_port({}) == DEFAULT_WEBHOOK_PORT
        cfg = {"github": {"webhook": {"port": 9999}}}
        assert get_github_webhook_port(cfg) == 9999

    def test_webhook_port_invalid_falls_back(self):
        from app.github_webhook import DEFAULT_WEBHOOK_PORT
        from app.github_config import get_github_webhook_port

        cfg = {"github": {"webhook": {"port": 70000}}}
        assert get_github_webhook_port(cfg) == DEFAULT_WEBHOOK_PORT
        cfg = {"github": {"webhook": {"port": "nope"}}}
        assert get_github_webhook_port(cfg) == DEFAULT_WEBHOOK_PORT

    def test_webhook_host_default_is_loopback(self):
        from app.github_config import get_github_webhook_host

        assert get_github_webhook_host({}) == "127.0.0.1"
        cfg = {"github": {"webhook": {"host": "0.0.0.0"}}}
        assert get_github_webhook_host(cfg) == "0.0.0.0"


class TestCreateServerGuard:
    def test_refuses_without_secret(self, tmp_path):
        from app.github_webhook import create_server

        with pytest.raises(ValueError):
            create_server(str(tmp_path), "")


# --- end-to-end HTTP ---------------------------------------------------------


@pytest.fixture
def running_server(tmp_path):
    """Start a real receiver on an ephemeral port, yield (base_url, koan_root)."""
    from app.github_webhook import start_webhook_server

    server = start_webhook_server(
        str(tmp_path), SECRET, port=0, host="127.0.0.1",
        known_repos={"owner/known"}, background=True,
    )
    host, port = server.server_address
    try:
        yield f"http://127.0.0.1:{port}", tmp_path
    finally:
        server.shutdown()
        server.server_close()


def _post(url, body: bytes, headers: dict):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestHttpEndToEnd:
    def test_invalid_signature_returns_401(self, running_server):
        base_url, koan_root = running_server
        body = json.dumps({"repository": {"full_name": "owner/known"},
                           "action": "created"}).encode()
        status, _ = _post(base_url, body, {
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        })
        assert status == 401
        assert not (koan_root / CHECK_NOTIFICATIONS_FILE).exists()

    def test_ping_returns_pong(self, running_server):
        base_url, _ = running_server
        body = b'{"zen":"hi"}'
        status, data = _post(base_url, body, {
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        })
        assert status == 200
        assert b"pong" in data

    def test_valid_event_triggers_signal(self, running_server):
        base_url, koan_root = running_server
        body = json.dumps({"repository": {"full_name": "owner/known"},
                           "action": "created"}).encode()
        status, _ = _post(base_url, body, {
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        })
        assert status == 202
        assert (koan_root / CHECK_NOTIFICATIONS_FILE).exists()

    def test_unknown_repo_authenticated_but_no_signal(self, running_server):
        base_url, koan_root = running_server
        body = json.dumps({"repository": {"full_name": "stranger/repo"},
                           "action": "created"}).encode()
        status, _ = _post(base_url, body, {
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        })
        # Authenticated → 202, but the unknown repo means no poll trigger.
        assert status == 202
        assert not (koan_root / CHECK_NOTIFICATIONS_FILE).exists()
