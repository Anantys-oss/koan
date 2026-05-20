"""GitHub webhook receiver — push-based notification triggering.

GitHub's REST notifications API offers no push/streaming mechanism, so Kōan
normally *polls* for @mentions (throttled to 60-180s with exponential backoff).
That polling delay is what makes the bot feel slow to respond.

This module adds an opt-in **webhook receiver**: GitHub pushes events to a local
HTTP endpoint, which writes the ``.koan-check-notifications`` signal so the run
loop performs an immediate forced poll (within ~10s) instead of waiting out the
backoff.

The webhook is a *latency trigger*, not a replacement for polling. It does NOT
parse @mentions itself — it reuses the full, robust polling pipeline (dedup,
permission checks, mission creation) by reusing the same signal that
``/check_notifications`` writes. Polling remains the reconciliation fallback for
any webhook delivery that is dropped, retried, or missed. "Webhook for latency,
poll for reliability."

Requires a publicly reachable endpoint. Behind NAT, front it with a tunnel
(smee.io, cloudflared, ngrok) that forwards to ``127.0.0.1:<port>``. Configure
the webhook in the GitHub repo settings with content type ``application/json``
and the same secret as ``KOAN_GITHUB_WEBHOOK_SECRET``.

No third-party dependencies — stdlib ``http.server`` only.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Set

from app.signals import CHECK_NOTIFICATIONS_FILE

log = logging.getLogger(__name__)

# Default port for the receiver. Chosen to avoid collision with the dashboard
# (5001) and common dev servers.
DEFAULT_WEBHOOK_PORT = 8474

# Default bind host. 127.0.0.1 is the safe default: tunnels (smee/cloudflared)
# run on the same host and forward to localhost, so the receiver never needs to
# be directly internet-exposed. Override with host="0.0.0.0" only for direct
# exposure behind your own TLS terminator.
DEFAULT_WEBHOOK_HOST = "127.0.0.1"

# Reject bodies larger than this to prevent memory exhaustion. GitHub caps
# webhook payloads at 25 MB.
MAX_BODY_BYTES = 25 * 1024 * 1024

# Webhook event types that may carry an @mention / assignment the poller acts
# on. For "issues" and "pull_request" we additionally filter on the action so a
# label/sync/push edit doesn't trigger a needless poll. Everything here is
# re-validated by the actual poll — this filter only reduces noise.
_COMMENT_EVENTS = frozenset({
    "issue_comment",
    "pull_request_review_comment",
    "pull_request_review",
    "commit_comment",
})
_ISSUE_ACTIONS = frozenset({"assigned"})
_PR_ACTIONS = frozenset({"assigned", "review_requested"})


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """Verify a GitHub webhook HMAC-SHA256 signature.

    Args:
        payload: The raw request body bytes (verified as-received, before any
            parsing — re-serializing JSON would change the bytes and break the
            HMAC).
        signature_header: The ``X-Hub-Signature-256`` header value, formatted
            as ``sha256=<hexdigest>``.
        secret: The shared webhook secret.

    Returns:
        True if the signature is present, well-formed, and matches.
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    # Constant-time comparison to avoid timing attacks.
    return hmac.compare_digest(expected, provided)


def extract_repo_full_name(payload: dict) -> str:
    """Return the lowercase ``owner/repo`` from a webhook payload, or ""."""
    repo = payload.get("repository")
    if not isinstance(repo, dict):
        return ""
    full_name = repo.get("full_name") or ""
    return full_name.lower()


def is_actionable_event(event_type: str, payload: dict) -> bool:
    """Decide whether a webhook event should trigger a notification poll.

    The poll re-validates everything (dedup, permissions, @mention parsing), so
    this filter is purely about avoiding pointless polls on irrelevant events.
    """
    if event_type in _COMMENT_EVENTS:
        # Only act on newly created/submitted comments, not edits/deletes.
        action = payload.get("action")
        if action is None:
            return True
        return action in {"created", "submitted"}
    action = payload.get("action")
    if event_type == "issues":
        return action in _ISSUE_ACTIONS
    if event_type == "pull_request":
        return action in _PR_ACTIONS
    return False


def should_trigger(event_type: str, payload: dict,
                   known_repos: Optional[Set[str]]) -> bool:
    """Combine repo filtering and event filtering into a single decision."""
    if not is_actionable_event(event_type, payload):
        return False
    if known_repos:
        repo = extract_repo_full_name(payload)
        if repo and repo not in known_repos:
            return False
    return True


def write_check_signal(koan_root: str) -> bool:
    """Write the check-notifications signal file to force an immediate poll.

    Mirrors the ``/check_notifications`` skill: the run loop consumes this file
    on its next sleep-cycle check (within ~10s) and bypasses the polling
    backoff.

    Returns:
        True if the signal file was written.
    """
    signal_path = os.path.join(str(koan_root), CHECK_NOTIFICATIONS_FILE)
    try:
        with open(signal_path, "w") as f:
            f.write(f"github webhook at {time.strftime('%H:%M:%S')}\n")
        return True
    except OSError as e:
        log.warning("Webhook: failed to write check-notifications signal: %s", e)
        return False


def handle_event(event_type: str, payload: dict, koan_root: str,
                 known_repos: Optional[Set[str]]) -> bool:
    """Process a parsed webhook event; trigger a poll if relevant.

    Returns:
        True if a poll was triggered (signal written), False otherwise.
    """
    if not should_trigger(event_type, payload, known_repos):
        log.debug(
            "Webhook: ignoring event=%s action=%s repo=%s",
            event_type, payload.get("action"), extract_repo_full_name(payload),
        )
        return False

    wrote = write_check_signal(koan_root)
    if wrote:
        log.info(
            "Webhook: %s on %s → triggered immediate notification poll",
            event_type, extract_repo_full_name(payload) or "?",
        )
    return wrote


def _make_handler(secret: str, koan_root: str,
                  known_repos: Optional[Set[str]]):
    """Build a BaseHTTPRequestHandler subclass bound to the given config."""

    class _WebhookHandler(BaseHTTPRequestHandler):
        # Quieten the default stderr access log; route through our logger.
        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            log.debug("Webhook HTTP: " + fmt, *args)

        def _respond(self, code: int, body: str = "") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_POST(self):  # noqa: N802 - stdlib method name
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                self._respond(400, "bad content-length")
                return
            if length <= 0:
                self._respond(400, "empty body")
                return
            if length > MAX_BODY_BYTES:
                self._respond(413, "payload too large")
                return

            payload_bytes = self.rfile.read(length)

            signature = self.headers.get("X-Hub-Signature-256", "")
            if not verify_signature(payload_bytes, signature, secret):
                log.warning("Webhook: rejected request with invalid signature")
                self._respond(401, "invalid signature")
                return

            event_type = self.headers.get("X-GitHub-Event", "")
            if event_type == "ping":
                self._respond(200, "pong")
                return

            try:
                payload = json.loads(payload_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._respond(400, "invalid json")
                return
            if not isinstance(payload, dict):
                self._respond(400, "unexpected payload")
                return

            try:
                handle_event(event_type, payload, koan_root, known_repos)
            except Exception as e:  # never 500 on internal handling errors
                log.warning("Webhook: handler error for %s: %s", event_type, e)

            # Always 202 once authenticated — don't leak which repos/events are
            # actionable to a holder of the secret.
            self._respond(202, "accepted")

        def do_GET(self):  # noqa: N802 - health probe convenience
            self._respond(200, "koan github webhook receiver")

    return _WebhookHandler


def create_server(koan_root: str, secret: str,
                  port: int = DEFAULT_WEBHOOK_PORT,
                  host: str = DEFAULT_WEBHOOK_HOST,
                  known_repos: Optional[Set[str]] = None) -> ThreadingHTTPServer:
    """Create (but do not start) the webhook HTTP server.

    Raises:
        ValueError: if no secret is provided. The server must never run without
            signature verification.
    """
    if not secret:
        raise ValueError(
            "Refusing to start webhook server without a secret. "
            "Set KOAN_GITHUB_WEBHOOK_SECRET."
        )
    handler_cls = _make_handler(secret, str(koan_root), known_repos)
    server = ThreadingHTTPServer((host, port), handler_cls)
    return server


def start_webhook_server(koan_root: str, secret: str,
                         port: int = DEFAULT_WEBHOOK_PORT,
                         host: str = DEFAULT_WEBHOOK_HOST,
                         known_repos: Optional[Set[str]] = None,
                         background: bool = True) -> ThreadingHTTPServer:
    """Start the webhook receiver.

    When ``background`` is True (default), serves in a daemon thread and returns
    immediately — suitable for embedding in the bridge process. Otherwise the
    caller is responsible for calling ``serve_forever()``.

    Returns:
        The HTTPServer instance.
    """
    server = create_server(koan_root, secret, port=port, host=host,
                           known_repos=known_repos)
    if background:
        thread = threading.Thread(
            target=server.serve_forever,
            name="github-webhook",
            daemon=True,
        )
        thread.start()
        log.info("GitHub webhook receiver listening on %s:%d", host, port)
    return server


def maybe_start_from_config(koan_root: str) -> Optional[ThreadingHTTPServer]:
    """Start the webhook server if enabled in config and a secret is present.

    Intended to be called once at bridge startup. Returns the running server,
    or None if disabled / misconfigured (logged, never raises).
    """
    try:
        from app.github_config import (
            get_github_webhook_enabled,
            get_github_webhook_host,
            get_github_webhook_port,
        )
        from app.utils import load_config

        config = load_config()
        if not get_github_webhook_enabled(config):
            return None

        secret = os.environ.get("KOAN_GITHUB_WEBHOOK_SECRET", "").strip()
        if not secret:
            log.warning(
                "GitHub webhook enabled but KOAN_GITHUB_WEBHOOK_SECRET is unset "
                "— receiver not started. Polling remains active."
            )
            return None

        port = get_github_webhook_port(config)
        host = get_github_webhook_host(config)
        known_repos = _resolve_known_repos(koan_root)

        return start_webhook_server(
            koan_root, secret, port=port, host=host,
            known_repos=known_repos, background=True,
        )
    except OSError as e:
        log.warning("GitHub webhook receiver could not bind: %s", e)
        return None
    except Exception as e:
        log.warning("GitHub webhook receiver failed to start: %s", e)
        return None


def _resolve_known_repos(koan_root: str) -> Optional[Set[str]]:
    """Reuse the poller's known-repo set so filtering stays consistent."""
    try:
        from app.loop_manager import _get_known_repos_from_projects
        return _get_known_repos_from_projects(koan_root)
    except Exception as e:
        log.debug("Webhook: could not resolve known repos: %s", e)
        return None


def main() -> int:
    """Run the receiver in the foreground (standalone process).

    Used by ``make webhook``. Reads config + KOAN_GITHUB_WEBHOOK_SECRET, then
    serves until interrupted.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    koan_root = os.environ.get("KOAN_ROOT", "")
    if not koan_root:
        log.error("KOAN_ROOT is not set")
        return 1

    secret = os.environ.get("KOAN_GITHUB_WEBHOOK_SECRET", "").strip()
    if not secret:
        log.error("KOAN_GITHUB_WEBHOOK_SECRET is not set")
        return 1

    from app.github_config import get_github_webhook_host, get_github_webhook_port
    from app.utils import load_config

    config = load_config()
    port = get_github_webhook_port(config)
    host = get_github_webhook_host(config)
    known_repos = _resolve_known_repos(koan_root)

    server = create_server(koan_root, secret, port=port, host=host,
                           known_repos=known_repos)
    log.info("GitHub webhook receiver listening on %s:%d (Ctrl-C to stop)",
             host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down webhook receiver")
        server.shutdown()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
