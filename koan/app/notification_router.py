"""Governor — Notification router with rollout groups and hot reload.

Manages which users receive notifications based on rollout group membership.
Configuration is loaded from config.yaml section governor.rollout and
hot-reloaded every 60 seconds.

Usage:
    from app.notification_router import get_router
    router = get_router()
    if router.should_notify("duplication", "dany-yourart"):
        send_notification(...)
"""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.utils import KOAN_ROOT, INSTANCE_DIR, load_config, atomic_write

logger = logging.getLogger("governor.notification_router")

RELOAD_INTERVAL = 60  # seconds


class NotificationRouter:
    """Routes notifications based on rollout groups and user registry."""

    VALID_MODULES = {"budget_controller", "credential_vault", "watcher", "advisor"}
    VALID_LEVELS = {"watch", "notify", "supervise"}

    def __init__(self):
        self._groups: Dict[str, dict] = {}
        self._autonomy: Dict[str, str] = {}
        self._user_registry: List[dict] = []
        self._last_reload: float = 0
        self._reload()

    def _reload(self):
        """Reload config and user registry."""
        try:
            config = load_config()
            governor = config.get("governor", {})
            rollout = governor.get("rollout", {})
            self._groups = rollout.get("groups", {})
            self._autonomy = governor.get("autonomy", {})

            registry_path = INSTANCE_DIR / "watcher" / "user_registry.yaml"
            if registry_path.exists():
                with open(registry_path) as f:
                    data = yaml.safe_load(f) or {}
                self._user_registry = data.get("users", [])
            else:
                self._user_registry = []

            self._last_reload = time.monotonic()
        except Exception as e:
            logger.error("Failed to reload notification router config: %s", e)

    def _ensure_fresh(self):
        """Hot reload if stale."""
        if time.monotonic() - self._last_reload > RELOAD_INTERVAL:
            self._reload()

    def should_notify(self, event_type: str, author_login: str) -> bool:
        """Check if a notification should be sent for this author.

        Returns True if the author is in at least one active rollout group.
        Governors group always receives notifications (if active).
        """
        self._ensure_fresh()

        for group_name, group in self._groups.items():
            if not group.get("active", False):
                continue
            members = group.get("members", [])
            if author_login in members:
                return True

        # Check user registry for rollout_group assignment
        for user in self._user_registry:
            if user.get("login") == author_login:
                user_group = user.get("rollout_group", "all")
                group_config = self._groups.get(user_group, {})
                return group_config.get("active", False)

        return False

    def get_recipients(self, event_type: str) -> List[str]:
        """Return list of logins in active rollout groups."""
        self._ensure_fresh()
        recipients = set()

        for group_name, group in self._groups.items():
            if not group.get("active", False):
                continue
            for member in group.get("members", []):
                recipients.add(member)

        return sorted(recipients)

    def get_groups(self) -> Dict[str, dict]:
        """Return current rollout groups configuration."""
        self._ensure_fresh()
        return dict(self._groups)

    def is_governor(self, login: str) -> bool:
        """Check if a login belongs to the governors group."""
        self._ensure_fresh()
        governors = self._groups.get("governors", {})
        return login in governors.get("members", [])

    def get_user_info(self, login: str) -> Optional[dict]:
        """Find user info from registry by login or alias."""
        self._ensure_fresh()
        for user in self._user_registry:
            if user.get("login") == login:
                return user
            for alias in user.get("aliases", []):
                if alias.get("login") == login:
                    return user
        return None

    def resolve_login(self, login: str) -> str:
        """Resolve an alias to the primary login."""
        user = self.get_user_info(login)
        if user:
            return user.get("login", login)
        return login

    def check_autonomy(self, module: str, action: str = "notify_citizen") -> str:
        """Check the autonomy level for a module.

        Args:
            module: Module name (budget_controller, credential_vault, watcher, advisor)
            action: Action to check (notify_citizen, etc.)

        Returns:
            "log_only" (watch), "notify_citizen" (notify), "ask_governor" (supervise)
        """
        self._ensure_fresh()
        level = self._autonomy.get(module, "watch")

        if level == "watch":
            return "log_only"
        elif level == "notify":
            return "notify_citizen"
        elif level == "supervise":
            return "ask_governor"
        return "log_only"

    def get_autonomy_levels(self) -> Dict[str, str]:
        """Return current autonomy levels for all modules."""
        self._ensure_fresh()
        return dict(self._autonomy)

    def set_autonomy_level(self, module: str, level: str) -> bool:
        """Set the autonomy level for a module in config.yaml.

        Returns True if successful, False if invalid module/level.
        """
        if module not in self.VALID_MODULES or level not in self.VALID_LEVELS:
            return False

        config = load_config()
        governor = config.setdefault("governor", {})
        autonomy = governor.setdefault("autonomy", {})
        autonomy[module] = level

        import yaml
        config_path = INSTANCE_DIR / "config.yaml"
        content = yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)
        atomic_write(config_path, content)

        self._autonomy = autonomy
        logger.info("Autonomy level set: %s → %s", module, level)
        return True

    def activate_group(self, name: str) -> bool:
        """Activate a rollout group."""
        config = load_config()
        groups = config.get("governor", {}).get("rollout", {}).get("groups", {})
        if name not in groups:
            return False
        groups[name]["active"] = True
        self._save_config(config)
        self._groups = groups
        return True

    def add_member(self, group: str, login: str) -> bool:
        """Add a member to a rollout group."""
        config = load_config()
        groups = config.get("governor", {}).get("rollout", {}).get("groups", {})
        if group not in groups:
            return False
        members = groups[group].setdefault("members", [])
        if login not in members:
            members.append(login)
        self._save_config(config)
        self._groups = groups
        return True

    def remove_member(self, group: str, login: str) -> bool:
        """Remove a member from a rollout group."""
        config = load_config()
        groups = config.get("governor", {}).get("rollout", {}).get("groups", {})
        if group not in groups:
            return False
        members = groups[group].get("members", [])
        if login in members:
            members.remove(login)
        self._save_config(config)
        self._groups = groups
        return True

    def _save_config(self, config: dict):
        """Save the full config.yaml."""
        import yaml
        config_path = INSTANCE_DIR / "config.yaml"
        content = yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)
        atomic_write(config_path, content)


_router: Optional[NotificationRouter] = None


def get_router() -> NotificationRouter:
    """Get the singleton NotificationRouter instance."""
    global _router
    if _router is None:
        _router = NotificationRouter()
    return _router


def reset_router():
    """Reset the singleton (for testing)."""
    global _router
    _router = None
