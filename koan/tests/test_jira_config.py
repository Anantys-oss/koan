"""Tests for jira_config.py — Jira configuration helpers."""

import os

import pytest

from app.jira_config import (
    get_jira_api_token,
    get_jira_authorized_users,
    get_jira_base_url,
    get_jira_check_interval,
    get_jira_commands_enabled,
    get_jira_email,
    get_jira_enabled,
    get_jira_max_age_hours,
    get_jira_max_check_interval,
    get_jira_nickname,
    get_jira_project_map,
    validate_jira_config,
)


@pytest.fixture
def minimal_jira_config():
    return {
        "jira": {
            "enabled": True,
            "base_url": "https://myorg.atlassian.net",
            "email": "bot@example.com",
            "api_token": "secret",
            "nickname": "koan-bot",
        }
    }


class TestGetJiraEnabled:
    def test_default_false(self):
        assert get_jira_enabled({}) is False

    def test_enabled_true(self):
        assert get_jira_enabled({"jira": {"enabled": True}}) is True

    def test_enabled_false(self):
        assert get_jira_enabled({"jira": {"enabled": False}}) is False

    def test_missing_jira_key(self):
        assert get_jira_enabled({"github": {}}) is False


class TestGetJiraCommandsEnabled:
    def test_default_false(self):
        assert get_jira_commands_enabled({}) is False

    def test_enabled(self):
        assert get_jira_commands_enabled({"jira": {"commands_enabled": True}}) is True


class TestGetJiraBaseUrl:
    def test_default_empty(self):
        assert get_jira_base_url({}) == ""

    def test_strips_trailing_slash(self):
        cfg = {"jira": {"base_url": "https://myorg.atlassian.net/"}}
        assert get_jira_base_url(cfg) == "https://myorg.atlassian.net"

    def test_returns_url(self):
        cfg = {"jira": {"base_url": "https://myorg.atlassian.net"}}
        assert get_jira_base_url(cfg) == "https://myorg.atlassian.net"


class TestGetJiraApiToken:
    def test_from_config(self):
        cfg = {"jira": {"api_token": "my-token"}}
        assert get_jira_api_token(cfg) == "my-token"

    def test_env_var_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("KOAN_JIRA_API_TOKEN", "env-token")
        cfg = {"jira": {"api_token": "config-token"}}
        assert get_jira_api_token(cfg) == "env-token"

    def test_default_empty(self):
        assert get_jira_api_token({}) == ""

    def test_env_var_empty_falls_back_to_config(self, monkeypatch):
        monkeypatch.delenv("KOAN_JIRA_API_TOKEN", raising=False)
        cfg = {"jira": {"api_token": "config-token"}}
        assert get_jira_api_token(cfg) == "config-token"


class TestGetJiraNickname:
    def test_default_empty(self):
        assert get_jira_nickname({}) == ""

    def test_strips_whitespace(self):
        cfg = {"jira": {"nickname": "  koan-bot  "}}
        assert get_jira_nickname(cfg) == "koan-bot"

    def test_returns_nickname(self):
        cfg = {"jira": {"nickname": "koan-bot"}}
        assert get_jira_nickname(cfg) == "koan-bot"


class TestGetJiraAuthorizedUsers:
    def test_default_empty(self):
        assert get_jira_authorized_users({}) == []

    def test_wildcard(self):
        cfg = {"jira": {"authorized_users": ["*"]}}
        assert get_jira_authorized_users(cfg) == ["*"]

    def test_list_of_emails(self):
        cfg = {"jira": {"authorized_users": ["alice@example.com", "bob@example.com"]}}
        assert get_jira_authorized_users(cfg) == ["alice@example.com", "bob@example.com"]

    def test_non_list_returns_empty(self):
        cfg = {"jira": {"authorized_users": "*"}}
        assert get_jira_authorized_users(cfg) == []


class TestGetJiraMaxAgeHours:
    def test_default(self):
        assert get_jira_max_age_hours({}) == 24

    def test_custom(self):
        cfg = {"jira": {"max_age_hours": 48}}
        assert get_jira_max_age_hours(cfg) == 48

    def test_invalid_returns_default(self):
        cfg = {"jira": {"max_age_hours": "bad"}}
        assert get_jira_max_age_hours(cfg) == 24


class TestGetJiraCheckInterval:
    def test_default(self):
        assert get_jira_check_interval({}) == 60

    def test_custom(self):
        cfg = {"jira": {"check_interval_seconds": 120}}
        assert get_jira_check_interval(cfg) == 120

    def test_floor_at_10(self):
        cfg = {"jira": {"check_interval_seconds": 5}}
        assert get_jira_check_interval(cfg) == 10


class TestGetJiraMaxCheckInterval:
    def test_default(self):
        assert get_jira_max_check_interval({}) == 180

    def test_custom(self):
        cfg = {"jira": {"max_check_interval_seconds": 300}}
        assert get_jira_max_check_interval(cfg) == 300

    def test_floor_at_30(self):
        cfg = {"jira": {"max_check_interval_seconds": 10}}
        assert get_jira_max_check_interval(cfg) == 30


class TestGetJiraProjectMap:
    def test_default_empty(self):
        assert get_jira_project_map({}) == {}

    def test_returns_map(self):
        cfg = {"jira": {"projects": {"FOO": "myproject", "BAR": "another"}}}
        assert get_jira_project_map(cfg) == {"FOO": "myproject", "BAR": "another"}

    def test_non_dict_returns_empty(self):
        cfg = {"jira": {"projects": "bad"}}
        assert get_jira_project_map(cfg) == {}

    def test_converts_keys_to_str(self):
        cfg = {"jira": {"projects": {123: "myproject"}}}
        result = get_jira_project_map(cfg)
        assert "123" in result


class TestValidateJiraConfig:
    def test_disabled_returns_none(self):
        assert validate_jira_config({}) is None
        assert validate_jira_config({"jira": {"enabled": False}}) is None

    def test_enabled_missing_base_url(self):
        cfg = {"jira": {"enabled": True}}
        result = validate_jira_config(cfg)
        assert result is not None
        assert "base_url" in result

    def test_enabled_missing_email(self):
        cfg = {"jira": {"enabled": True, "base_url": "https://x.atlassian.net"}}
        result = validate_jira_config(cfg)
        assert result is not None
        assert "email" in result

    def test_enabled_missing_api_token(self, monkeypatch):
        monkeypatch.delenv("KOAN_JIRA_API_TOKEN", raising=False)
        cfg = {
            "jira": {
                "enabled": True,
                "base_url": "https://x.atlassian.net",
                "email": "bot@example.com",
            }
        }
        result = validate_jira_config(cfg)
        assert result is not None
        assert "api_token" in result or "KOAN_JIRA_API_TOKEN" in result

    def test_enabled_missing_nickname(self, monkeypatch):
        monkeypatch.delenv("KOAN_JIRA_API_TOKEN", raising=False)
        cfg = {
            "jira": {
                "enabled": True,
                "base_url": "https://x.atlassian.net",
                "email": "bot@example.com",
                "api_token": "secret",
            }
        }
        result = validate_jira_config(cfg)
        assert result is not None
        assert "nickname" in result

    def test_valid_config_returns_none(self, monkeypatch, minimal_jira_config):
        monkeypatch.delenv("KOAN_JIRA_API_TOKEN", raising=False)
        assert validate_jira_config(minimal_jira_config) is None
