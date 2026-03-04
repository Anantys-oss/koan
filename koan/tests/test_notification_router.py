"""Tests for app.notification_router — rollout groups, autonomy, user registry."""

import yaml
import pytest

from app import utils as utils_mod
from app import notification_router as router_mod
from app.notification_router import NotificationRouter, get_router, reset_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_CONFIG = {
    "governor": {
        "rollout": {
            "groups": {
                "governors": {
                    "active": True,
                    "members": ["admin-gov", "stephane"],
                },
                "pilots": {
                    "active": False,
                    "members": ["pilot-alice", "pilot-bob"],
                },
                "all": {
                    "active": False,
                    "members": [],
                },
            }
        },
        "autonomy": {
            "budget_controller": "notify",
            "credential_vault": "supervise",
            "watcher": "watch",
            "advisor": "watch",
        },
    }
}

MINIMAL_REGISTRY = {
    "users": [
        {
            "login": "dany-yourart",
            "name": "Dany",
            "role": "tech",
            "rollout_group": "governors",
            "aliases": [{"login": "dany-alias", "platform": "gitlab"}],
        },
        {
            "login": "pilot-alice",
            "name": "Alice",
            "role": "citizen",
            "rollout_group": "pilots",
        },
    ]
}


def _write_config(tmp_path, config=None, registry=None):
    """Write config.yaml and user_registry.yaml under tmp_path/instance/."""
    instance = tmp_path / "instance"
    instance.mkdir(exist_ok=True)

    cfg = config if config is not None else MINIMAL_CONFIG
    (instance / "config.yaml").write_text(
        yaml.dump(cfg, default_flow_style=False, allow_unicode=True)
    )

    watcher_dir = instance / "watcher"
    watcher_dir.mkdir(exist_ok=True)
    reg = registry if registry is not None else MINIMAL_REGISTRY
    (watcher_dir / "user_registry.yaml").write_text(
        yaml.dump(reg, default_flow_style=False, allow_unicode=True)
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def router_env(tmp_path, monkeypatch):
    """Set up isolated KOAN_ROOT/INSTANCE_DIR and write default config files."""
    _write_config(tmp_path)
    instance_dir = tmp_path / "instance"
    monkeypatch.setattr(utils_mod, "KOAN_ROOT", tmp_path)
    monkeypatch.setattr(utils_mod, "INSTANCE_DIR", instance_dir)
    monkeypatch.setattr(router_mod, "KOAN_ROOT", tmp_path)
    monkeypatch.setattr(router_mod, "INSTANCE_DIR", instance_dir)
    reset_router()
    yield tmp_path
    reset_router()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestShouldNotify:
    def test_should_notify_active_group(self, router_env):
        """Member of an active group -> should_notify returns True."""
        router = get_router()
        assert router.should_notify("duplication", "admin-gov") is True
        assert router.should_notify("commit", "stephane") is True

    def test_should_notify_inactive_group(self, router_env):
        """Member of an inactive group -> should_notify returns False."""
        router = get_router()
        assert router.should_notify("duplication", "pilot-alice") is False
        assert router.should_notify("commit", "pilot-bob") is False

    def test_should_notify_unknown_user(self, router_env):
        """User not in any group or registry -> should_notify returns False."""
        router = get_router()
        assert router.should_notify("commit", "unknown-user") is False

    def test_should_notify_registry_fallback(self, router_env):
        """User not in group members but in registry with rollout_group -> uses group active flag."""
        router = get_router()
        # dany-yourart has rollout_group=governors (active) but is not in members list
        assert router.should_notify("commit", "dany-yourart") is True


class TestGetRecipients:
    def test_get_recipients(self, router_env):
        """Returns sorted members of all active groups only."""
        router = get_router()
        recipients = router.get_recipients("any_event")
        assert recipients == ["admin-gov", "stephane"]
        assert "pilot-alice" not in recipients
        assert "pilot-bob" not in recipients


class TestCheckAutonomy:
    def test_check_autonomy_watch(self, router_env):
        """Module with 'watch' level -> 'log_only'."""
        router = get_router()
        assert router.check_autonomy("watcher") == "log_only"
        assert router.check_autonomy("advisor") == "log_only"

    def test_check_autonomy_notify(self, router_env):
        """Module with 'notify' level -> 'notify_citizen'."""
        router = get_router()
        assert router.check_autonomy("budget_controller") == "notify_citizen"

    def test_check_autonomy_supervise(self, router_env):
        """Module with 'supervise' level -> 'ask_governor'."""
        router = get_router()
        assert router.check_autonomy("credential_vault") == "ask_governor"

    def test_check_autonomy_unknown_module(self, router_env):
        """Unknown module defaults to 'log_only' (watch)."""
        router = get_router()
        assert router.check_autonomy("nonexistent_module") == "log_only"


class TestIsGovernor:
    def test_is_governor(self, router_env):
        """Login in the governors group -> True."""
        router = get_router()
        assert router.is_governor("admin-gov") is True
        assert router.is_governor("stephane") is True

    def test_is_not_governor(self, router_env):
        """Login NOT in governors group -> False."""
        router = get_router()
        assert router.is_governor("pilot-alice") is False
        assert router.is_governor("unknown") is False


class TestResolveLogin:
    def test_resolve_login_alias(self, router_env):
        """Alias login resolves to the primary login."""
        router = get_router()
        assert router.resolve_login("dany-alias") == "dany-yourart"

    def test_resolve_login_primary(self, router_env):
        """Primary login resolves to itself."""
        router = get_router()
        assert router.resolve_login("dany-yourart") == "dany-yourart"

    def test_resolve_login_unknown(self, router_env):
        """Unknown login returns itself."""
        router = get_router()
        assert router.resolve_login("nobody") == "nobody"


class TestGetUserInfo:
    def test_get_user_info_by_login(self, router_env):
        """Find user by primary login."""
        router = get_router()
        info = router.get_user_info("dany-yourart")
        assert info is not None
        assert info["name"] == "Dany"

    def test_get_user_info_by_alias(self, router_env):
        """Find user by alias login."""
        router = get_router()
        info = router.get_user_info("dany-alias")
        assert info is not None
        assert info["login"] == "dany-yourart"

    def test_get_user_info_unknown(self, router_env):
        """Unknown login returns None."""
        router = get_router()
        assert router.get_user_info("nobody") is None


class TestGetGroups:
    def test_get_groups(self, router_env):
        """Returns all groups with their config."""
        router = get_router()
        groups = router.get_groups()
        assert "governors" in groups
        assert "pilots" in groups
        assert "all" in groups
        assert groups["governors"]["active"] is True
        assert groups["pilots"]["active"] is False


class TestActivateGroup:
    def test_activate_group(self, router_env):
        """Activating an inactive group makes it active."""
        router = get_router()
        assert router.activate_group("pilots") is True
        assert router._groups["pilots"]["active"] is True
        # pilot members now appear in recipients
        recipients = router.get_recipients("any")
        assert "pilot-alice" in recipients

    def test_activate_group_nonexistent(self, router_env):
        """Activating a group that doesn't exist returns False."""
        router = get_router()
        assert router.activate_group("nonexistent") is False


class TestAddRemoveMember:
    def test_add_member(self, router_env):
        """Adding a member to a group persists it."""
        router = get_router()
        assert router.add_member("governors", "new-gov") is True
        assert "new-gov" in router._groups["governors"]["members"]

    def test_add_member_nonexistent_group(self, router_env):
        """Adding to a nonexistent group returns False."""
        router = get_router()
        assert router.add_member("nonexistent", "someone") is False

    def test_remove_member(self, router_env):
        """Removing a member from a group."""
        router = get_router()
        assert router.remove_member("governors", "stephane") is True
        assert "stephane" not in router._groups["governors"]["members"]

    def test_remove_member_nonexistent_group(self, router_env):
        """Removing from a nonexistent group returns False."""
        router = get_router()
        assert router.remove_member("nonexistent", "someone") is False


class TestSingleton:
    def test_get_router_returns_same_instance(self, router_env):
        """get_router() returns the same singleton."""
        r1 = get_router()
        r2 = get_router()
        assert r1 is r2

    def test_reset_router_clears_singleton(self, router_env):
        """reset_router() creates a fresh instance on next get_router()."""
        r1 = get_router()
        reset_router()
        r2 = get_router()
        assert r1 is not r2
