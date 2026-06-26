"""Kōan — Local Dashboard (Flask blueprint package).

Flask web app for monitoring and interacting with Kōan, built by a
``create_app()`` factory that mirrors ``app.api.__init__``. Routes are split into
per-domain blueprints; pure business logic lives in :mod:`app.dashboard_service`;
patchable paths/caches/regexes live in :mod:`app.dashboard.state`.

Run with ``python app/dashboard/__main__.py`` (see :mod:`app.dashboard.__main__`)
or ``make dashboard``.
"""
import logging
from pathlib import Path

from flask import Flask

from app.dashboard import state

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Build and configure the dashboard Flask app with all blueprints."""
    app = Flask(
        __name__,
        template_folder=str(state.KOAN_ROOT / "koan" / "templates" / "dashboard"),
        static_folder=str(Path(__file__).resolve().parent.parent.parent / "static"),
        static_url_path="/static",
    )

    from app.dashboard._helpers import register_helpers
    register_helpers(app)

    from app.dashboard.agent import agent_bp
    from app.dashboard.chat import chat_bp
    from app.dashboard.config import config_bp
    from app.dashboard.core import core_bp
    from app.dashboard.missions import missions_bp
    from app.dashboard.prs import prs_bp
    from app.dashboard.usage import usage_bp

    for bp in (core_bp, missions_bp, chat_bp, usage_bp, agent_bp, config_bp, prs_bp):
        app.register_blueprint(bp)

    return app


# Module-level app instance used by the runnable entry and the test suite.
app = create_app()
