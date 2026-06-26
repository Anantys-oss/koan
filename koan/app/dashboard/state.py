"""Mutable, patchable module-level state for the dashboard package.

Route handlers and service functions read these at call time (``state.INSTANCE_DIR``)
so tests can ``patch.object(app.dashboard.state, "INSTANCE_DIR", tmp_path)`` and have
the change observed everywhere. Never bind these values at import time in callers.
"""
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

KOAN_ROOT = Path(os.environ["KOAN_ROOT"])
INSTANCE_DIR = KOAN_ROOT / "instance"
MISSIONS_FILE = INSTANCE_DIR / "missions.md"
OUTBOX_FILE = INSTANCE_DIR / "outbox.md"
SOUL_FILE = INSTANCE_DIR / "soul.md"
SUMMARY_FILE = INSTANCE_DIR / "memory" / "summary.md"
JOURNAL_DIR = INSTANCE_DIR / "journal"
PENDING_FILE = JOURNAL_DIR / "pending.md"
RECURRING_FILE = INSTANCE_DIR / "recurring.json"
CONVERSATION_HISTORY_FILE = INSTANCE_DIR / "conversation-history.jsonl"

# ---------------------------------------------------------------------------
# Behavioural settings
# ---------------------------------------------------------------------------

CHAT_TIMEOUT = int(os.environ.get("KOAN_CHAT_TIMEOUT", "180"))
DASHBOARD_PWD = os.environ.get("KOAN_DASHBOARD_PWD", "").strip()

# ---------------------------------------------------------------------------
# Thresholds & cache TTLs
# ---------------------------------------------------------------------------

_DISK_WARN_PCT = 85
_DISK_ERROR_PCT = 95
_PLANS_CACHE_TTL = 60  # seconds
_AGENT_SKILLS_CACHE_TTL = 30  # seconds

# ---------------------------------------------------------------------------
# In-memory caches (process-lifetime)
# ---------------------------------------------------------------------------

# {cache_key: (timestamp, data)}
_plans_cache: dict = {}
# {"ts": float, "data": dict}
_agent_skills_cache: dict = {}

# ---------------------------------------------------------------------------
# Shared regexes
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r'(https?://[^\s<>)\]]+)')
_GITHUB_ISSUE_PR_RE = re.compile(
    r'^https?://(?:[^/]+\.)?github\.com/[^/]+/[^/]+/(?:issues|pull)/(\d+)(?:[?#].*)?$'
)
_JIRA_BROWSE_RE = re.compile(
    r'^https?://[^/]+/browse/([A-Z][A-Z0-9_]+-\d+)(?:[?#].*)?$'
)
_SENSITIVE_KEY_RE = re.compile(
    r'(?m)^(\s*(?:token|password|api_key|secret|private_key)\s*:\s*)\S+',
    re.IGNORECASE,
)
