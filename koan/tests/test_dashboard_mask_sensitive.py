"""Tests for mask_sensitive() — must redact compound keys, not numeric limits."""

from pathlib import Path

import pytest

from app.dashboard_service import mask_sensitive

# The shipped default config — masking it must redact secrets while keeping
# the numeric limit keys visible in the config editor.
EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "instance.example" / "config.yaml"


class TestSensitiveKeyRegex:
    """mask_sensitive() must redact bare and compound secret keys while
    leaving non-secret token-* numeric limits visible."""

    def test_bare_token_redacted(self):
        """bare token: 'abc123' → token: <redacted>."""
        line = "  token: abc123"
        out = mask_sensitive(line)
        assert "token: <redacted>" in out

    def test_bare_password_redacted(self):
        line = "password: mysecret"
        assert "password: <redacted>" in mask_sensitive(line)

    def test_bare_api_key_redacted(self):
        line = "api_key: AKIAxxxxxxxx"
        assert "api_key: <redacted>" in mask_sensitive(line)

    def test_bare_secret_redacted(self):
        line = "secret: supersecret"
        assert "secret: <redacted>" in mask_sensitive(line)

    def test_bare_private_key_redacted(self):
        line = "private_key: -----BEGIN"
        assert "private_key: <redacted>" in mask_sensitive(line)

    def test_bot_token_redacted(self):
        """compound key bot_token must be masked (the mission's leak)."""
        line = "  bot_token: 123456:ABC-DEF"
        assert "bot_token: <redacted>" in mask_sensitive(line)

    def test_api_token_redacted(self):
        line = "  api_token: my-jira-token"
        assert "api_token: <redacted>" in mask_sensitive(line)

    def test_access_token_redacted(self):
        line = "    access_token: xoxb-abc123"
        assert "access_token: <redacted>" in mask_sensitive(line)

    def test_app_token_redacted(self):
        line = "  app_token: xapp-123"
        assert "app_token: <redacted>" in mask_sensitive(line)

    def test_password_hash_redacted(self):
        line = "  password_hash: $2b$12$..."
        assert "password_hash: <redacted>" in mask_sensitive(line)

    def test_password_salt_redacted(self):
        line = "  password_salt: abc123"
        assert "password_salt: <redacted>" in mask_sensitive(line)

    @pytest.mark.parametrize(
        "stem",
        ["passwd", "access_key", "signing_key", "encryption_key",
         "credential", "passphrase"],
    )
    def test_new_stems_redacted(self, stem):
        line = f"  {stem}: somevalue"
        assert f"{stem}: <redacted>" in mask_sensitive(line)

    @pytest.mark.parametrize(
        "key", ["passwd_hash", "token_salt", "credential_salt"]
    )
    def test_hash_salt_suffixes_redacted(self, key):
        line = f"  {key}: somevalue"
        assert f"{key}: <redacted>" in mask_sensitive(line)

    def test_session_token_limit_NOT_redacted(self):
        """Numeric limits must stay visible for the config editor."""
        line = "  session_token_limit: 500000"
        assert mask_sensitive(line) == line

    def test_weekly_token_limit_NOT_redacted(self):
        line = "  weekly_token_limit: 5000000"
        assert mask_sensitive(line) == line

    def test_budget_tokens_NOT_redacted(self):
        line = "  budget_tokens: 10000"
        assert mask_sensitive(line) == line

    def test_max_tokens_NOT_redacted(self):
        line = "max_tokens: 2000"
        assert mask_sensitive(line) == line

    def test_token_limit_NOT_redacted(self):
        line = "  token_limit: 1000"
        assert mask_sensitive(line) == line

    def test_tokens_NOT_redacted(self):
        line = "  tokens: 42"
        assert mask_sensitive(line) == line

    def test_empty_value_not_redacted(self):
        """Empty/null values should not match \\S+ (bare : at end)."""
        line = "  bot_token:"
        assert mask_sensitive(line) == line

    def test_empty_value_multiline_not_redacted(self):
        """A sensitive key with an empty value must not swallow the next
        line's key (\\s* around the colon used to cross the newline)."""
        text = '  bot_token:\n  chat_id: "123"\n'
        assert mask_sensitive(text) == text

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
        out = mask_sensitive(text)
        assert "bot_token: <redacted>" in out
        assert "chat_id: -100abc" in out
        assert "session_token_limit: 500000" in out
        assert "weekly_token_limit: 5000000" in out

    def test_discord_access_token_redacted(self):
        line = "    access_token: syt_abc123"
        assert "access_token: <redacted>" in mask_sensitive(line)

    def test_key_not_false_positive(self):
        """Standalone 'key' and 'project_key' must not match."""
        assert mask_sensitive("  key: value") == "  key: value"
        assert mask_sensitive("  project_key: PROJ-123") == "  project_key: PROJ-123"
        assert mask_sensitive("  my_key: abc") == "  my_key: abc"

    def test_example_config_masked_selectively(self):
        """The shipped default config: secrets redacted, numeric limits
        and placeholders for non-secret keys stay visible."""
        text = EXAMPLE_CONFIG.read_text()
        out = mask_sensitive(text)
        assert "bot_token: <redacted>" in out
        assert 'bot_token: ""' not in out
        assert "session_token_limit: 500000" in out
        assert "weekly_token_limit: 5000000" in out
