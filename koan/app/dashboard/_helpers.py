"""Cross-cutting Flask wiring: passphrase gate, template filters, context processor.

All functions read patchable settings from :mod:`app.dashboard.state` at request
time so tests can patch them on a single, stable target.
"""
import contextlib
import hashlib
import os
from pathlib import Path

from flask import jsonify, redirect, request, session, url_for

from app.dashboard import state
from app.utils import PROJECT_TAG_FULL_RE

_LOGIN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kōan — Locked</title>
  <style>
    body { font-family: -apple-system, system-ui, sans-serif; background:#111; color:#eee;
           display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }
    form { background:#1b1b1b; padding:2rem; border-radius:12px; width:300px;
           box-shadow:0 8px 30px rgba(0,0,0,.5); }
    h1 { font-size:1.2rem; margin:0 0 1rem; text-align:center; }
    input { width:100%; padding:.6rem; margin:.4rem 0; box-sizing:border-box;
            border:1px solid #333; border-radius:6px; background:#222; color:#eee; }
    button { width:100%; padding:.6rem; margin-top:.6rem; border:0; border-radius:6px;
             background:#4a7; color:#000; font-weight:600; cursor:pointer; }
    .err { color:#e66; font-size:.85rem; text-align:center; min-height:1.2em; }
  </style>
</head>
<body>
  <form method="post" action="{{ url_for('core.login') }}">
    <h1>🧘 Kōan Dashboard</h1>
    <div class="err">{{ error }}</div>
    <input type="password" name="passphrase" placeholder="Passphrase" autofocus>
    <button type="submit">Unlock</button>
    <input type="hidden" name="next" value="{{ next_url }}">
  </form>
</body>
</html>"""


def is_authed() -> bool:
    """True when the current session has cleared the passphrase gate."""
    return bool(session.get("koan_dashboard_authed"))


def _shorten_url(url: str) -> str:
    """Return a short display label for known URL patterns, or the URL itself."""
    m = state._GITHUB_ISSUE_PR_RE.match(url)
    if m:
        return f'#{m.group(1)}'
    m = state._JIRA_BROWSE_RE.match(url)
    if m:
        return m.group(1)
    return url


def strip_project_tag_filter(text: str) -> str:
    """Remove [project:name] tag from mission text for display."""
    return PROJECT_TAG_FULL_RE.sub(' ', text).strip()


def project_badge_filter(text: str) -> str:
    """Extract project tag and return badge HTML, or empty string."""
    m = PROJECT_TAG_FULL_RE.search(text)
    if m:
        name = m.group(1)
        return f'<span class="k-badge k-badge--brand">{name}</span> '
    return ''


def linkify_filter(text: str) -> str:
    """Convert URLs in text to clickable links that open in a new tab."""
    from markupsafe import Markup, escape
    parts = state._URL_RE.split(str(escape(text)))
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            label = _shorten_url(part)
            out.append(f'<a href="{part}" target="_blank" rel="noopener noreferrer">{label}</a>')
        else:
            out.append(part)
    return Markup(''.join(out))


def configure_security(app) -> None:
    """Derive the session secret from the passphrase and harden cookies."""
    if state.DASHBOARD_PWD:
        # Derive a stable secret so sessions survive restarts.
        app.secret_key = hashlib.sha256(
            ("koan-dashboard-session:" + state.DASHBOARD_PWD).encode()
        ).digest()
    else:
        # No passphrase: random per-process key (sessions are unused).
        app.secret_key = os.urandom(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )


def register_helpers(app) -> None:
    """Attach the passphrase gate, cache-buster, filters, and context processor."""
    configure_security(app)

    @app.before_request
    def _require_passphrase():
        """Block every request unless authenticated. No-op when no passphrase set."""
        if not state.DASHBOARD_PWD:
            return None
        # Allow the login endpoint and static assets through unauthenticated.
        if request.endpoint in ("core.login", "core.logout", "static"):
            return None
        if is_authed():
            return None
        # Unauthenticated: HTML clients get the login page, API clients get 401.
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("core.login", next=request.full_path))

    @app.url_defaults
    def _static_cache_buster(endpoint, values):
        if endpoint == "static":
            filename = values.get("filename")
            if filename and not filename.endswith("/"):
                file_path = Path(app.static_folder) / filename
                with contextlib.suppress(OSError):
                    values["v"] = int(file_path.stat().st_mtime)

    @app.context_processor
    def _inject_instance_nickname():
        from app.config import get_dashboard_nickname
        return {"instance_nickname": get_dashboard_nickname()}

    app.add_template_filter(strip_project_tag_filter, name='strip_project_tag')
    app.add_template_filter(project_badge_filter, name='project_badge')
    app.add_template_filter(linkify_filter, name='linkify')
