"""Tests for the Grok Build CLI provider (issue #2400).

All stream/usage behavior is exercised against recorded samples in
``tests/grok_samples.py`` — never a live ``grok`` subprocess.
"""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

import app.provider.grok as grok_module
from app.provider import (
    _extract_assistant_text_chunks,
    _extract_result_text,
    _summarize_stream_event,
    _usage_snapshot_from_event,
)
from app.provider.grok import GrokProvider
from tests import grok_samples


@pytest.fixture(autouse=True)
def _fresh_warn_state(monkeypatch):
    """Isolate the once-per-process unsupported-feature warning set."""
    monkeypatch.setattr(grok_module, "_WARNED_UNSUPPORTED", set())


def _parse_ndjson(blob: str):
    events = []
    for line in blob.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        events.append(json.loads(stripped))
    return events


def _join_stream_text(events) -> str:
    """Mirror run_command_streaming delta/block accumulation for fixtures."""
    text_lines: list[str] = []
    deltas: list[str] = []
    for event in events:
        chunks = _extract_assistant_text_chunks(event)
        if event.get("type") == "text" and isinstance(event.get("data"), str):
            deltas.extend(chunks)
        else:
            if deltas:
                text_lines.append("".join(deltas))
                deltas.clear()
            text_lines.extend(chunks)
        result = _extract_result_text(event)
        if result is not None:
            # Prefer explicit result text when present (Claude/Haze shapes).
            return result
    if deltas:
        text_lines.append("".join(deltas))
    return "\n".join(text_lines)


# ---------------------------------------------------------------------------
# Package structure
# ---------------------------------------------------------------------------

class TestGrokPackageStructure:
    def test_import_from_provider_package(self):
        from app.provider import GrokProvider as PackageGrok
        assert PackageGrok.name == "grok"

    def test_grok_in_provider_registry(self):
        from app.provider import _PROVIDERS
        assert "grok" in _PROVIDERS

    def test_registry_creates_grok_instance(self):
        from app.provider import _PROVIDERS
        provider = _PROVIDERS["grok"]()
        assert isinstance(provider, GrokProvider)
        assert provider.name == "grok"

    def test_known_providers_includes_grok(self):
        from app.provider import known_providers
        assert "grok" in known_providers()


# ---------------------------------------------------------------------------
# Provider basics & capability profile
# ---------------------------------------------------------------------------

class TestGrokProviderBasics:
    def setup_method(self):
        self.provider = GrokProvider()

    def test_binary_default(self):
        assert self.provider.binary() == "grok"

    def test_binary_override_absolute(self):
        provider = GrokProvider(binary_path="/opt/tools/grok-nightly")
        assert provider.binary() == "/opt/tools/grok-nightly"

    def test_binary_override_bare_name_stays_path_lookup(self):
        provider = GrokProvider(binary_path="grok-nightly")
        assert provider.binary() == "grok-nightly"

    def test_is_available_uses_which(self):
        with patch("app.provider.grok.shutil.which", return_value="/usr/bin/grok"):
            assert self.provider.is_available() is True
        with patch("app.provider.grok.shutil.which", return_value=None):
            assert self.provider.is_available() is False

    def test_invocation_lock_name(self):
        assert self.provider.invocation_lock_name() == "grok-cli"

    def test_capability_profile(self):
        assert self.provider.supports_stream_json() is True
        assert self.provider.supports_stdin_prompt_passing() is False
        assert self.provider.supports_session_resume() is True
        assert self.provider.supports_system_prompt_file() is False
        assert self.provider.supports_last_message_file() is False
        assert self.provider.has_api_quota() is True


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestGrokCommandConstruction:
    def setup_method(self):
        self.provider = GrokProvider()

    def test_build_prompt_args(self):
        assert self.provider.build_prompt_args("do it") == ["-p", "do it"]

    def test_build_model_args(self):
        assert self.provider.build_model_args("grok-4.5") == ["-m", "grok-4.5"]
        assert self.provider.build_model_args("") == []

    def test_build_model_args_fallback_notes_once_at_info(self):
        with patch("app.provider.grok.log_safe") as log:
            flags = self.provider.build_model_args("grok-4.5", fallback="grok-3")
            self.provider.build_model_args("grok-4.5", fallback="grok-3")
        assert flags == ["-m", "grok-4.5"]
        fallback_notes = [c for c in log.call_args_list if "fallback" in c.args[1]]
        assert len(fallback_notes) == 1
        assert fallback_notes[0].args[0] == "info"

    def test_build_output_args_mapping(self):
        assert self.provider.build_output_args("stream-json") == [
            "--output-format", "streaming-json",
        ]
        assert self.provider.build_output_args("json") == [
            "--output-format", "json",
        ]
        assert self.provider.build_output_args("plain") == [
            "--output-format", "plain",
        ]
        assert self.provider.build_output_args("") == []

    def test_build_permission_args(self):
        assert self.provider.build_permission_args(True) == ["--always-approve"]
        assert self.provider.build_permission_args(False) == [
            "--permission-mode", "acceptEdits",
        ]

    def test_build_tool_args(self):
        assert self.provider.build_tool_args(
            allowed_tools=["Read", "Grep"],
            disallowed_tools=["Bash"],
        ) == ["--tools", "Read,Grep", "--disallowed-tools", "Bash"]

    def test_build_max_turns_args(self):
        assert self.provider.build_max_turns_args(12) == ["--max-turns", "12"]
        assert self.provider.build_max_turns_args(0) == []

    def test_build_effort_args(self):
        assert self.provider.build_effort_args("high") == [
            "--reasoning-effort", "high",
        ]
        assert self.provider.build_effort_args("") == []

    def test_build_effort_args_unknown_warns(self):
        with patch("app.provider.grok.log_safe") as log:
            assert self.provider.build_effort_args("turbo") == []
        assert any("unknown reasoning effort" in c.args[1] for c in log.call_args_list)

    def test_build_command_minimal_stream(self):
        cmd = self.provider.build_command("hello world", output_format="stream-json")
        assert cmd[0] == "grok"
        assert "--permission-mode" in cmd
        assert "acceptEdits" in cmd
        assert cmd[-4:] == ["--output-format", "streaming-json", "-p", "hello world"]

    def test_build_command_with_model_and_tools(self):
        cmd = self.provider.build_command(
            "hello",
            model="grok-4.5",
            output_format="stream-json",
            allowed_tools=["Read", "Glob"],
            skip_permissions=True,
            max_turns=8,
        )
        assert cmd == [
            "grok",
            "--always-approve",
            "--tools", "Read,Glob",
            "-m", "grok-4.5",
            "--output-format", "streaming-json",
            "--max-turns", "8",
            "-p", "hello",
        ]

    def test_build_command_prompt_args_last(self):
        cmd = self.provider.build_command(
            "hello", model="grok-4.5", output_format="stream-json",
        )
        assert cmd[-2:] == ["-p", "hello"]

    def test_build_command_system_prompt_via_rules(self):
        cmd = self.provider.build_command(
            "user ask", system_prompt="be brief", output_format="stream-json",
        )
        assert "--rules" in cmd
        assert "be brief" in cmd
        assert cmd[-1] == "user ask"

    def test_build_command_system_prompt_file_inlined(self, tmp_path):
        path = tmp_path / "sys.md"
        path.write_text("from file", encoding="utf-8")
        cmd = self.provider.build_command(
            "user ask",
            system_prompt="inline",
            system_prompt_file=str(path),
        )
        assert "--rules" in cmd
        idx = cmd.index("--rules")
        assert cmd[idx + 1] == "from file"
        assert "inline" not in cmd

    def test_build_resume_args(self):
        assert self.provider.build_resume_args("sess-1") == ["--resume", "sess-1"]
        cmd = self.provider.build_command("hi", resume_session_id="sess-1")
        assert cmd[1:3] == ["--resume", "sess-1"]

    def test_mcp_and_plugins_warn_once(self):
        with patch("app.provider.grok.log_safe") as log:
            self.provider.build_mcp_args(["cfg.json"])
            self.provider.build_mcp_args(["cfg.json"])
            self.provider.build_plugin_args(["/plugins"])
            self.provider.build_plugin_args(["/plugins"])
        mcp_notes = [c for c in log.call_args_list if "MCP" in c.args[1]]
        plugin_notes = [c for c in log.call_args_list if "plugin" in c.args[1]]
        assert len(mcp_notes) == 1
        assert len(plugin_notes) == 1


# ---------------------------------------------------------------------------
# Registry / env resolution
# ---------------------------------------------------------------------------

class TestGrokProviderResolution:
    def setup_method(self):
        from app.provider import reset_provider
        reset_provider()

    def teardown_method(self):
        from app.provider import reset_provider
        reset_provider()

    @patch.dict(os.environ, {"KOAN_CLI_PROVIDER": "grok"}, clear=False)
    def test_env_var_grok(self):
        from app.provider import get_provider, get_provider_name
        assert get_provider_name() == "grok"
        assert isinstance(get_provider(), GrokProvider)


# ---------------------------------------------------------------------------
# Stream samples — text, summary, usage
# ---------------------------------------------------------------------------

class TestGrokStreamSamples:
    def test_stream_success_text(self):
        events = _parse_ndjson(grok_samples.STREAM_SUCCESS)
        assert _join_stream_text(events) == grok_samples.STREAM_SUCCESS_RESULT_TEXT

    def test_stream_multi_delta_concatenates_without_newlines(self):
        events = _parse_ndjson(grok_samples.STREAM_MULTI_DELTA)
        assert _join_stream_text(events) == grok_samples.STREAM_MULTI_DELTA_RESULT_TEXT

    def test_stream_truncated_partial(self):
        events = _parse_ndjson(grok_samples.STREAM_TRUNCATED)
        assert _join_stream_text(events) == grok_samples.STREAM_TRUNCATED_PARTIAL_TEXT

    def test_end_event_has_no_result_text(self):
        end = {
            "type": "end",
            "stopReason": "EndTurn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        assert _extract_result_text(end) is None

    def test_summarize_thought_text_end(self):
        assert "thinking" in _summarize_stream_event({"type": "thought", "data": "x"})
        assert "text:" in _summarize_stream_event({"type": "text", "data": "hello"})
        summary = _summarize_stream_event({
            "type": "end",
            "stopReason": "EndTurn",
            "num_turns": 2,
        })
        assert "result:" in summary
        assert "EndTurn" in summary

    def test_usage_snapshot_from_stream_success(self):
        events = _parse_ndjson(grok_samples.STREAM_SUCCESS)
        usage = None
        for event in events:
            snap = _usage_snapshot_from_event(event)
            if snap is not None:
                usage = snap
        assert usage is not None
        assert usage["input_tokens"] == grok_samples.STREAM_SUCCESS_USAGE["input_tokens"]
        assert usage["output_tokens"] == grok_samples.STREAM_SUCCESS_USAGE["output_tokens"]
        assert (
            usage["cache_read_input_tokens"]
            == grok_samples.STREAM_SUCCESS_USAGE["cache_read_input_tokens"]
        )
        assert usage["model"] == "grok-4.5"

    def test_usage_when_cache_exceeds_input_keeps_input(self):
        """Multi-turn Grok usage can report cache_read > input_tokens."""
        events = _parse_ndjson(grok_samples.STREAM_MULTI_DELTA)
        usage = None
        for event in events:
            snap = _usage_snapshot_from_event(event)
            if snap is not None:
                usage = snap
        assert usage is not None
        assert usage["input_tokens"] == 21550
        assert usage["cache_read_input_tokens"] == 33408
        assert usage["model"] == "grok-4.5"

    def test_json_object_text_field(self):
        data = json.loads(grok_samples.JSON_OBJECT_SUCCESS)
        assert data["text"] == grok_samples.JSON_OBJECT_SUCCESS_TEXT
        # Non-stream path: usage extractable from the object shape.
        snap = _usage_snapshot_from_event(data)
        assert snap is not None
        assert snap["output_tokens"] == 19
        assert snap["model"] == "grok-4.5"


# ---------------------------------------------------------------------------
# Quota / auth detection
# ---------------------------------------------------------------------------

class TestGrokFailureDetection:
    def setup_method(self):
        self.provider = GrokProvider()

    def test_quota_on_stderr(self):
        assert self.provider.detect_quota_exhaustion(
            stderr_text="Error: rate limit exceeded",
            exit_code=1,
        )

    def test_quota_ignores_success_stdout_prose(self):
        assert not self.provider.detect_quota_exhaustion(
            stdout_text="We should avoid rate limits in the design.",
            exit_code=0,
        )

    def test_auth_on_stderr(self):
        assert self.provider.detect_auth_failure(
            stderr_text="not authenticated — run `grok login` or set XAI_API_KEY",
            exit_code=1,
        )

    def test_auth_ignores_success(self):
        assert not self.provider.detect_auth_failure(
            stderr_text="invalid api key",
            exit_code=0,
        )


class TestGrokQuotaProbe:
    def setup_method(self):
        self.provider = GrokProvider()

    def test_probe_available_on_success(self):
        fake = subprocess.CompletedProcess(
            args=["grok"], returncode=0, stdout='{"text":"ok"}', stderr="",
        )
        with patch("app.cli_exec.run_cli", return_value=fake):
            ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    def test_probe_unavailable_on_auth_failure(self):
        fake = subprocess.CompletedProcess(
            args=["grok"],
            returncode=1,
            stdout="",
            stderr="invalid api key",
        )
        with patch("app.cli_exec.run_cli", return_value=fake):
            ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is False
        assert "invalid api key" in detail

    def test_probe_timeout_reports_available(self):
        with patch(
            "app.cli_exec.run_cli",
            side_effect=subprocess.TimeoutExpired(cmd="grok", timeout=1),
        ):
            ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""


# ---------------------------------------------------------------------------
# Onboarding wiring
# ---------------------------------------------------------------------------

class TestGrokOnboarding:
    def test_provider_tools_and_list(self):
        from app.onboarding import PROVIDER_TOOLS, PROVIDERS
        assert PROVIDER_TOOLS.get("grok") == "grok"
        assert any(p[0] == "grok" for p in PROVIDERS)
