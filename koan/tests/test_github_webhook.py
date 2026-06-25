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
import io
import json
import os
import socket
import urllib.error
import urllib.request

import pytest

from app.signals import CHECK_NOTIFICATIONS_FILE

SECRET = "s3cr3t-test-key"


def _sign(payload: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture(autouse=True)
def _reset_signal_debounce():
    """Clear the module-level debounce clock before each test.

    ``handle_event`` coalesces signal writes through a process-global timestamp;
    without this reset, a signal write in one test would suppress the next
    test's write and make order-dependent failures.
    """
    from app.github_webhook import reset_signal_debounce

    reset_signal_debounce()
    yield
    reset_signal_debounce()


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

    def test_should_trigger_empty_set_rejects_all(self):
        from app.github_webhook import should_trigger

        # Empty set (is not None) means "filter to nothing" — every repo is
        # rejected, distinct from None ("no filter").
        payload = {"action": "created", "repository": {"full_name": "a/b"}}
        assert should_trigger("issue_comment", payload, set()) is False


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


# --- debounce / rate limiting ------------------------------------------------


class TestSignalDebounce:
    def test_rapid_events_are_coalesced(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        clock = {"t": 1000.0}
        monkeypatch.setattr(wh.time, "monotonic", lambda: clock["t"])
        payload = {"action": "created", "repository": {"full_name": "a/b"}}
        signal = tmp_path / CHECK_NOTIFICATIONS_FILE

        # First delivery writes the signal.
        assert wh.handle_event("issue_comment", payload, str(tmp_path), None) is True
        assert signal.exists()
        signal.unlink()

        # Second delivery within the interval is debounced — no write.
        clock["t"] = 1001.0
        assert wh.handle_event("issue_comment", payload, str(tmp_path), None) is False
        assert not signal.exists()

    def test_signal_allowed_again_after_interval(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        clock = {"t": 2000.0}
        monkeypatch.setattr(wh.time, "monotonic", lambda: clock["t"])
        payload = {"action": "created", "repository": {"full_name": "a/b"}}
        signal = tmp_path / CHECK_NOTIFICATIONS_FILE

        assert wh.handle_event("issue_comment", payload, str(tmp_path), None) is True
        signal.unlink()

        # Past the interval → a fresh signal is written.
        clock["t"] = 2000.0 + wh.MIN_SIGNAL_INTERVAL + 0.1
        assert wh.handle_event("issue_comment", payload, str(tmp_path), None) is True
        assert signal.exists()


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

    def test_invalid_port_warns(self, caplog):
        import logging

        from app.github_webhook import DEFAULT_WEBHOOK_PORT
        from app.github_config import get_github_webhook_port

        cfg = {"github": {"webhook": {"port": 70000}}}
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            assert get_github_webhook_port(cfg) == DEFAULT_WEBHOOK_PORT
        assert any("github.webhook.port" in r.message for r in caplog.records)

    def test_valid_and_absent_port_do_not_warn(self, caplog):
        import logging

        from app.github_config import get_github_webhook_port

        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            get_github_webhook_port({})  # absent → silent default
            get_github_webhook_port({"github": {"webhook": {"port": 9999}}})  # valid
        assert not [r for r in caplog.records if "github.webhook.port" in r.message]

    def test_invalid_host_warns(self, caplog):
        import logging

        from app.github_config import get_github_webhook_host

        cfg = {"github": {"webhook": {"host": 0}}}  # non-string
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            assert get_github_webhook_host(cfg) == "127.0.0.1"
        assert any("github.webhook.host" in r.message for r in caplog.records)

    def test_empty_host_warns(self, caplog):
        import logging

        from app.github_config import get_github_webhook_host

        cfg = {"github": {"webhook": {"host": "   "}}}  # blank string
        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            assert get_github_webhook_host(cfg) == "127.0.0.1"
        assert any("github.webhook.host" in r.message for r in caplog.records)

    def test_absent_host_does_not_warn(self, caplog):
        import logging

        from app.github_config import get_github_webhook_host

        with caplog.at_level(logging.WARNING, logger="app.github_config"):
            get_github_webhook_host({})
            get_github_webhook_host({"github": {"webhook": {}}})
        assert not [r for r in caplog.records if "github.webhook.host" in r.message]


class TestCreateServerGuard:
    def test_refuses_without_secret(self, tmp_path):
        from app.github_webhook import create_server

        with pytest.raises(ValueError):
            create_server(str(tmp_path), "")


class TestHandlerHardening:
    def test_handler_has_request_timeout(self):
        from app.github_webhook import _make_handler

        cls = _make_handler(SECRET, "/tmp", None)
        # Bounds any single request so a slow client can't hold a worker thread.
        assert cls.timeout == 5

    def test_default_constants_shared_with_config(self):
        # Constants live in github_config and are re-exported from github_webhook;
        # both names must resolve to the same value (no circular import).
        from app import github_config, github_webhook

        assert github_webhook.DEFAULT_WEBHOOK_PORT == github_config.DEFAULT_WEBHOOK_PORT
        assert github_webhook.DEFAULT_WEBHOOK_HOST == github_config.DEFAULT_WEBHOOK_HOST


class TestConfigValidatorSchema:
    def test_github_webhook_dict_accepted(self):
        from app.config_validator import validate_config

        cfg = {"github": {"webhook": {"enabled": True, "port": 8474,
                                      "host": "127.0.0.1"}}}
        warnings = validate_config(cfg)
        # No "unrecognized key" warning for the webhook section.
        assert not [w for w in warnings if "webhook" in w[0]]


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

    def test_get_returns_generic_ok(self, running_server):
        base_url, _ = running_server
        with urllib.request.urlopen(base_url, timeout=5) as resp:
            status, data = resp.status, resp.read()
        # Generic body — must not fingerprint the service.
        assert status == 200
        assert data == b"ok"

    def test_incomplete_body_returns_400(self, running_server):
        base_url, koan_root = running_server
        host, port = base_url.rsplit(":", 1)
        port = int(port)
        # Advertise more bytes than we send, then half-close so the server's
        # read() returns short without blocking — exercises the length check.
        sent = b'{"partial":'
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"X-GitHub-Event: issue_comment\r\n"
            b"Content-Length: 500\r\n"
            b"\r\n" + sent
        )
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(raw)
            sock.shutdown(socket.SHUT_WR)
            resp = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp += chunk
        assert b"400" in resp.split(b"\r\n", 1)[0]
        assert not (koan_root / CHECK_NOTIFICATIONS_FILE).exists()


# --- pure-function edge cases ------------------------------------------------


class TestPureFunctionEdges:
    def test_extract_repo_full_name_non_dict_repository(self):
        from app.github_webhook import extract_repo_full_name

        # repository present but not a dict → "" (no crash).
        assert extract_repo_full_name({"repository": "oops"}) == ""
        assert extract_repo_full_name({}) == ""

    def test_extract_repo_full_name_missing_full_name(self):
        from app.github_webhook import extract_repo_full_name

        assert extract_repo_full_name({"repository": {}}) == ""

    def test_comment_event_without_action_is_actionable(self):
        from app.github_webhook import is_actionable_event

        # Some comment events omit "action"; treat absence as actionable so the
        # poll re-validates rather than silently dropping the delivery.
        assert is_actionable_event("commit_comment", {}) is True

    def test_write_check_signal_oswrite_failure_returns_false(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(wh, "atomic_write", boom)
        assert wh.write_check_signal(str(tmp_path)) is False

    def test_handle_event_returns_false_when_signal_not_written(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        # handle_event propagates the debounced-write result: when the write
        # reports failure (False), handle_event returns False for an otherwise
        # actionable event.
        monkeypatch.setattr(wh, "write_check_signal_debounced",
                            lambda _root: False)
        payload = {"action": "created", "repository": {"full_name": "a/b"}}
        assert wh.handle_event("issue_comment", payload, str(tmp_path), None) is False


# --- do_POST request-validation branches (unit, no socket) -------------------


def _post_handler(headers: dict, body: bytes, *, secret: str = SECRET,
                  koan_root: str = "/tmp", known_repos=None):
    """Construct a _WebhookHandler bypassing socketserver wiring.

    Returns (handler, captured) where ``captured`` records the (code, body)
    passed to ``_respond`` — letting us assert the HTTP outcome of each
    do_POST validation branch without binding a real socket.
    """
    from app.github_webhook import _make_handler

    cls = _make_handler(secret, str(koan_root), known_repos)
    handler = cls.__new__(cls)
    handler.headers = headers
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    captured = {}

    def _respond(code, resp_body=""):
        captured["code"] = code
        captured["body"] = resp_body

    handler._respond = _respond
    return handler, captured


class TestDoPostValidation:
    def test_bad_content_length_returns_400(self):
        handler, captured = _post_handler({"Content-Length": "not-a-number"}, b"x")
        handler.do_POST()
        assert captured["code"] == 400

    def test_empty_body_returns_400(self):
        handler, captured = _post_handler({"Content-Length": "0"}, b"")
        handler.do_POST()
        assert captured["code"] == 400

    def test_oversized_body_returns_413(self):
        from app.github_webhook import MAX_BODY_BYTES

        handler, captured = _post_handler(
            {"Content-Length": str(MAX_BODY_BYTES + 1)}, b"")
        handler.do_POST()
        assert captured["code"] == 413

    def test_incomplete_body_returns_400(self):
        # Advertise more than the buffer holds → short read → 400.
        handler, captured = _post_handler({"Content-Length": "100"}, b"short")
        handler.do_POST()
        assert captured["code"] == 400

    def test_invalid_json_returns_400(self):
        body = b"not json at all"
        handler, captured = _post_handler(
            {"Content-Length": str(len(body)),
             "X-Hub-Signature-256": _sign(body),
             "X-GitHub-Event": "issue_comment"},
            body)
        handler.do_POST()
        assert captured["code"] == 400
        assert "json" in captured["body"]

    def test_non_dict_payload_returns_400(self):
        body = b"[1, 2, 3]"  # valid JSON, but not an object
        handler, captured = _post_handler(
            {"Content-Length": str(len(body)),
             "X-Hub-Signature-256": _sign(body),
             "X-GitHub-Event": "issue_comment"},
            body)
        handler.do_POST()
        assert captured["code"] == 400

    def test_handler_error_still_returns_202(self, monkeypatch):
        import app.github_webhook as wh

        # An exception inside handle_event must be swallowed → still 202.
        def boom(*_a, **_k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(wh, "handle_event", boom)
        body = json.dumps({"action": "created"}).encode()
        handler, captured = _post_handler(
            {"Content-Length": str(len(body)),
             "X-Hub-Signature-256": _sign(body),
             "X-GitHub-Event": "issue_comment"},
            body)
        handler.do_POST()
        assert captured["code"] == 202


# --- maybe_start_from_config / _resolve_known_repos --------------------------


class TestMaybeStartFromConfig:
    def test_disabled_returns_none(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setattr("app.utils.load_config", lambda: {})
        monkeypatch.setattr("app.github_config.get_github_webhook_enabled",
                            lambda _cfg: False)
        assert wh.maybe_start_from_config(str(tmp_path)) is None

    def test_enabled_but_no_secret_returns_none(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setattr("app.utils.load_config", lambda: {})
        monkeypatch.setattr("app.github_config.get_github_webhook_enabled",
                            lambda _cfg: True)
        monkeypatch.delenv("KOAN_GITHUB_WEBHOOK_SECRET", raising=False)
        assert wh.maybe_start_from_config(str(tmp_path)) is None

    def test_enabled_with_secret_starts_server(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setattr("app.utils.load_config", lambda: {})
        monkeypatch.setattr("app.github_config.get_github_webhook_enabled",
                            lambda _cfg: True)
        monkeypatch.setattr("app.github_config.get_github_webhook_port",
                            lambda _cfg: 0)
        monkeypatch.setattr("app.github_config.get_github_webhook_host",
                            lambda _cfg: "127.0.0.1")
        monkeypatch.setattr(wh, "_resolve_known_repos", lambda _root: None)
        monkeypatch.setenv("KOAN_GITHUB_WEBHOOK_SECRET", SECRET)

        server = wh.maybe_start_from_config(str(tmp_path))
        assert server is not None
        try:
            assert server.server_address[1] != 0  # bound to a real port
        finally:
            server.shutdown()
            server.server_close()

    def test_exception_returns_none(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        def boom():
            raise RuntimeError("config blew up")

        monkeypatch.setattr("app.utils.load_config", boom)
        monkeypatch.setattr("app.github_config.get_github_webhook_enabled",
                            lambda _cfg: True)
        assert wh.maybe_start_from_config(str(tmp_path)) is None

    def test_bind_failure_returns_none(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setattr("app.utils.load_config", lambda: {})
        monkeypatch.setattr("app.github_config.get_github_webhook_enabled",
                            lambda _cfg: True)
        monkeypatch.setattr("app.github_config.get_github_webhook_port",
                            lambda _cfg: 0)
        monkeypatch.setattr("app.github_config.get_github_webhook_host",
                            lambda _cfg: "127.0.0.1")
        monkeypatch.setattr(wh, "_resolve_known_repos", lambda _root: None)
        monkeypatch.setenv("KOAN_GITHUB_WEBHOOK_SECRET", SECRET)

        def cannot_bind(*_a, **_k):
            raise OSError("address already in use")

        monkeypatch.setattr(wh, "start_webhook_server", cannot_bind)
        assert wh.maybe_start_from_config(str(tmp_path)) is None


class TestResolveKnownRepos:
    def test_returns_loop_manager_result(self, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setattr(
            "app.loop_manager.get_known_repos_from_projects",
            lambda _root: {"owner/repo"},
        )
        assert wh._resolve_known_repos("/koan") == {"owner/repo"}

    def test_swallows_exception_returns_none(self, monkeypatch):
        import app.github_webhook as wh

        def boom(_root):
            raise RuntimeError("no projects")

        monkeypatch.setattr(
            "app.loop_manager.get_known_repos_from_projects", boom)
        assert wh._resolve_known_repos("/koan") is None


# --- main() (standalone entrypoint) ------------------------------------------


class TestMain:
    def test_missing_koan_root_returns_1(self, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.delenv("KOAN_ROOT", raising=False)
        assert wh.main() == 1

    def test_missing_secret_returns_1(self, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setenv("KOAN_ROOT", "/tmp")
        monkeypatch.delenv("KOAN_GITHUB_WEBHOOK_SECRET", raising=False)
        assert wh.main() == 1

    def test_serves_then_stops_on_keyboard_interrupt(self, tmp_path, monkeypatch):
        import app.github_webhook as wh

        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        monkeypatch.setenv("KOAN_GITHUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr("app.utils.load_config", lambda: {})
        monkeypatch.setattr("app.github_config.get_github_webhook_port",
                            lambda _cfg: 0)
        monkeypatch.setattr("app.github_config.get_github_webhook_host",
                            lambda _cfg: "127.0.0.1")
        monkeypatch.setattr(wh, "_resolve_known_repos", lambda _root: None)

        captured = {}

        class _FakeServer:
            def serve_forever(self):
                raise KeyboardInterrupt
            def shutdown(self):
                captured["shutdown"] = True

        monkeypatch.setattr(wh, "create_server",
                            lambda *a, **k: _FakeServer())
        assert wh.main() == 0
        assert captured.get("shutdown") is True
