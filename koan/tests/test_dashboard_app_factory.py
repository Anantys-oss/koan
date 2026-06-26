"""App-factory and structural acceptance tests for the dashboard package."""
from pathlib import Path

from app.dashboard import create_app

PKG = Path(__file__).parent.parent / "app" / "dashboard"

# Every route path that existed in the original monolithic dashboard.py.
LEGACY_ROUTES = [
    "/", "/login", "/logout",
    "/missions", "/missions/add",
    "/chat", "/chat/send", "/progress",
    "/usage", "/journal", "/logs",
    "/agent", "/skills",
    "/config", "/rules", "/recurring",
    "/prs", "/plans",
    "/api/health", "/api/status", "/api/forecast", "/api/provider",
    "/api/missions", "/api/missions/reorder", "/api/missions/cancel",
    "/api/missions/edit", "/api/projects",
    "/api/attention", "/api/attention/dismiss", "/api/attention/dismiss-all",
    "/api/progress", "/api/progress/stream", "/api/state/stream",
    "/api/usage", "/api/usage/missions", "/api/metrics", "/api/efficiency",
    "/api/skill-metrics", "/api/journal/<day>", "/api/logs",
    "/api/agent/soul", "/api/agent/memory", "/api/agent/skills",
    "/api/agent/config", "/api/agent/pause", "/api/agent/resume",
    "/api/agent/restart",
    "/api/config/<target>", "/api/config/restart", "/api/nickname",
    "/api/rules", "/api/rules/<rule_id>",
    "/api/recurring", "/api/recurring/<task_id>", "/api/recurring/<task_id>/run",
    "/api/prs", "/api/prs/<project>/<int:number>/checks",
    "/api/prs/<project>/<int:number>/merge",
    "/api/plans", "/api/plans/<project>/<int:number>",
]


def test_factory_builds_app():
    app = create_app()
    assert app.name == "app.dashboard"


def test_factory_registers_blueprints():
    app = create_app()
    assert set(app.blueprints) == {
        "core", "missions", "chat", "usage", "agent", "config", "prs"
    }


def test_all_legacy_routes_present():
    rules = {r.rule for r in create_app().url_map.iter_rules()}
    missing = [r for r in LEGACY_ROUTES if r not in rules]
    assert not missing, f"missing routes: {missing}"


def test_main_init_under_300_lines():
    n = len((PKG / "__init__.py").read_text().splitlines())
    assert n < 300, f"__init__.py is {n} lines"


def test_each_blueprint_under_400_lines():
    for f in ["core", "missions", "chat", "usage", "agent", "config", "prs"]:
        n = len((PKG / f"{f}.py").read_text().splitlines())
        assert n < 400, f"{f}.py is {n} lines"
