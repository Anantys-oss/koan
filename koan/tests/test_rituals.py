"""Tests for rituals module."""

import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest

from app.rituals import load_template, should_run_morning, should_run_evening, run_ritual


@pytest.fixture
def instance_dir(tmp_path):
    """Create a minimal instance directory for testing."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (tmp_path / "soul.md").write_text("You are Kōan.")
    return tmp_path


@pytest.fixture
def prompt_dir(tmp_path):
    """Create a minimal prompt directory with ritual templates."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()

    (prompts / "morning-brief.md").write_text(
        "Morning brief template. Instance: {INSTANCE}"
    )
    (prompts / "evening-debrief.md").write_text(
        "Evening debrief template. Instance: {INSTANCE}"
    )

    def _get_prompt_path(name):
        return prompts / f"{name}.md"

    with patch("app.rituals.get_prompt_path", side_effect=_get_prompt_path):
        yield prompts


class TestShouldRunMorning:
    def test_first_run_triggers_morning(self):
        assert should_run_morning(1) is True

    def test_second_run_no_morning(self):
        assert should_run_morning(2) is False

    def test_later_run_no_morning(self):
        assert should_run_morning(5) is False


class TestShouldRunEvening:
    def test_last_run_triggers_evening(self):
        assert should_run_evening(10, 10) is True

    def test_not_last_run_no_evening(self):
        assert should_run_evening(5, 10) is False

    def test_first_run_no_evening_unless_max_is_1(self):
        assert should_run_evening(1, 10) is False
        assert should_run_evening(1, 1) is True


class TestLoadTemplate:
    def test_loads_morning_template(self, prompt_dir, instance_dir):
        template = load_template("morning-brief", instance_dir)
        assert "Morning brief template" in template
        assert str(instance_dir) in template

    def test_loads_evening_template(self, prompt_dir, instance_dir):
        template = load_template("evening-debrief", instance_dir)
        assert "Evening debrief template" in template
        assert str(instance_dir) in template

    def test_replaces_instance_placeholder(self, prompt_dir, instance_dir):
        template = load_template("morning-brief", instance_dir)
        assert "{INSTANCE}" not in template
        assert str(instance_dir) in template

    def test_raises_for_missing_template(self, prompt_dir, instance_dir):
        with pytest.raises(FileNotFoundError):
            load_template("nonexistent", instance_dir)


class TestRunRitual:
    @patch("app.rituals.subprocess.run")
    def test_morning_calls_claude(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="Morning message", stderr="")

        result = run_ritual("morning", instance_dir)

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "claude"
        assert "-p" in call_args
        # KOAN_ROOT session: suppress contributor project tooling (#2379).
        assert "--setting-sources" in call_args
        assert call_args[call_args.index("--setting-sources") + 1] == "user"

    @patch("app.rituals.subprocess.run")
    def test_evening_calls_claude(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="Evening message", stderr="")

        result = run_ritual("evening", instance_dir)

        assert result is True
        mock_run.assert_called_once()

    @patch("app.rituals.subprocess.run")
    def test_handles_claude_failure(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        result = run_ritual("morning", instance_dir)

        assert result is False

    @patch("app.rituals.subprocess.run")
    def test_handles_timeout(self, mock_run, prompt_dir, instance_dir):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=90)

        result = run_ritual("morning", instance_dir)

        assert result is False

    def test_returns_false_for_missing_template(self, tmp_path, instance_dir):
        # Point get_prompt_path to a directory without templates
        with patch("app.rituals.get_prompt_path", side_effect=lambda n: tmp_path / f"{n}.md"):
            result = run_ritual("morning", instance_dir)

        assert result is False

    @patch("app.rituals.subprocess.run")
    def test_handles_generic_exception(self, mock_run, prompt_dir, instance_dir):
        mock_run.side_effect = OSError("Permission denied")

        result = run_ritual("morning", instance_dir)
        assert result is False

    @patch("app.rituals.subprocess.run")
    def test_empty_stdout(self, mock_run, prompt_dir, instance_dir):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = run_ritual("morning", instance_dir)
        assert result is True

    @patch("app.rituals.subprocess.run")
    def test_max_turns_is_7(self, mock_run, prompt_dir, instance_dir):
        """Regression: max_turns=3 was too low for ritual prompts (need 5+ tool calls)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        run_ritual("morning", instance_dir)

        cmd = mock_run.call_args[0][0]
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "7"

    @patch("app.rituals.subprocess.run")
    def test_strips_max_turns_error_from_stdout(self, mock_run, prompt_dir, instance_dir, capsys):
        """Regression: 'Error: Reached max turns (N)' leaked to console."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Morning message\nError: Reached max turns (3)\n",
            stderr=""
        )

        result = run_ritual("morning", instance_dir)
        assert result is True
        captured = capsys.readouterr()
        assert "max turns" not in captured.out.lower()
        assert "Morning message" in captured.out

    @patch("app.rituals.subprocess.run")
    def test_strips_max_turns_error_from_stderr(self, mock_run, prompt_dir, instance_dir, capsys):
        """Regression: max turns error in stderr should also be filtered."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Reached max turns (3)"
        )

        result = run_ritual("morning", instance_dir)
        assert result is False
        captured = capsys.readouterr()
        assert "max turns" not in captured.err.lower()

    @patch("app.rituals.subprocess.run")
    def test_only_max_turns_error_in_stdout_prints_nothing(self, mock_run, prompt_dir, instance_dir, capsys):
        """When stdout is only the max turns error, output should be empty."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Error: Reached max turns (7)\n",
            stderr=""
        )

        result = run_ritual("morning", instance_dir)
        assert result is True
        captured = capsys.readouterr()
        # Should print the completed message but not the error
        assert "ritual completed" in captured.out
        assert "max turns" not in captured.out.lower()


class TestRitualLanguagePreference:
    """A configured language.json must override soul.md's language in rituals.

    The prompt is delivered to the CLI via a stdin temp file, so we capture its
    content through the ``stdin`` file handle at call time.
    """

    @staticmethod
    def _capturing_run(sink):
        def _run(cmd, **kwargs):
            stdin = kwargs.get("stdin")
            if stdin is not None and hasattr(stdin, "read"):
                sink["prompt"] = stdin.read()
            return MagicMock(returncode=0, stdout="ok", stderr="")
        return _run

    @patch("app.rituals.subprocess.run")
    def test_language_preference_injected_into_prompt(
        self, mock_run, prompt_dir, instance_dir, tmp_path, monkeypatch
    ):
        """With language.json=english, the morning prompt carries the english override."""
        sink = {}
        mock_run.side_effect = self._capturing_run(sink)
        (tmp_path / "instance").mkdir()
        (tmp_path / "instance" / "language.json").write_text('{"language": "english"}')
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))

        run_ritual("morning", instance_dir)

        assert "reply in english" in sink["prompt"].lower()

    @patch("app.rituals.subprocess.run")
    def test_no_language_instruction_when_unset(
        self, mock_run, prompt_dir, instance_dir, tmp_path, monkeypatch
    ):
        """With no language.json, no override text is appended to the prompt."""
        sink = {}
        mock_run.side_effect = self._capturing_run(sink)
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))  # no instance/language.json

        run_ritual("evening", instance_dir)

        assert "user-configured language preference" not in sink["prompt"]


class TestRitualsCLI:
    """CLI tests call main() directly to avoid runpy re-import issues."""

    @patch("app.rituals.subprocess.run")
    def test_main_morning(self, mock_subprocess, prompt_dir, instance_dir):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="Morning!", stderr="")

        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "morning", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    @patch("app.rituals.subprocess.run")
    def test_main_evening(self, mock_subprocess, prompt_dir, instance_dir):
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="Evening!", stderr="")

        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "evening", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_main_missing_args(self):
        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_invalid_ritual_type(self, instance_dir):
        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "midnight", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_main_missing_instance_dir(self, tmp_path):
        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "morning", str(tmp_path / "nonexistent")]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    @patch("app.rituals.subprocess.run")
    def test_main_failure_exits_1(self, mock_subprocess, prompt_dir, instance_dir):
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="", stderr="Error")

        from app.rituals import main
        with patch.object(sys, "argv", ["rituals.py", "morning", str(instance_dir)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
