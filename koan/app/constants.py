"""Centralized numeric constants for the Kōan agent loop.

Gathers magic-number thresholds, timeouts, and tuning parameters from
mission_runner, loop_manager, and iteration_manager into a single module
for easy discovery and tuning.  Values that are overridden at runtime
(e.g. from config.yaml) live here as *defaults*; the owning module
creates a mutable copy via ``from app.constants import X as _X``.
"""

# ---------------------------------------------------------------------------
# Post-mission pipeline  (mission_runner.py)
# ---------------------------------------------------------------------------

# Maximum wall-clock seconds for the entire post-mission pipeline.
# Individual steps have their own timeouts; this caps accumulated delays.
# Configurable via ``post_mission_timeout`` in config.yaml.
POST_MISSION_TIMEOUT_DEFAULT = 300

# Pipeline timeout rate alert — fires when a fraction of recent sessions
# exceed the POST_MISSION_TIMEOUT deadline.
TIMEOUT_ALERT_WINDOW = 10        # recent session outcomes to inspect
TIMEOUT_ALERT_THRESHOLD = 0.5    # fraction that triggers alert
TIMEOUT_ALERT_COOLDOWN = 3600    # seconds between alerts (1 hour)

# Maximum characters extracted from stdout for mission result forwarding.
RESULT_FORWARD_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Sleep / CI queue  (loop_manager.py)
# ---------------------------------------------------------------------------

# Minimum seconds between CI queue checks during interruptible sleep.
CI_QUEUE_SLEEP_INTERVAL = 30

# ---------------------------------------------------------------------------
# GitHub notifications  (loop_manager.py)
# ---------------------------------------------------------------------------

# Default polling intervals (seconds).  Overridden at runtime from
# ``github.check_interval_seconds`` / ``github.max_check_interval_seconds``
# in config.yaml.
GITHUB_CHECK_INTERVAL_DEFAULT = 60
GITHUB_MAX_CHECK_INTERVAL_DEFAULT = 180

# Notification dedup cache parameters.
NOTIF_CACHE_TTL = 86400          # 24 hours
NOTIF_CACHE_MAX = 2000           # LRU eviction threshold

# Error reply retry parameters.
MAX_REPLY_RETRIES = 3
MAX_PENDING_REPLIES = 50

# Non-actionable notification drain cap per cycle.
MAX_DRAIN_PER_CYCLE = 30

# ---------------------------------------------------------------------------
# Jira notifications  (loop_manager.py)
# ---------------------------------------------------------------------------

# Default polling intervals (seconds).  Overridden at runtime from
# ``jira.check_interval_seconds`` / ``jira.max_check_interval_seconds``
# in config.yaml.
JIRA_CHECK_INTERVAL_DEFAULT = 60
JIRA_MAX_CHECK_INTERVAL_DEFAULT = 180

# ---------------------------------------------------------------------------
# Burn rate / budget  (iteration_manager.py)
# ---------------------------------------------------------------------------

# When time-to-exhaustion (minutes) drops below these thresholds,
# mode is downgraded or a Telegram warning fires.
BURN_RATE_DOWNGRADE_THRESHOLD_MIN = 30.0
BURN_RATE_WARNING_THRESHOLD_MIN = 60.0

# Skip warning if quota resets within this many minutes.
BURN_RATE_WARNING_MIN_RESET_GAP_MIN = 120.0

# ---------------------------------------------------------------------------
# Project selection audit  (iteration_manager.py)
# ---------------------------------------------------------------------------

# Ring-buffer cap for the selection audit log.
MAX_SELECTION_AUDIT_ENTRIES = 200
