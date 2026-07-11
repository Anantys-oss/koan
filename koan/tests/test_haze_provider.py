"""Tests for the Haze CLI provider (specs/006-haze-cli-provider).

All provider behavior is exercised against the recorded haze >= 0.7.0 output
samples in ``tests/haze_samples.py`` — never a live ``haze`` subprocess.
"""

import json
import os
import subprocess
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
        # Disabled pending the upstream haze isTTY stdin-fallback fix — see
        # HazeProvider.supports_stdin_prompt_passing.
        assert self.provider.supports_stdin_prompt_passing() is False
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
#
# The rewrite is implemented and tested but DORMANT: stdin passing is
# disabled until upstream haze fixes its `isTTY === false` stdin gate
# (Node reports undefined for pipes, so piped runs go interactive).
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

    def test_prepare_prompt_file_keeps_prompt_in_argv(self, tmp_path, monkeypatch):
        """While stdin passing is disabled, the prompt must stay in argv."""
        monkeypatch.setenv("KOAN_TMP_DIR", str(tmp_path))
        from app.cli_exec import prepare_prompt_file

        cmd = self.provider.build_command("large prompt", output_format="stream-json")
        new_cmd, prompt_path = prepare_prompt_file(cmd, provider=self.provider)
        assert prompt_path is None
        assert new_cmd == cmd
        assert new_cmd[-2:] == ["-p", "large prompt"]

    def test_prepare_prompt_file_uses_stdin_once_reenabled(self, tmp_path, monkeypatch):
        """The dormant rewrite works end-to-end when stdin passing is re-enabled."""
        monkeypatch.setenv("KOAN_TMP_DIR", str(tmp_path))
        from app.cli_exec import prepare_prompt_file

        cmd = self.provider.build_command("large prompt", output_format="stream-json")
        with patch.object(HazeProvider, "supports_stdin_prompt_passing", return_value=True):
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


# ---------------------------------------------------------------------------
# T013 — stream usage snapshot (camelCase envelope -> sidecar accounting)
# ---------------------------------------------------------------------------

class TestHazeStreamUsage:
    def _envelope_event(self, transcript: str) -> dict:
        return json.loads(transcript.strip().splitlines()[-1])

    def test_usage_snapshot_from_camelcase_envelope(self):
        from app.provider import _usage_snapshot_from_event

        snapshot = _usage_snapshot_from_event(
            self._envelope_event(haze_samples.STREAM_SUCCESS)
        )
        assert snapshot == {
            "input_tokens": 5230 - 1200,
            "output_tokens": 410,
            "cache_read_input_tokens": 1200,
            "cache_creation_input_tokens": 300,
            "model": "unknown",
        }

    def test_all_zero_usage_yields_no_snapshot(self):
        from app.provider import _usage_snapshot_from_event

        assert _usage_snapshot_from_event(
            json.loads(haze_samples.JSON_ENVELOPE_ZERO_USAGE)
        ) is None

    def test_progress_events_yield_no_snapshot(self):
        from app.provider import _usage_snapshot_from_event

        for line in haze_samples.STREAM_SUCCESS.strip().splitlines()[:-1]:
            assert _usage_snapshot_from_event(json.loads(line)) is None

    def test_replay_persists_usage_sidecar(self, tmp_path, monkeypatch):
        sidecar = tmp_path / "stream-usage.json"
        monkeypatch.setenv("KOAN_STREAM_USAGE_FILE", str(sidecar))
        _replay(haze_samples.STREAM_SUCCESS)
        persisted = json.loads(sidecar.read_text())
        assert persisted["input_tokens"] == 5230 - 1200
        assert persisted["output_tokens"] == 410
        assert persisted["cache_read_input_tokens"] == 1200
        assert persisted["cache_creation_input_tokens"] == 300


# ---------------------------------------------------------------------------
# T015 — failure classification & status mapping
# ---------------------------------------------------------------------------

class TestHazeQuotaDetection:
    def setup_method(self):
        self.provider = HazeProvider()

    def test_stderr_quota_patterns_trusted(self):
        for sample in (
            haze_samples.STDERR_QUOTA_429,
            haze_samples.STDERR_QUOTA_INSUFFICIENT,
        ):
            assert self.provider.detect_quota_exhaustion(
                stderr_text=sample, exit_code=1,
            ) is True

    def test_stderr_quota_detected_even_with_exit_zero(self):
        assert self.provider.detect_quota_exhaustion(
            stderr_text=haze_samples.STDERR_QUOTA_429, exit_code=0,
        ) is True

    def test_stdout_quota_envelope_detected_on_failure(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text=haze_samples.STDOUT_QUOTA_ENVELOPE, exit_code=1,
        ) is True

    def test_benign_prose_on_success_never_quota(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text=haze_samples.STDOUT_BENIGN_PROSE_ENVELOPE, exit_code=0,
        ) is False

    def test_plain_failure_is_not_quota(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text=haze_samples.STREAM_FAILED, exit_code=1,
        ) is False

    def test_no_provider_configured_is_not_quota(self):
        assert self.provider.detect_quota_exhaustion(
            stderr_text=haze_samples.STDERR_NO_PROVIDER, exit_code=1,
        ) is False


class TestHazeAuthDetection:
    def setup_method(self):
        self.provider = HazeProvider()

    def test_stderr_auth_patterns(self):
        for sample in (haze_samples.STDERR_AUTH_401, haze_samples.STDERR_AUTH_KEY):
            assert self.provider.detect_auth_failure(
                stderr_text=sample, exit_code=1,
            ) is True

    def test_exit_zero_never_auth_failure(self):
        assert self.provider.detect_auth_failure(
            stderr_text=haze_samples.STDERR_AUTH_401, exit_code=0,
        ) is False

    def test_bad_model_selector_is_not_auth(self):
        assert self.provider.detect_auth_failure(
            stderr_text=haze_samples.STDERR_BAD_MODEL, exit_code=1,
        ) is False

    def test_quota_error_is_not_auth(self):
        assert self.provider.detect_auth_failure(
            stderr_text=haze_samples.STDERR_QUOTA_429, exit_code=1,
        ) is False


class TestHazeStatusMapping:
    """failed/aborted terminal statuses surface as failures, never success."""

    def test_failed_run_raises_with_envelope_error(self, monkeypatch):
        monkeypatch.delenv("KOAN_STREAM_USAGE_FILE", raising=False)
        with pytest.raises(RuntimeError) as exc:
            _replay(haze_samples.STREAM_FAILED, returncode=1)
        assert "Model call failed after 3 attempts" in str(exc.value)

    def test_aborted_run_raises(self, monkeypatch):
        monkeypatch.delenv("KOAN_STREAM_USAGE_FILE", raising=False)
        with pytest.raises(RuntimeError):
            _replay(haze_samples.STREAM_ABORTED, returncode=1)

    def test_fatal_context_overflow_feeds_error_preview(self):
        from app.provider import _extract_provider_error_preview

        preview = _extract_provider_error_preview(
            haze_samples.STREAM_CONTEXT_OVERFLOW_FATAL
        )
        assert preview == "context window exceeded"

    def test_recovered_context_overflow_not_an_error(self):
        from app.provider import _extract_provider_error_preview

        assert _extract_provider_error_preview(
            haze_samples.STREAM_CONTEXT_OVERFLOW_RECOVERED
        ) == ""

    def test_complete_envelope_not_an_error(self):
        from app.provider import _extract_provider_error_preview

        assert _extract_provider_error_preview(
            haze_samples.JSON_ENVELOPE_SUCCESS
        ) == ""


# ---------------------------------------------------------------------------
# T019 — pre-flight quota probe
# ---------------------------------------------------------------------------

class TestHazeQuotaProbe:
    def setup_method(self):
        self.provider = HazeProvider()

    def _completed(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["haze"], returncode=returncode, stdout=stdout, stderr=stderr,
        )

    def test_probe_success(self):
        with patch(
            "app.cli_exec.run_cli",
            return_value=self._completed(stdout=haze_samples.JSON_ENVELOPE_SUCCESS),
        ) as run_cli:
            ok, detail = self.provider.check_quota_available("/tmp")
        assert (ok, detail) == (True, "")
        cmd = run_cli.call_args[0][0]
        assert cmd[0] == "haze"
        assert "--output" in cmd and "json" in cmd
        assert cmd[-2:] == ["-p", "ok"]

    def test_probe_quota_exhaustion(self):
        with patch(
            "app.cli_exec.run_cli",
            return_value=self._completed(
                returncode=1, stdout=haze_samples.STDOUT_QUOTA_ENVELOPE,
            ),
        ):
            ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is False
        assert "rate limit" in detail

    def test_probe_auth_failure(self):
        with patch(
            "app.cli_exec.run_cli",
            return_value=self._completed(
                returncode=1, stderr=haze_samples.STDERR_AUTH_401,
            ),
        ):
            ok, detail = self.provider.check_quota_available("/tmp")
        assert ok is False
        assert "401" in detail

    def test_probe_timeout_never_blocks(self):
        with patch(
            "app.cli_exec.run_cli",
            side_effect=subprocess.TimeoutExpired(cmd=["haze"], timeout=15),
        ):
            assert self.provider.check_quota_available("/tmp") == (True, "")

    def test_probe_unexpected_error_never_blocks(self):
        with patch("app.cli_exec.run_cli", side_effect=OSError("boom")):
            assert self.provider.check_quota_available("/tmp") == (True, "")
