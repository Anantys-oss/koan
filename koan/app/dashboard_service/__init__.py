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
import re
from pathlib import Path

# Leading indent + key name of a line matched by _SENSITIVE_KEY_RE
# (group 1 is ``<indent><key><ws>:<ws>``).
_KEY_PREFIX_RE = re.compile(r"([ \t]*)([A-Za-z0-9_.\-]+)")


def read_file(path: Path) -> str:
    """Read a file's text, returning '' when it does not exist."""
    if path.exists():
        return path.read_text()
    return ""


def mask_sensitive(yaml_text: str) -> str:
    """Replace sensitive YAML values with <redacted>."""
    from app.dashboard import state
    return state._SENSITIVE_KEY_RE.sub(r'\1<redacted>', yaml_text)


def restore_redacted_values(existing_text: str, submitted_text: str) -> str:
    """Put on-disk values back under sensitive keys submitted as <redacted>.

    The config editor shows masked content, so saving it back must not persist
    the <redacted> placeholder. Each submitted line whose sensitive value is
    exactly <redacted> (nothing but whitespace after it) is restored to the
    value currently on disk for the same key — matched by indent + key name,
    consumed in file order (reordering blocks can mis-pair duplicate keys).
    Lines without an on-disk original are left untouched; the save endpoint
    rejects the submission in that case.
    """
    from app.dashboard import state

    marker = "<redacted>"
    originals: dict[tuple[str, str], list[str]] = {}
    for line in existing_text.splitlines():
        m = state._SENSITIVE_KEY_RE.match(line)
        if not m:
            continue
        hidden = line[m.end(1):]
        if hidden.startswith(marker):
            continue  # on-disk value is itself a placeholder — unusable
        key_prefix = _KEY_PREFIX_RE.match(m.group(1))
        originals.setdefault((key_prefix.group(1), key_prefix.group(2)), []).append(
            hidden.split(None, 1)[0]
        )

    out = []
    for line in submitted_text.splitlines():
        m = state._SENSITIVE_KEY_RE.match(line)
        if m and line[m.end(1):].startswith(marker):
            rest = line[m.end(1) + len(marker):]
            if not rest or rest[0].isspace():
                key_prefix = _KEY_PREFIX_RE.match(m.group(1))
                pool = originals.get((key_prefix.group(1), key_prefix.group(2)))
                if pool:
                    line = line[:m.end(1)] + pool.pop(0) + rest
        out.append(line)

    restored = "\n".join(out)
    if submitted_text.endswith("\n"):
        restored += "\n"
    return restored


def validate_yaml(text: str) -> str | None:
    """Return None if valid YAML, error message otherwise."""
    import yaml
    try:
        yaml.safe_load(text)
        return None
    except yaml.YAMLError as e:
        return str(e)
