"""Tests for the Haze CLI provider (specs/006-haze-cli-provider).

All provider behavior is exercised against the recorded haze >= 0.7.0 output
samples in ``tests/haze_samples.py`` — never a live ``haze`` subprocess.
"""

import json
import os
from unittest.mock import patch

import pytest

import app.provider.haze as haze_module
from app.provider.haze import HazeProvider
from tests import haze_samples


@pytest.fixture(autouse=True)
def _fresh_warn_state(monkeypatch):
    """Isolate the once-per-process unsupported-feature warning set."""
    monkeypatch.setattr(haze_module, "_WARNED_UNSUPPORTED", set())


# ---------------------------------------------------------------------------
# Package structure
# ---------------------------------------------------------------------------

class TestHazePackageStructure:
    def test_import_from_provider_package(self):
        from app.provider import HazeProvider as PackageHaze
        assert PackageHaze.name == "haze"

    def test_haze_in_provider_registry(self):
        from app.provider import _PROVIDERS
        assert "haze" in _PROVIDERS

    def test_registry_creates_haze_instance(self):
        from app.provider import _PROVIDERS
        provider = _PROVIDERS["haze"]()
        assert isinstance(provider, HazeProvider)
        assert provider.name == "haze"


# ---------------------------------------------------------------------------
# Provider basics & capability profile
# ---------------------------------------------------------------------------

class TestHazeProviderBasics:
    def setup_method(self):
        self.provider = HazeProvider()

    def test_binary_default(self):
        assert self.provider.binary() == "haze"

    def test_binary_override_absolute(self):
        provider = HazeProvider(binary_path="/opt/tools/haze-nightly")
        assert provider.binary() == "/opt/tools/haze-nightly"

    def test_binary_override_bare_name_stays_path_lookup(self):
        provider = HazeProvider(binary_path="haze-nightly")
        assert provider.binary() == "haze-nightly"

    def test_is_available_uses_which(self):
        with patch("app.provider.haze.shutil.which", return_value="/usr/bin/haze"):
            assert self.provider.is_available() is True
        with patch("app.provider.haze.shutil.which", return_value=None):
            assert self.provider.is_available() is False

    def test_invocation_lock_name(self):
        assert self.provider.invocation_lock_name() == "haze-cli"

    def test_capability_profile(self):
        assert self.provider.supports_stream_json() is True
        assert self.provider.supports_stdin_prompt_passing() is True
        assert self.provider.supports_session_resume() is False
        assert self.provider.supports_system_prompt_file() is False
        assert self.provider.supports_last_message_file() is False
        assert self.provider.has_api_quota() is True


# ---------------------------------------------------------------------------
# T006 — command construction
# ---------------------------------------------------------------------------

class TestHazeCommandConstruction:
    def setup_method(self):
        self.provider = HazeProvider()

    def test_build_prompt_args(self):
        assert self.provider.build_prompt_args("do it") == ["-p", "do it"]

    def test_build_model_args(self):
        assert self.provider.build_model_args("openai:gpt-5") == ["-m", "openai:gpt-5"]
        assert self.provider.build_model_args("") == []

    def test_build_model_args_fallback_warns_and_skips(self):
        with patch("app.provider.haze.log_safe") as log:
            flags = self.provider.build_model_args("openai:gpt-5", fallback="openai:gpt-4o")
        assert flags == ["-m", "openai:gpt-5"]
        assert log.called
        assert "fallback" in log.call_args[0][1]

    def test_build_output_args_mapping(self):
        assert self.provider.build_output_args("stream-json") == ["--output", "stream-json"]
        assert self.provider.build_output_args("json") == ["--output", "json"]
        assert self.provider.build_output_args("") == []

    def test_build_command_minimal(self):
        cmd = self.provider.build_command("hello world", output_format="stream-json")
        assert cmd == ["haze", "--output", "stream-json", "-p", "hello world"]

    def test_build_command_with_model(self):
        cmd = self.provider.build_command(
            "hello", model="openai:gpt-5", output_format="stream-json",
        )
        assert cmd == [
            "haze", "-m", "openai:gpt-5", "--output", "stream-json", "-p", "hello",
        ]

    def test_build_command_prompt_args_last(self):
        cmd = self.provider.build_command(
            "hello", model="openai:gpt-5", output_format="stream-json",
        )
        assert cmd[-2:] == ["-p", "hello"]

    def test_build_command_prepends_system_prompt(self):
        cmd = self.provider.build_command(
            "user ask", system_prompt="be brief", output_format="stream-json",
        )
        assert cmd[-1] == "be brief\n\nuser ask"

    def test_build_command_system_prompt_file_warns_falls_back_inline(self):
        with patch("app.provider.haze.log_safe") as log:
            cmd = self.provider.build_command(
                "user ask",
                system_prompt="be brief",
                system_prompt_file="/tmp/sys.md",
            )
        assert cmd[-1] == "be brief\n\nuser ask"
        assert any("system prompt file" in c.args[1] for c in log.call_args_list)

    def test_build_command_resume_warns_and_skips(self):
        with patch("app.provider.haze.log_safe") as log:
            cmd = self.provider.build_command("hello", resume_session_id="sess-1")
        assert "sess-1" not in cmd
        assert any("resume" in c.args[1] for c in log.call_args_list)

    def test_unsupported_builders_return_empty(self):
        assert self.provider.build_tool_args(["Bash"], ["Write"]) == []
        assert self.provider.build_max_turns_args(10) == []
        assert self.provider.build_mcp_args(["/tmp/mcp.json"]) == []
        assert self.provider.build_plugin_args(["/tmp/plugins"]) == []
        assert self.provider.build_effort_args("high") == []

    def test_unsupported_builders_warn(self):
        with patch("app.provider.haze.log_safe") as log:
            self.provider.build_tool_args(["Bash"])
            self.provider.build_max_turns_args(10)
            self.provider.build_mcp_args(["/tmp/mcp.json"])
            self.provider.build_plugin_args(["/tmp/plugins"])
            self.provider.build_effort_args("high")
        warned = " | ".join(c.args[1] for c in log.call_args_list)
        for feature in ("tool", "max turns", "MCP", "plugin", "effort"):
            assert feature in warned, f"missing warning for {feature}"

    def test_structural_warnings_fire_once_per_process(self):
        with patch("app.provider.haze.log_safe") as log:
            self.provider.build_tool_args(["Bash"])
            self.provider.build_tool_args(["Bash"])
            self.provider.build_max_turns_args(10)
            self.provider.build_max_turns_args(5)
        tool_warnings = [c for c in log.call_args_list if "tool" in c.args[1]]
        turn_warnings = [c for c in log.call_args_list if "max turns" in c.args[1]]
        assert len(tool_warnings) == 1
        assert len(turn_warnings) == 1

    def test_permission_gating_unenforceable_warns_once(self):
        with patch("app.provider.haze.log_safe") as log:
            self.provider.build_command("hello", skip_permissions=False)
            self.provider.build_command("hello", skip_permissions=False)
        gate_warnings = [c for c in log.call_args_list if "permission" in c.args[1]]
        assert len(gate_warnings) == 1

    def test_skip_permissions_true_does_not_warn(self):
        with patch("app.provider.haze.log_safe") as log:
            self.provider.build_command("hello", skip_permissions=True)
        assert not any("permission" in c.args[1] for c in log.call_args_list)

    def test_empty_inputs_do_not_warn(self):
        with patch("app.provider.haze.log_safe") as log:
            self.provider.build_tool_args(None, None)
            self.provider.build_max_turns_args(0)
            self.provider.build_mcp_args(None)
            self.provider.build_plugin_args(None)
            self.provider.build_effort_args("")
        assert not log.called


# ---------------------------------------------------------------------------
# T007 — stdin prompt delivery (flag-removal rewrite)
# ---------------------------------------------------------------------------

class TestHazeStdinRewrite:
    def setup_method(self):
        self.provider = HazeProvider()

    def test_rewrite_removes_prompt_flag_entirely(self):
        cmd = ["haze", "-m", "openai:gpt-5", "--output", "stream-json", "-p", "big prompt"]
        rewritten, prompt = self.provider.rewrite_prompt_for_stdin(cmd, "@stdin")
        assert prompt == "big prompt"
        assert rewritten == ["haze", "-m", "openai:gpt-5", "--output", "stream-json"]
        assert "-p" not in rewritten
        assert "@stdin" not in rewritten

    def test_rewrite_without_prompt_flag_is_noop(self):
        cmd = ["haze", "--output", "stream-json"]
        rewritten, prompt = self.provider.rewrite_prompt_for_stdin(cmd, "@stdin")
        assert prompt is None
        assert rewritten == cmd

    def test_rewrite_with_dangling_prompt_flag_is_noop(self):
        cmd = ["haze", "--output", "stream-json", "-p"]
        rewritten, prompt = self.provider.rewrite_prompt_for_stdin(cmd, "@stdin")
        assert prompt is None
        assert rewritten == cmd

    def test_prepare_prompt_file_integration(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_TMP_DIR", str(tmp_path))
        from app.cli_exec import prepare_prompt_file

        cmd = self.provider.build_command("large prompt", output_format="stream-json")
        new_cmd, prompt_path = prepare_prompt_file(cmd, provider=self.provider)
        try:
            assert prompt_path is not None
            assert "-p" not in new_cmd
            assert new_cmd == ["haze", "--output", "stream-json"]
            with open(prompt_path) as f:
                assert f.read() == "large prompt"
        finally:
            if prompt_path:
                os.unlink(prompt_path)


# ---------------------------------------------------------------------------
# T008 — streaming replay through run_command_streaming (recorded transcript)
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, lines=None, read_value=""):
        self._lines = list(lines or [])
        self._read_value = read_value

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._read_value

    def close(self):
        pass


class _FakeProc:
    def __init__(self, transcript: str, returncode: int = 0, stderr_text: str = ""):
        self.stdout = _FakeStream(transcript.splitlines(keepends=True))
        self.stderr = _FakeStream(read_value=stderr_text)
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


def _replay(transcript: str, returncode: int = 0, stderr_text: str = ""):
    """Run a recorded haze transcript through run_command_streaming."""
    import app.provider as provider_pkg

    fake_proc = _FakeProc(transcript, returncode=returncode, stderr_text=stderr_text)
    with patch.object(
        provider_pkg,
        "_resolve_role_provider_and_models",
        return_value=(HazeProvider(), {}),
    ), patch("app.cli_exec.popen_cli", return_value=(fake_proc, lambda: None)) as popen:
        result = provider_pkg.run_command_streaming(
            prompt="Summarize the repo layout",
            project_path="/tmp",
            allowed_tools=[],
            timeout=30,
        )
    return result, popen


class TestHazeStreamingReplay:
    def test_success_replay_returns_envelope_result(self, monkeypatch, capsys):
        monkeypatch.delenv("KOAN_STREAM_USAGE_FILE", raising=False)
        result, popen = _replay(haze_samples.STREAM_SUCCESS)
        assert result == haze_samples.STREAM_SUCCESS_RESULT_TEXT

        out_lines = capsys.readouterr().out.splitlines()
        cli_lines = [line for line in out_lines if line.startswith("[cli]")]
        # One liveness line per fixture event (+ the session-start banner):
        # every stdout line resets the run.py watchdog.
        n_events = len(haze_samples.STREAM_SUCCESS.strip().splitlines())
        assert len(cli_lines) >= n_events
        joined = "\n".join(cli_lines)
        assert "listFiles" in joined
        assert "complete" in joined

    def test_success_replay_command_uses_stream_json(self):
        _, popen = _replay(haze_samples.STREAM_SUCCESS)
        cmd = popen.call_args[0][0]
        assert cmd[0] == "haze"
        assert ["--output", "stream-json"] == cmd[-4:-2]

    def test_truncated_stream_falls_back_to_message_end_text(self, monkeypatch):
        monkeypatch.delenv("KOAN_STREAM_USAGE_FILE", raising=False)
        result, _ = _replay(haze_samples.STREAM_TRUNCATED, returncode=0)
        assert result == haze_samples.STREAM_TRUNCATED_PARTIAL_TEXT

    def test_hidden_segments_excluded_from_fallback(self, monkeypatch):
        monkeypatch.delenv("KOAN_STREAM_USAGE_FILE", raising=False)
        # Strip the terminal envelope + turn_end so the fallback path is used.
        lines = haze_samples.STREAM_HIDDEN_SEGMENT.strip().splitlines()
        truncated = "\n".join(lines[:-2]) + "\n"
        result, _ = _replay(truncated, returncode=0)
        assert result == "Visible answer."
        assert "internal scratch note" not in result


# ---------------------------------------------------------------------------
# T011 unit coverage — haze event summaries (watchdog liveness lines)
# ---------------------------------------------------------------------------

class TestHazeStreamEventSummaries:
    def _summarize(self, line: str) -> str:
        from app.provider import _summarize_stream_event
        return _summarize_stream_event(json.loads(line))

    def _event_lines(self, transcript: str):
        return transcript.strip().splitlines()

    def test_message_end_shows_text_preview(self):
        summary = self._summarize(
            '{"type":"message_end","id":"m1","text":"The repo has two packages.","at":"t"}'
        )
        assert summary.startswith("[cli] assistant")
        assert "The repo has two packages." in summary

    def test_hidden_message_end_shows_no_text(self):
        summary = self._summarize(
            '{"type":"message_end","id":"m1","text":"secret scratch","hidden":true,"at":"t"}'
        )
        assert summary.startswith("[cli] assistant")
        assert "secret scratch" not in summary

    def test_message_update_is_cheap_tag_without_text(self):
        summary = self._summarize(
            '{"type":"message_update","id":"m1","text":"long cumulative snapshot","at":"t"}'
        )
        assert summary.startswith("[cli] assistant")
        assert "long cumulative snapshot" not in summary

    def test_tool_end_includes_success_and_duration(self):
        summary = self._summarize(
            '{"type":"tool_end","id":"t1","name":"listFiles","success":true,"durationMs":412,"at":"t"}'
        )
        assert "listFiles" in summary
        assert "412" in summary

    def test_tool_end_failure_includes_error(self):
        summary = self._summarize(
            '{"type":"tool_end","id":"t1","name":"bash","success":false,"durationMs":88,"error":"exit 1","at":"t"}'
        )
        assert "bash" in summary
        assert "exit 1" in summary

    def test_retry_includes_attempt_and_error(self):
        line = self._event_lines(haze_samples.STREAM_FAILED)[1]
        summary = self._summarize(line)
        assert "retry" in summary.lower()
        assert "1/3" in summary
        assert "500" in summary

    def test_context_overflow_recovered_and_fatal(self):
        recovered = self._summarize(haze_samples.STREAM_CONTEXT_OVERFLOW_RECOVERED)
        fatal = self._summarize(haze_samples.STREAM_CONTEXT_OVERFLOW_FATAL)
        assert "context_overflow" in recovered
        assert "recovered" in recovered
        assert "context_overflow" in fatal
        assert "context window exceeded" in fatal

    def test_result_envelope_shows_status(self):
        line = self._event_lines(haze_samples.STREAM_SUCCESS)[-1]
        summary = self._summarize(line)
        assert summary.startswith("[cli] result")
        assert "complete" in summary

    def test_turn_end_shows_status_via_generic_fallback(self):
        line = self._event_lines(haze_samples.STREAM_ABORTED)[-2]
        summary = self._summarize(line)
        assert "aborted" in summary
