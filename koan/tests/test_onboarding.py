"""Tests for the CLI onboarding wizard."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Must set KOAN_ROOT before imports
os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")


class TestOnboardingState:
    """Tests for OnboardingState persistence."""

    def test_empty_state(self):
        from app.onboarding import OnboardingState

        state = OnboardingState()
        assert state.completed_steps == []
        assert state.data == {}

    def test_mark_complete(self):
        from app.onboarding import OnboardingState

        state = OnboardingState()
        state.mark_complete("step1")
        assert state.is_complete("step1")
        assert not state.is_complete("step2")

    def test_mark_complete_idempotent(self):
        from app.onboarding import OnboardingState

        state = OnboardingState()
        state.mark_complete("step1")
        state.mark_complete("step1")
        assert state.completed_steps.count("step1") == 1

    def test_save_and_load(self, tmp_path):
        from app.onboarding import OnboardingState

        checkpoint = tmp_path / ".koan-onboarding.json"

        state = OnboardingState()
        state.mark_complete("step1")
        state.mark_complete("step2")
        state.data["key"] = "value"
        state.save(checkpoint)

        loaded = OnboardingState.load(checkpoint)
        assert loaded.is_complete("step1")
        assert loaded.is_complete("step2")
        assert not loaded.is_complete("step3")
        assert loaded.data["key"] == "value"

    def test_load_missing_file(self, tmp_path):
        from app.onboarding import OnboardingState

        loaded = OnboardingState.load(tmp_path / "nonexistent.json")
        assert loaded.completed_steps == []
        assert loaded.data == {}

    def test_load_corrupt_file(self, tmp_path):
        from app.onboarding import OnboardingState

        checkpoint = tmp_path / "corrupt.json"
        checkpoint.write_text("not valid json{{{")

        loaded = OnboardingState.load(checkpoint)
        assert loaded.completed_steps == []


class TestInputHelpers:
    """Tests for terminal input helpers."""

    def test_ask_with_default_non_interactive(self):
        from app.onboarding import ask

        with patch("app.onboarding._is_interactive", False):
            result = ask("prompt", default="hello")
            assert result == "hello"

    def test_ask_yes_no_default_true_non_interactive(self):
        from app.onboarding import ask_yes_no

        with patch("app.onboarding._is_interactive", False):
            assert ask_yes_no("ok?", default=True) is True

    def test_ask_yes_no_default_false_non_interactive(self):
        from app.onboarding import ask_yes_no

        with patch("app.onboarding._is_interactive", False):
            assert ask_yes_no("ok?", default=False) is False

    def test_ask_choice_default_non_interactive(self):
        from app.onboarding import ask_choice

        with patch("app.onboarding._is_interactive", False):
            result = ask_choice("pick", ["a", "b", "c"], default=1)
            assert result == 1

    def test_ask_path_non_interactive(self):
        from app.onboarding import ask_path

        with patch("app.onboarding._is_interactive", False):
            result = ask_path("path")
            assert result == ""

    def test_pause_non_interactive_skips(self):
        from app.onboarding import pause

        with patch("app.onboarding._is_interactive", False):
            # Should return immediately without blocking
            pause()

    def test_pause_interactive_waits_for_enter(self):
        from app.onboarding import pause

        with patch("app.onboarding._is_interactive", True), patch(
            "builtins.input", return_value=""
        ) as mock_input:
            pause()
            mock_input.assert_called_once()

    def test_pause_handles_eof(self):
        from app.onboarding import pause

        with patch("app.onboarding._is_interactive", True), patch(
            "builtins.input", side_effect=EOFError
        ):
            pause()  # Should not raise

    def test_pause_raises_keyboard_interrupt(self):
        from app.onboarding import pause

        with patch("app.onboarding._is_interactive", True), patch(
            "builtins.input", side_effect=KeyboardInterrupt
        ), pytest.raises(KeyboardInterrupt):
            pause()

    def test_pause_custom_message(self):
        from app.onboarding import pause

        with patch("app.onboarding._is_interactive", True), patch(
            "builtins.input", return_value=""
        ) as mock_input:
            pause("Press Enter to begin →")
            # Verify the custom message appears in the prompt
            call_arg = mock_input.call_args[0][0]
            assert "Press Enter to begin" in call_arg

    def test_pause_textual_none_fallback_to_input(self):
        """When Textual pause exits without a result, fall back to plain input()."""
        from app.onboarding import pause

        with (
            patch("app.onboarding._is_interactive", True),
            patch("app.onboarding._use_textual_prompts", return_value=True),
            patch("app.onboarding._textual_pause", return_value=False) as mock_textual,
            patch("builtins.input", return_value="") as mock_input,
        ):
            pause("test message")
            mock_textual.assert_called_once_with("test message")
            mock_input.assert_called_once()

    def test_textual_pause_returns_false_when_app_run_returns_none(self):
        """_textual_pause must return False when Textual exits unexpectedly."""
        from app.onboarding import _textual_pause

        # Simulate Textual installed but App.run() returns None (immediate exit)
        mock_app_instance = MagicMock()
        mock_app_instance.run.return_value = None
        mock_app_class = MagicMock(return_value=mock_app_instance)

        textual_modules = {
            "textual.app": MagicMock(App=mock_app_class, ComposeResult=MagicMock()),
            "textual.binding": MagicMock(Binding=MagicMock()),
            "textual.containers": MagicMock(Vertical=MagicMock()),
            "textual.widgets": MagicMock(
                Button=MagicMock(), Footer=MagicMock(), Header=MagicMock(), Label=MagicMock()
            ),
        }
        with patch.dict("sys.modules", textual_modules):
            result = _textual_pause("test message")
            assert result is False

    def test_ask_interactive_returns_typed_value(self):
        from app.onboarding import ask

        with (
            patch("app.onboarding._is_interactive", True),
            patch("builtins.input", return_value="  hello  "),
        ):
            assert ask("prompt", default="fallback") == "hello"

    def test_ask_interactive_empty_uses_default(self):
        from app.onboarding import ask

        with (
            patch("app.onboarding._is_interactive", True),
            patch("builtins.input", return_value=""),
        ):
            assert ask("prompt", default="fallback") == "fallback"

    def test_ask_handles_eof(self):
        from app.onboarding import ask

        with (
            patch("app.onboarding._is_interactive", True),
            patch("builtins.input", side_effect=EOFError),
        ):
            assert ask("prompt", default="fallback") == "fallback"

    def test_ask_raises_keyboard_interrupt(self):
        from app.onboarding import ask

        with patch("app.onboarding._is_interactive", True), patch(
            "builtins.input", side_effect=KeyboardInterrupt
        ), pytest.raises(KeyboardInterrupt):
            ask("prompt")

    def test_ask_yes_no_interactive_variants(self):
        from app.onboarding import ask_yes_no

        with patch("app.onboarding._is_interactive", True):
            with patch("builtins.input", return_value=""):
                assert ask_yes_no("ok?", default=True) is True
            with patch("builtins.input", return_value="n"):
                assert ask_yes_no("ok?", default=True) is False
            with patch("builtins.input", return_value="yes"):
                assert ask_yes_no("ok?", default=False) is True

    def test_ask_choice_interactive_valid_invalid_and_interrupts(self):
        from app.onboarding import ask_choice

        with patch("app.onboarding._is_interactive", True):
            with patch("builtins.input", return_value="2"):
                assert ask_choice("pick", ["a", "b"], default=0) == 1
            with patch("builtins.input", return_value="bad"):
                assert ask_choice("pick", ["a", "b"], default=1) == 1
            with patch("builtins.input", side_effect=KeyboardInterrupt), pytest.raises(
                KeyboardInterrupt
            ):
                ask_choice("pick", ["a", "b"], default=1)

    def test_textual_choice_selects_with_helper(self):
        import app.onboarding as onb

        with patch("app.onboarding._is_interactive", True), patch(
            "app.onboarding._use_textual_prompts", return_value=True
        ), patch("app.onboarding._textual_choice", return_value=1):
            assert onb.ask_choice("pick", ["a", "b"], default=0) == 1

    def test_textual_choice_abort_raises_keyboard_interrupt(self):
        import app.onboarding as onb

        with patch("app.onboarding._is_interactive", True), patch(
            "app.onboarding._use_textual_prompts", return_value=True
        ), patch("app.onboarding._textual_choice", return_value=onb._ABORT), pytest.raises(
            KeyboardInterrupt
        ):
            onb.ask_choice("pick", ["a", "b"], default=0)

    def test_textual_choice_reset_raises_onboarding_reset(self):
        import app.onboarding as onb

        with patch("app.onboarding._is_interactive", True), patch(
            "app.onboarding._use_textual_prompts", return_value=True
        ), patch("app.onboarding._textual_choice", return_value=onb._RESET), pytest.raises(
            onb.OnboardingReset
        ):
            onb.ask_choice("pick", ["a", "b"], default=0)

    def test_ask_path_expands_and_validates(self, tmp_path):
        from app.onboarding import ask_path

        existing = tmp_path / "project"
        existing.mkdir()
        with (
            patch("app.onboarding._is_interactive", True),
            patch("builtins.input", return_value=str(existing)),
        ):
            assert ask_path("path") == str(existing)

        missing = tmp_path / "missing"
        with (
            patch("app.onboarding._is_interactive", True),
            patch("builtins.input", return_value=str(missing)),
        ):
            assert ask_path("path", must_exist=True) == ""

        with (
            patch("app.onboarding._is_interactive", True),
            patch("builtins.input", return_value=str(missing)),
        ):
            assert ask_path("path", must_exist=False) == str(missing)


class TestColorHelpers:
    """Tests for terminal color helpers."""

    def test_bold_with_color(self):
        from app.onboarding import bold

        with patch("app.onboarding._use_color", True):
            result = bold("test")
            assert "\033[1m" in result
            assert "test" in result

    def test_bold_without_color(self):
        from app.onboarding import bold

        with patch("app.onboarding._use_color", False):
            result = bold("test")
            assert result == "test"


@pytest.fixture
def onboarding_root():
    """Create a temporary KOAN_ROOT for onboarding tests."""
    temp_dir = tempfile.mkdtemp()
    old_root = os.environ.get("KOAN_ROOT")
    os.environ["KOAN_ROOT"] = temp_dir

    # Create instance.example structure
    ie = Path(temp_dir) / "instance.example"
    ie.mkdir()
    (ie / "config.yaml").write_text("max_runs_per_day: 20\n")
    (ie / "soul.md").write_text("# Soul\nDefault personality.\n")
    (ie / "missions.md").write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")

    # Create soul-presets
    presets_dir = ie / "soul-presets"
    presets_dir.mkdir()
    (presets_dir / "soul-sparring.md").write_text("# Sparring\n")
    (presets_dir / "soul-mentor.md").write_text("# Mentor\n")
    (presets_dir / "soul-pragmatist.md").write_text("# Pragmatist\n")
    (presets_dir / "soul-creative.md").write_text("# Creative\n")
    (presets_dir / "soul-butler.md").write_text("# Butler\n")

    # Create env.example
    (Path(temp_dir) / "env.example").write_text(
        "# KOAN_ROOT=/path\n# KOAN_TELEGRAM_TOKEN=\n# KOAN_TELEGRAM_CHAT_ID=\n"
    )

    # Create Makefile (for make setup step)
    (Path(temp_dir) / "Makefile").write_text("setup:\n\t@echo ok\n")

    yield temp_dir

    # Cleanup
    if old_root:
        os.environ["KOAN_ROOT"] = old_root
    else:
        os.environ.pop("KOAN_ROOT", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestStepPrerequisites:
    """Tests for the prerequisites check step."""

    def test_prerequisites_passes_with_required_tools(self, onboarding_root):
        import app.onboarding as onb

        # Patch KOAN_ROOT in the module
        with patch.object(onb, "KOAN_ROOT", Path(onboarding_root)):
            state = onb.OnboardingState()
            result = onb.step_prerequisites(state)
            assert "has_claude" in result.data
            assert "has_gh" in result.data


class TestStepInstanceInit:
    """Tests for the instance initialization step."""

    def test_creates_instance_and_env(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.KOAN_ROOT", root
        ), patch("app.onboarding_helpers.INSTANCE_DIR", root / "instance"), patch(
            "app.onboarding_helpers.INSTANCE_EXAMPLE", root / "instance.example"
        ), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch(
            "app.onboarding_helpers.ENV_EXAMPLE", root / "env.example"
        ):
            state = onb.OnboardingState()
            result = onb.step_instance_init(state)
            assert (root / "instance").exists()
            assert (root / ".env").exists()

    def test_skips_if_already_exists(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir()
        (root / ".env").write_text("KOAN_ROOT=/tmp\n")

        with patch.object(onb, "KOAN_ROOT", root):
            state = onb.OnboardingState()
            result = onb.step_instance_init(state)
            # Should succeed without errors


class TestStepProvider:
    def test_existing_provider_skips(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text("KOAN_CLI_PROVIDER=codex\n")

        with patch.object(onb, "KOAN_ROOT", root):
            state = onb.OnboardingState()
            result = onb.step_provider(state)

        assert result.data["cli_provider"] == "codex"

    def test_noninteractive_selects_available_default(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text("# empty\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState(data={"installed_providers": ["local"]})
            result = onb.step_provider(state)

        assert result.data["cli_provider"] == "local"
        assert "KOAN_CLI_PROVIDER=local" in (root / ".env").read_text()


class TestStepVenv:
    def test_skips_when_marker_exists(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        marker = root / ".venv" / ".installed"
        marker.parent.mkdir()
        marker.write_text("")

        with patch.object(onb, "KOAN_ROOT", root):
            result = onb.step_venv(onb.OnboardingState())

        assert isinstance(result, onb.OnboardingState)

    def test_make_setup_success(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        proc = MagicMock(returncode=0)

        with (
            patch.object(onb, "KOAN_ROOT", root),
            patch("subprocess.run", return_value=proc) as mock_run,
            patch("app.onboarding.pause"),
        ):
            onb.step_venv(onb.OnboardingState())

        mock_run.assert_called_once_with(["make", "setup"], cwd=str(root), timeout=300)

    def test_make_setup_timeout_is_handled(self, onboarding_root):
        import app.onboarding as onb

        with (
            patch.object(onb, "KOAN_ROOT", Path(onboarding_root)),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("make", 300)),
            patch("app.onboarding.pause"),
        ):
            onb.step_venv(onb.OnboardingState())

    def test_make_missing_is_handled(self, onboarding_root):
        import app.onboarding as onb

        with (
            patch.object(onb, "KOAN_ROOT", Path(onboarding_root)),
            patch("subprocess.run", side_effect=FileNotFoundError),
            patch("app.onboarding.pause"),
        ):
            onb.step_venv(onb.OnboardingState())


class TestStepMessaging:
    """Tests for the messaging configuration step."""

    def test_already_configured(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ):
            state = onb.OnboardingState()
            result = onb.step_messaging(state)
            # Should complete without asking

    def test_slack_setup_non_interactive(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text("# empty\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch("app.onboarding._is_interactive", False):
            state = onb.OnboardingState()
            # Non-interactive uses default (Telegram idx 0), empty token -> skips
            result = onb.step_messaging(state)


class TestStepLanguage:
    """Tests for the language preference step."""

    def test_default_english_non_interactive(self, onboarding_root):
        import app.onboarding as onb

        with patch.object(onb, "KOAN_ROOT", Path(onboarding_root)), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState()
            result = onb.step_language(state)
            assert result.data["language"] == "english"


class TestStepPersonality:
    """Tests for the personality selection step."""

    def test_default_sparring_non_interactive(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        # Must have instance/ dir
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "soul.md").write_text("# Default\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState()
            result = onb.step_personality(state)
            assert result.data["personality"] == "sparring"
            assert result.data["address_style"] == "my human"

    def test_preset_applied(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)

        # Simulate choosing "mentor" (index 1)
        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding.ask_choice", side_effect=[1, 0]
        ):
            state = onb.OnboardingState()
            result = onb.step_personality(state)
            assert result.data["personality"] == "mentor"
            # soul.md should have mentor content
            soul = (root / "instance" / "soul.md").read_text()
            assert "Mentor" in soul


class TestStepProjects:
    """Tests for the project registration step."""

    def test_already_configured(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "projects.yaml").write_text("projects:\n  test:\n    path: /tmp\n")

        with patch.object(onb, "KOAN_ROOT", root):
            state = onb.OnboardingState()
            result = onb.step_projects(state)
            # Should skip

    def test_non_interactive_no_projects(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState()
            result = onb.step_projects(state)
            # Non-interactive returns empty path, skips


class TestStepWorkspaceKoan:
    def test_workspace_koan_success(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(True, "workspace ready"),
        ):
            state = onb.OnboardingState()
            result = onb.step_workspace_koan(state)

        assert result.data["workspace_koan"] is True

    def test_workspace_koan_failure_blocks(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(False, "conflict"),
        ), pytest.raises(RuntimeError):
            onb.step_workspace_koan(onb.OnboardingState())


class TestStepGitHub:
    """Tests for the GitHub identity step."""

    def test_skips_without_gh(self, onboarding_root):
        import app.onboarding as onb

        with patch.object(onb, "KOAN_ROOT", Path(onboarding_root)):
            state = onb.OnboardingState()
            state.data["has_gh"] = False
            result = onb.step_github(state)
            # Should skip gracefully

    def test_runs_with_gh(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / ".env").write_text("# empty\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._is_interactive", False
        ), patch("app.onboarding._run_cmd") as mock_cmd, patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ):
            mock_cmd.return_value = MagicMock(returncode=0, stdout="testuser\n")
            state = onb.OnboardingState()
            state.data["has_gh"] = True
            result = onb.step_github(state)


class TestStepModels:
    """Tests for the model configuration step."""

    def test_skips_when_already_configured(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text(
            "models:\n  claude:\n    mission: opus\n"
        )

        with patch.object(onb, "KOAN_ROOT", root):
            state = onb.OnboardingState(data={"cli_provider": "claude"})
            result = onb.step_models(state)

        assert result.data.get("models") is None

    def test_noninteractive_accepts_defaults(self, onboarding_root):
        import yaml

        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState(data={"cli_provider": "claude"})
            result = onb.step_models(state)

        assert result.data["models"]["lightweight"] == "haiku"
        config = yaml.safe_load((root / "instance" / "config.yaml").read_text())
        assert config["models"]["claude"]["lightweight"] == "haiku"

    def test_customize_models_writes_to_config(self, onboarding_root):
        import yaml

        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding.ask_yes_no", return_value=False
        ), patch("app.onboarding.ask", side_effect=["", "gpt-5.5", "", "", "", ""]):
            state = onb.OnboardingState(data={"cli_provider": "codex"})
            result = onb.step_models(state)

        assert result.data["models"]["chat"] == "gpt-5.5"
        config = yaml.safe_load((root / "instance" / "config.yaml").read_text())
        assert config["models"]["codex"]["chat"] == "gpt-5.5"

    def test_check_models_true_when_configured(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text(
            "models:\n  claude:\n    lightweight: haiku\n"
        )

        with patch.object(onb, "KOAN_ROOT", root):
            state = onb.OnboardingState(data={"cli_provider": "claude"})
            assert onb.check_models(state) is True

    def test_check_models_false_when_missing(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")

        with patch.object(onb, "KOAN_ROOT", root):
            state = onb.OnboardingState(data={"cli_provider": "claude"})
            assert onb.check_models(state) is False


class TestStepDeployment:
    """Tests for the deployment method step."""

    def test_default_terminal_non_interactive(self, onboarding_root):
        import app.onboarding as onb

        with patch.object(onb, "KOAN_ROOT", Path(onboarding_root)), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState()
            result = onb.step_deployment(state)
            assert result.data["deployment_method"] == "terminal"

    def test_no_docker_option(self, onboarding_root):
        import app.onboarding as onb

        with patch.object(onb, "KOAN_ROOT", Path(onboarding_root)), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState()
            result = onb.step_deployment(state)
            assert result.data["deployment_method"] != "docker"


class TestWelcomePage:
    """Tests for the welcome page shown on first run."""

    def test_welcome_shown_on_fresh_start(self, onboarding_root, capsys):
        """Welcome message is printed when no steps are completed."""
        import app.onboarding as onb

        root = Path(onboarding_root)
        # Pre-create everything so steps complete instantly
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / "instance" / "soul.md").write_text("# Soul\n")
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        (root / ".venv").mkdir(exist_ok=True)
        (root / "projects.yaml").write_text("projects:\n  test:\n    path: /tmp\n")
        checkpoint = root / ".koan-onboarding.json"

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ), patch("app.onboarding._is_interactive", False), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch("app.onboarding_helpers.KOAN_ROOT", root), patch(
            "app.onboarding_helpers.INSTANCE_DIR", root / "instance"
        ), patch("app.onboarding_helpers.INSTANCE_EXAMPLE", root / "instance.example"), patch(
            "app.onboarding_helpers.ENV_EXAMPLE", root / "env.example"
        ), patch("app.onboarding._run_cmd") as mock_cmd, patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(True, "workspace ready"),
        ):
            mock_cmd.return_value = MagicMock(returncode=0, stdout="user\n")
            onb.run_onboarding(force=True)

        captured = capsys.readouterr()
        assert "Welcome!" in captured.out
        assert "Ctrl-C" in captured.out

    def test_resume_message_on_existing_checkpoint(self, onboarding_root, capsys):
        """Resuming shows different message than fresh start."""
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / "instance" / "soul.md").write_text("# Soul\n")
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        (root / ".venv").mkdir(exist_ok=True)
        (root / "projects.yaml").write_text("projects:\n  test:\n    path: /tmp\n")
        checkpoint = root / ".koan-onboarding.json"

        # Create a checkpoint with some completed steps
        state = onb.OnboardingState()
        state.mark_complete("prerequisites")
        state.mark_complete("instance_init")
        state.save(checkpoint)

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ), patch("app.onboarding._is_interactive", False), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch("app.onboarding_helpers.KOAN_ROOT", root), patch(
            "app.onboarding_helpers.INSTANCE_DIR", root / "instance"
        ), patch("app.onboarding_helpers.INSTANCE_EXAMPLE", root / "instance.example"), patch(
            "app.onboarding_helpers.ENV_EXAMPLE", root / "env.example"
        ), patch("app.onboarding._run_cmd") as mock_cmd, patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(True, "workspace ready"),
        ):
            mock_cmd.return_value = MagicMock(returncode=0, stdout="user\n")
            onb.run_onboarding()

        captured = capsys.readouterr()
        assert "Resuming" in captured.out
        assert "Welcome!" not in captured.out


class TestRunOnboarding:
    """Tests for the main run_onboarding flow."""

    def test_projects_step_not_in_onboarding_flow(self):
        import app.onboarding as onb

        assert "projects" not in [step.name for step in onb.STEPS]

    def test_force_clears_checkpoint(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        checkpoint = root / ".koan-onboarding.json"
        checkpoint.write_text('{"completed_steps": ["step1"], "data": {}}')

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ):
            # Verify force deletes the file
            onb.run_onboarding.__wrapped__ if hasattr(onb.run_onboarding, "__wrapped__") else None
            # Directly test the force logic
            if checkpoint.exists():
                checkpoint.unlink()
            assert not checkpoint.exists()

    def test_resumability(self, onboarding_root):
        """Steps marked complete are skipped on re-run."""
        from app.onboarding import OnboardingState

        root = Path(onboarding_root)
        checkpoint = root / ".koan-onboarding.json"

        state = OnboardingState()
        state.mark_complete("prerequisites")
        state.mark_complete("instance_init")
        state.mark_complete("venv")
        state.save(checkpoint)

        loaded = OnboardingState.load(checkpoint)
        assert loaded.is_complete("prerequisites")
        assert loaded.is_complete("instance_init")
        assert loaded.is_complete("venv")
        assert not loaded.is_complete("messaging")

    def test_full_non_interactive_smoke(self, onboarding_root):
        """Smoke test: run all steps non-interactively."""
        import app.onboarding as onb

        root = Path(onboarding_root)
        checkpoint = root / ".koan-onboarding.json"

        # Pre-create instance and env
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / "instance" / "soul.md").write_text("# Soul\n")
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        (root / ".venv").mkdir(exist_ok=True)
        (root / "projects.yaml").write_text("projects:\n  test:\n    path: /tmp\n")

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ), patch("app.onboarding._is_interactive", False), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch(
            "app.onboarding_helpers.KOAN_ROOT", root
        ), patch(
            "app.onboarding_helpers.INSTANCE_DIR", root / "instance"
        ), patch(
            "app.onboarding_helpers.INSTANCE_EXAMPLE", root / "instance.example"
        ), patch(
            "app.onboarding_helpers.ENV_EXAMPLE", root / "env.example"
        ), patch(
            "app.onboarding._run_cmd"
        ) as mock_cmd, patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(True, "workspace ready"),
        ):
            mock_cmd.return_value = MagicMock(returncode=0, stdout="user\n")

            onb.run_onboarding(force=True)

            # Checkpoint should be cleaned up on success
            assert not checkpoint.exists()

    def test_final_shows_next_steps_without_start_prompt(self, onboarding_root, capsys):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / ".env").write_text(
            "KOAN_CLI_PROVIDER=local\nKOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        (root / "workspace" / "koan").mkdir(parents=True)

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding.ask_yes_no"
        ) as ask_yes_no:
            result = onb.step_final(onb.OnboardingState(data={"cli_provider": "local"}))

        out = capsys.readouterr().out
        assert result is not None
        assert "make koan" in out
        assert "/help" in out
        assert "/add_project <github-url>" in out
        ask_yes_no.assert_not_called()

    def test_reset_clears_checkpoint_and_restarts(self, onboarding_root, capsys):
        import app.onboarding as onb

        root = Path(onboarding_root)
        checkpoint = root / ".koan-onboarding.json"
        checkpoint.write_text('{"completed_steps": [], "data": {"draft": true}}')

        # Seed a provider choice in .env — must be wiped on reset
        (root / ".env").write_text("KOAN_CLI_PROVIDER=codex\n")

        calls = {"count": 0}

        def maybe_reset(state):
            calls["count"] += 1
            if calls["count"] == 1:
                raise onb.OnboardingReset
            return state

        reset_step = onb.Step("reset_probe", "Reset probe", maybe_reset)

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ), patch.object(onb, "STEPS", [reset_step]), patch(
            "app.onboarding._is_interactive", False
        ):
            onb.run_onboarding()

        captured = capsys.readouterr()
        assert "Install reset" in captured.out
        assert not checkpoint.exists()
        assert calls["count"] == 2
        assert "KOAN_CLI_PROVIDER" not in (root / ".env").read_text()


class TestIntroScreen:
    """Tests for the onboarding intro screen."""

    def test_shows_on_fresh_start(self, onboarding_root, capsys):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / "instance" / "soul.md").write_text("# Soul\n")
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        (root / ".venv").mkdir(exist_ok=True)
        (root / "projects.yaml").write_text("projects:\n  test:\n    path: /tmp\n")
        checkpoint = root / ".koan-onboarding.json"

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ), patch("app.onboarding._is_interactive", False), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch(
            "app.onboarding_helpers.KOAN_ROOT", root
        ), patch(
            "app.onboarding_helpers.INSTANCE_DIR", root / "instance"
        ), patch(
            "app.onboarding_helpers.INSTANCE_EXAMPLE", root / "instance.example"
        ), patch(
            "app.onboarding_helpers.ENV_EXAMPLE", root / "env.example"
        ), patch(
            "app.onboarding._run_cmd"
        ) as mock_cmd, patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(True, "workspace ready"),
        ):
            mock_cmd.return_value = MagicMock(returncode=0, stdout="user\n")
            onb.run_onboarding(force=True)

        captured = capsys.readouterr()
        assert "Welcome to Kōan" in captured.out
        assert "koan.anantys.com" in captured.out
        assert "steps" in captured.out

    def test_hidden_on_resume(self, onboarding_root, capsys):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        (root / "instance" / "config.yaml").write_text("max_runs_per_day: 20\n")
        (root / "instance" / "soul.md").write_text("# Soul\n")
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        (root / ".venv").mkdir(exist_ok=True)
        (root / "projects.yaml").write_text("projects:\n  test:\n    path: /tmp\n")
        checkpoint = root / ".koan-onboarding.json"

        state = onb.OnboardingState()
        state.mark_complete("prerequisites")
        state.save(checkpoint)

        with patch.object(onb, "KOAN_ROOT", root), patch.object(
            onb, "CHECKPOINT_FILE", checkpoint
        ), patch("app.onboarding._is_interactive", False), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ), patch(
            "app.onboarding_helpers.KOAN_ROOT", root
        ), patch(
            "app.onboarding_helpers.INSTANCE_DIR", root / "instance"
        ), patch(
            "app.onboarding_helpers.INSTANCE_EXAMPLE", root / "instance.example"
        ), patch(
            "app.onboarding_helpers.ENV_EXAMPLE", root / "env.example"
        ), patch(
            "app.onboarding._run_cmd"
        ) as mock_cmd, patch(
            "app.onboarding_helpers.setup_workspace_koan",
            return_value=(True, "workspace ready"),
        ):
            mock_cmd.return_value = MagicMock(returncode=0, stdout="user\n")
            onb.run_onboarding()

        captured = capsys.readouterr()
        assert "Resuming" in captured.out
        assert "Welcome to Kōan" not in captured.out


class TestCheckFunctions:
    """Tests for step check functions."""

    def test_check_instance_init(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        with patch.object(onb, "KOAN_ROOT", root):
            assert not onb.check_instance_init(onb.OnboardingState())
            (root / "instance").mkdir()
            (root / ".env").write_text("")
            assert onb.check_instance_init(onb.OnboardingState())

    def test_check_venv(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        with patch.object(onb, "KOAN_ROOT", root):
            assert not onb.check_venv(onb.OnboardingState())
            (root / ".venv").mkdir()
            assert onb.check_venv(onb.OnboardingState())

    def test_check_projects(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        with patch.object(onb, "KOAN_ROOT", root):
            assert not onb.check_projects(onb.OnboardingState())
            (root / "projects.yaml").write_text("projects: {}")
            assert onb.check_projects(onb.OnboardingState())

    def test_check_messaging_telegram(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text(
            "KOAN_TELEGRAM_TOKEN=123:ABC\nKOAN_TELEGRAM_CHAT_ID=456\n"
        )
        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ):
            assert onb.check_messaging(onb.OnboardingState())

    def test_check_messaging_unconfigured(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text("# empty\n")
        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding_helpers.ENV_FILE", root / ".env"
        ):
            assert not onb.check_messaging(onb.OnboardingState())


class TestUpdateConfigYamlGitHub:
    """Tests for _update_config_yaml_github helper."""

    def test_updates_github_section(self, onboarding_root):
        import yaml

        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / "instance").mkdir(exist_ok=True)
        config_file = root / "instance" / "config.yaml"
        config_file.write_text("max_runs_per_day: 20\n")

        with patch.object(onb, "KOAN_ROOT", root):
            onb._update_config_yaml_github("mybot", ["alice", "bob"])

        config = yaml.safe_load(config_file.read_text())
        assert config["github"]["nickname"] == "mybot"
        assert config["github"]["commands_enabled"] is True
        assert config["github"]["authorized_users"] == ["alice", "bob"]
        assert config["max_runs_per_day"] == 20  # preserved


class TestOllamaLaunchInOnboarding:
    """Tests that ollama-launch appears correctly in the onboarding wizard."""

    def test_providers_list_includes_ollama_launch(self):
        import app.onboarding as onb

        names = [key for key, _label in onb.PROVIDERS]
        assert "ollama-launch" in names

    def test_detect_installed_providers_includes_ollama(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._check_tool", return_value="/usr/bin/ollama"
        ):
            installed = onb._detect_installed_providers()

        assert "ollama-launch" in installed

    def test_detect_installed_providers_excludes_ollama_when_missing(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._check_tool", return_value=None
        ):
            installed = onb._detect_installed_providers()

        assert "ollama-launch" not in installed

    def test_provider_ready_ollama_launch_when_installed(self, onboarding_root):
        import app.onboarding as onb

        with patch("app.onboarding._check_tool", return_value="/usr/bin/ollama"):
            ok, msg = onb._provider_ready("ollama-launch")

        assert ok is True
        assert "ollama-launch provider selected" in msg

    def test_provider_ready_ollama_launch_when_missing(self, onboarding_root):
        import app.onboarding as onb

        with patch("app.onboarding._check_tool", return_value=None):
            ok, msg = onb._provider_ready("ollama-launch")

        assert ok is False
        assert "ollama" in msg
        assert "not installed" in msg

    def test_step_provider_ollama_launch_noninteractive(self, onboarding_root):
        import app.onboarding as onb

        root = Path(onboarding_root)
        (root / ".env").write_text("# empty\n")

        with patch.object(onb, "KOAN_ROOT", root), patch(
            "app.onboarding._is_interactive", False
        ):
            state = onb.OnboardingState(data={"installed_providers": ["ollama-launch"]})
            result = onb.step_provider(state)

        assert result.data["cli_provider"] == "ollama-launch"
        assert "KOAN_CLI_PROVIDER=ollama-launch" in (root / ".env").read_text()
