"""Tests for notification_config.py — shared polling interval resolution.

These exercise the precedence (provider override → shared section → default),
floor clamping, and malformed-value handling that github_config / jira_config
rely on to derive their notification poll cadence.
"""

from app.notification_config import (
    get_notification_check_interval,
    get_notification_max_check_interval,
)


class TestCheckInterval:
    def test_default_when_empty_config(self):
        assert get_notification_check_interval({}, "github") == 60

    def test_custom_default_honored(self):
        assert get_notification_check_interval({}, "github", default=90) == 90

    def test_provider_value_overrides_shared(self):
        config = {
            "github": {"check_interval_seconds": 45},
            "notification_polling": {"check_interval_seconds": 120},
        }
        assert get_notification_check_interval(config, "github") == 45

    def test_falls_back_to_shared_when_provider_absent(self):
        config = {"notification_polling": {"check_interval_seconds": 120}}
        assert get_notification_check_interval(config, "github") == 120

    def test_provider_isolation(self):
        """A value under one provider does not leak into another."""
        config = {"github": {"check_interval_seconds": 45}}
        assert get_notification_check_interval(config, "jira") == 60

    def test_floor_clamps_low_values(self):
        config = {"github": {"check_interval_seconds": 3}}
        assert get_notification_check_interval(config, "github") == 10

    def test_floor_is_configurable(self):
        config = {"github": {"check_interval_seconds": 3}}
        assert get_notification_check_interval(config, "github", floor=5) == 5

    def test_malformed_provider_value_returns_default(self):
        """A present-but-garbage provider value yields the default, not a crash
        and not a fall-through to the shared section."""
        config = {
            "github": {"check_interval_seconds": "not-a-number"},
            "notification_polling": {"check_interval_seconds": 120},
        }
        assert get_notification_check_interval(config, "github") == 60

    def test_none_provider_value_is_present_and_returns_default(self):
        """An explicit null is 'present' for precedence but un-coercible."""
        config = {"github": {"check_interval_seconds": None}}
        assert get_notification_check_interval(config, "github") == 60

    def test_non_dict_provider_section_ignored(self):
        config = {"github": "oops", "notification_polling": {"check_interval_seconds": 75}}
        assert get_notification_check_interval(config, "github") == 75

    def test_non_dict_shared_section_falls_to_default(self):
        config = {"notification_polling": ["not", "a", "dict"]}
        assert get_notification_check_interval(config, "github") == 60

    def test_float_value_is_truncated(self):
        config = {"github": {"check_interval_seconds": 42.9}}
        assert get_notification_check_interval(config, "github") == 42


class TestMaxCheckInterval:
    def test_default_when_empty_config(self):
        assert get_notification_max_check_interval({}, "jira") == 300

    def test_provider_value_overrides_shared(self):
        config = {
            "jira": {"max_check_interval_seconds": 200},
            "notification_polling": {"max_check_interval_seconds": 600},
        }
        assert get_notification_max_check_interval(config, "jira") == 200

    def test_falls_back_to_shared(self):
        config = {"notification_polling": {"max_check_interval_seconds": 600}}
        assert get_notification_max_check_interval(config, "jira") == 600

    def test_floor_clamps_low_values(self):
        config = {"jira": {"max_check_interval_seconds": 5}}
        assert get_notification_max_check_interval(config, "jira") == 30

    def test_malformed_provider_value_returns_default(self):
        config = {"jira": {"max_check_interval_seconds": {}}}
        assert get_notification_max_check_interval(config, "jira") == 300
