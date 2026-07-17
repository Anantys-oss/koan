"""Pure business logic for the dashboard, extracted from the route layer.

These modules contain no Flask request/response handling — they read patchable
paths from :mod:`app.dashboard.state` and are unit-testable without a Flask client.

Submodules:
    missions — mission parsing, filtering, project/skill name discovery
    journal  — journal date/day readers
    plans    — plan-issue fetching and progress parsing
    progress — pending.md header + [cli] timeline for /progress
    stats    — forecast, skill metrics, agent-state readers
"""
from pathlib import Path


def read_file(path: Path) -> str:
    """Read a file's text, returning '' when it does not exist."""
    if path.exists():
        return path.read_text()
    return ""


def mask_sensitive(yaml_text: str) -> str:
    """Replace sensitive YAML values with <redacted>."""
    from app.dashboard import state
    return state._SENSITIVE_KEY_RE.sub(r'\1<redacted>', yaml_text)


def validate_yaml(text: str) -> str | None:
    """Return None if valid YAML, error message otherwise."""
    import yaml
    try:
        yaml.safe_load(text)
        return None
    except yaml.YAMLError as e:
        return str(e)
