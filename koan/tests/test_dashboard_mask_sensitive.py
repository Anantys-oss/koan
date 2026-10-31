"""Tests for _SENSITIVE_KEY_RE — must redact compound keys, not numeric limits."""

import pytest

# Import the module that owns _SENSITIVE_KEY_RE directly — state.py is the
# canonical owner (mask_sensitive() delegates to it in dashboard_service).
from app.dashboard import state

def _mask(line: str) -> str:
    """Apply the sensitive-key mask (mirrors mask_sensitive behavior)."""
    return state._SENSITIVE_KEY_RE.sub(r'\1<redacted>', line)


class TestSensitiveKeyRegex:
    """_SENSITIVE_KEY_RE must redact bare and compound secret keys while
    leaving non-secret token-* numeric limits visible."""

    def test_bare_token_redacted(self):
        """bare token: 'abc123' → token: <redacted>."""
        line = "  token: abc123"
        out = _mask(line)
        assert "token: <redacted>" in out

    def test_bare_password_redacted(self):
        line = "password: mysecret"
        assert "password: <redacted>" in _mask(line)

    def test_bare_api_key_redacted(self):
        line = "api_key: AKIAxxxxxxxx"
        assert "api_key: <redacted>" in _mask(line)

    def test_bare_secret_redacted(self):
        line = "secret: supersecret"
        assert "secret: <redacted>" in _mask(line)

    def test_bare_private_key_redacted(self):
        line = "private_key: -----BEGIN"
        assert "private_key: <redacted>" in _mask(line)

    def test_bot_token_redacted(self):
        """compound key bot_token must be masked (the mission's leak)."""
        line = "  bot_token: 123456:ABC-DEF"
        assert "bot_token: <redacted>" in _mask(line)

    def test_api_token_redacted(self):
        line = "  api_token: my-jira-token"
        assert "api_token: <redacted>" in _mask(line)

    def test_access_token_redacted(self):
        line = "    access_token: xoxb-abc123"
        assert "access_token: <redacted>" in _mask(line)

    def test_app_token_redacted(self):
        line = "  app_token: xapp-123"
        assert "app_token: <redacted>" in _mask(line)

    def test_password_hash_redacted(self):
        line = "  password_hash: $2b$12$..."
        assert "password_hash: <redacted>" in _mask(line)

    def test_password_salt_redacted(self):
        line = "  password_salt: abc123"
        assert "password_salt: <redacted>" in _mask(line)

    def test_session_token_limit_NOT_redacted(self):
        """Numeric limits must stay visible for the config editor."""
        line = "  session_token_limit: 500000"
        assert _mask(line) == "  session_token_limit: 500000"

    def test_weekly_token_limit_NOT_redacted(self):
        line = "  weekly_token_limit: 5000000"
        assert _mask(line) == "  weekly_token_limit: 5000000"

    def test_budget_tokens_NOT_redacted(self):
        line = "  budget_tokens: 10000"
        assert _mask(line) == "  budget_tokens: 10000"

    def test_max_tokens_NOT_redacted(self):
        line = "max_tokens: 2000"
        assert _mask(line) == "max_tokens: 2000"

    def test_token_limit_NOT_redacted(self):
        line = "  token_limit: 1000"
        assert _mask(line) == "  token_limit: 1000"

    def test_tokens_NOT_redacted(self):
        line = "  tokens: 42"
        assert _mask(line) == "  tokens: 42"

    def test_empty_value_not_redacted(self):
        """Empty/null values should not match \\S+ (bare : at end)."""
        line = "  bot_token:"
        assert _mask(line) == "  bot_token:"

    def test_multiline_yaml(self):
        """Full YAML block with mixed keys."""
        text = """messaging:
  telegram:
    bot_token: 123456:ABC
    chat_id: -100abc
quotas:
  session_token_limit: 500000
  weekly_token_limit: 5000000
"""
        out = _mask(text)
        assert "bot_token: <redacted>" in out
        assert "chat_id: -100abc" in out
        assert "session_token_limit: 500000" in out
        assert "weekly_token_limit: 5000000" in out

    def test_discord_access_token_redacted(self):
        line = "    access_token: syt_abc123"
        assert "access_token: <redacted>" in _mask(line)

    def test_key_not_false_positive(self):
        """Standalone 'key' and 'project_key' must not match."""
        assert _mask("  key: value") == "  key: value"
        assert _mask("  project_key: PROJ-123") == "  project_key: PROJ-123"
        assert _mask("  my_key: abc") == "  my_key: abc"
