"""Tests for governor_cli — CLI dispatch, output formatting, error handling."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure koan/ is in path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def env_setup(tmp_path, monkeypatch):
    """Set up minimal KOAN_ROOT environment for testing."""
    koan_root = tmp_path / "koan"
    koan_root.mkdir()
    instance = koan_root / "instance"
    instance.mkdir()
    (instance / "skills").mkdir()
    (instance / "config.yaml").write_text("go_live:\n  owner_mode: true\n")
    (instance / "outbox.md").write_text("")
    monkeypatch.setenv("KOAN_ROOT", str(koan_root))


class TestCLIContext:
    """Test CLIContext hybrid dict/attribute access."""

    def test_attribute_access(self):
        from app.governor_cli import CLIContext
        ctx = CLIContext(
            koan_root=Path("/tmp"),
            instance_dir=Path("/tmp/instance"),
            command_name="watcher",
            args="scan",
        )
        assert ctx.args == "scan"
        assert ctx.command_name == "watcher"

    def test_dict_get_access(self):
        from app.governor_cli import CLIContext
        ctx = CLIContext(
            koan_root=Path("/tmp"),
            instance_dir=Path("/tmp/instance"),
            args="status",
        )
        assert ctx.get("args") == "status"
        assert ctx.get("nonexistent", "default") == "default"


class TestSuggestCommand:
    """Test command suggestion for typos."""

    def test_close_match(self):
        from app.governor_cli import _suggest_command
        result = _suggest_command("statu")
        assert "status" in result

    def test_no_match(self):
        from app.governor_cli import _suggest_command
        result = _suggest_command("xyzabc")
        assert "Commande inconnue" in result

    def test_multiple_matches(self):
        from app.governor_cli import _suggest_command
        result = _suggest_command("s")
        assert "Commande inconnue" in result


class TestSkillMap:
    """Test SKILL_MAP configuration."""

    def test_all_expected_commands_present(self):
        from app.governor_cli import SKILL_MAP
        expected = ["status", "watcher", "advisor", "autonomy", "rollout",
                    "offboard", "budget", "keys", "vault", "env", "scan"]
        for cmd in expected:
            assert cmd in SKILL_MAP, f"Missing command: {cmd}"

    def test_budget_no_prepend(self):
        from app.governor_cli import SKILL_MAP
        _, prepend = SKILL_MAP["budget"]
        assert prepend is False

    def test_watcher_no_prepend(self):
        from app.governor_cli import SKILL_MAP
        _, prepend = SKILL_MAP["watcher"]
        assert prepend is False


class TestExitCodes:
    """Test exit code constants."""

    def test_exit_codes(self):
        from app.governor_cli import (
            EXIT_OK, EXIT_ERROR, EXIT_SKILL_NOT_FOUND,
            EXIT_DOCKER_DOWN, EXIT_CONFIG_MISSING,
        )
        assert EXIT_OK == 0
        assert EXIT_ERROR == 1
        assert EXIT_SKILL_NOT_FOUND == 2
        assert EXIT_DOCKER_DOWN == 3
        assert EXIT_CONFIG_MISSING == 4


class TestColorFunctions:
    """Test ANSI color helpers."""

    def test_colors_disabled_when_not_tty(self):
        import app.governor_cli as cli
        cli._USE_COLOR = False
        assert cli._green("test") == "test"
        assert cli._red("test") == "test"
        assert cli._bold("test") == "test"

    def test_colors_enabled(self):
        import app.governor_cli as cli
        cli._USE_COLOR = True
        result = cli._green("test")
        assert "\033[32m" in result
        assert "test" in result


class TestGChatUrl:
    """Test Google Chat webhook URL resolution."""

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("GCHAT_WEBHOOK_URL", "https://chat.example.com/webhook")
        from app.governor_cli import _get_gchat_url
        assert _get_gchat_url() == "https://chat.example.com/webhook"

    def test_no_url(self, monkeypatch):
        monkeypatch.delenv("GCHAT_WEBHOOK_URL", raising=False)
        from app.governor_cli import _get_gchat_url
        # May return None if not in config
        result = _get_gchat_url()
        # Just check it doesn't crash


class TestBuildParser:
    """Test argparse configuration."""

    def test_help_flag(self):
        from app.governor_cli import _build_parser
        parser = _build_parser()
        # Parser should be created without errors
        assert parser.prog == "governor"

    def test_json_flag(self):
        from app.governor_cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["--json", "status"])
        assert args.output_json is True
        assert args.command == "status"

    def test_dry_run_flag(self):
        from app.governor_cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["--dry-run", "watcher", "scan"])
        assert args.dry_run is True
        assert args.command == "watcher"
        assert args.rest == ["scan"]

    def test_verbose_flag(self):
        from app.governor_cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["--verbose", "status"])
        assert args.verbose is True

    def test_no_command(self):
        from app.governor_cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None
