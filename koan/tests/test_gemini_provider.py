"""Tests for the Google Gemini CLI provider.

All stream/usage behavior is exercised against the schema samples in
``tests/gemini_samples.py`` — never a live ``gemini`` subprocess.
"""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

import app.provider.gemini as gemini_module
from app.provider import (
    _extract_assistant_text_chunks,
    _extract_result_text,
    _is_text_delta_event,
    _summarize_stream_event,
    _usage_snapshot_from_event,
)
from app.provider.gemini import GeminiProvider
from tests import gemini_samples


@pytest.fixture(autouse=True)
def _fresh_warn_state(monkeypatch):
    """Isolate the once-per-process unsupported-feature warning set."""
    monkeypatch.setattr(gemini_module, "_WARNED_UNSUPPORTED", set())


def _parse_ndjson(blob: str):
    return [
        json.loads(line.strip())
        for line in blob.splitlines()
        if line.strip()
    ]


def _join_stream_text(events) -> str:
    """Mirror run_command_streaming delta/block accumulation for fixtures."""
    text_lines: list = []
    deltas: list = []
    for event in events:
        chunks = _extract_assistant_text_chunks(event)
        if _is_text_delta_event(event):
            deltas.extend(chunks)
        else:
            if deltas:
                text_lines.append("".join(deltas))
                deltas.clear()
            text_lines.extend(chunks)
        result = _extract_result_text(event)
        if result is not None:
            return result
    if deltas:
        text_lines.append("".join(deltas))
    return "\n".join(text_lines)


# ---------------------------------------------------------------------------
# Package structure
# ---------------------------------------------------------------------------

class TestGeminiPackageStructure:
    def test_import_from_provider_package(self):
        from app.provider import GeminiProvider as PackageGemini
        assert PackageGemini is GeminiProvider

    def test_gemini_in_provider_registry(self):
        from app.provider import _PROVIDERS
        assert _PROVIDERS["gemini"] is GeminiProvider

    def test_registry_creates_gemini_instance(self):
        from app.provider import _PROVIDERS
        provider = _PROVIDERS["gemini"]()
        assert provider.name == "gemini"

    def test_known_and_selectable_providers_include_gemini(self):
        from app.provider import known_providers, selectable_providers
        assert "gemini" in known_providers()
        assert "gemini" in selectable_providers()


# ---------------------------------------------------------------------------
# Basics & capability profile
# ---------------------------------------------------------------------------

class TestGeminiProviderBasics:
    def setup_method(self):
        self.provider = GeminiProvider()

    def test_binary_default(self):
        assert self.provider.binary() == "gemini"

    def test_binary_override_absolute(self):
        provider = GeminiProvider(binary_path="/opt/tools/gemini-nightly")
        assert provider.binary() == "/opt/tools/gemini-nightly"

    def test_binary_override_bare_name_stays_path_lookup(self):
        provider = GeminiProvider(binary_path="gemini-nightly")
        assert provider.binary() == "gemini-nightly"

    def test_is_available_uses_which(self):
        with patch("app.provider.gemini.shutil.which", return_value="/usr/bin/gemini"):
            assert self.provider.is_available() is True
        with patch("app.provider.gemini.shutil.which", return_value=None):
            assert self.provider.is_available() is False

    def test_invocation_lock_name(self):
        assert self.provider.invocation_lock_name() == "gemini-cli"

    def test_capability_profile(self):
        assert self.provider.supports_stream_json() is True
        assert self.provider.supports_stdin_prompt_passing() is False
        assert self.provider.supports_prompt_file_passing() is False
        assert self.provider.supports_session_resume() is True
        assert self.provider.supports_system_prompt_file() is False
        assert self.provider.has_api_quota() is True

    def test_declares_no_read_only_enforcement(self):
        """Read-only roles must refuse gemini rather than run unrestricted."""
        assert self.provider.supports_tool_restriction() is False
        assert self.provider.supports_tool_denial() is False
        assert self.provider.supports_read_only_sandbox() is False
        assert self.provider.enforces_read_only() is False

    def test_read_only_invocation_is_refused(self):
        from app.provider import ReadOnlyUnenforceable, build_full_command
        with pytest.raises(ReadOnlyUnenforceable, match="gemini"):
            build_full_command(
                prompt="audit this",
                allowed_tools=["Read"],
                provider=self.provider,
                read_only=True,
            )


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestGeminiCommandConstruction:
    def setup_method(self):
        self.provider = GeminiProvider()

    def test_build_prompt_args(self):
        assert self.provider.build_prompt_args("hi") == ["-p", "hi"]

    def test_build_model_args(self):
        assert self.provider.build_model_args("gemini-2.5-pro") == [
            "-m", "gemini-2.5-pro",
        ]
        assert self.provider.build_model_args("flash") == ["-m", "flash"]
        assert self.provider.build_model_args("") == []

    def test_build_model_args_refuses_claude_aliases(self):
        for alias in ("haiku", "Sonnet", "claude-opus-4-5"):
            assert self.provider.build_model_args(alias) == []

    def test_build_output_args_mapping(self):
        assert self.provider.build_output_args("stream-json") == [
            "--output-format", "stream-json",
        ]
        assert self.provider.build_output_args("json") == ["--output-format", "json"]
        assert self.provider.build_output_args("") == []
        assert self.provider.build_output_args("streaming-json") == []

    def test_permission_args_only_when_skip_permissions(self):
        assert self.provider.build_permission_args(skip_permissions=True) == [
            "--approval-mode", "yolo",
        ]
        assert self.provider.build_permission_args(skip_permissions=False) == []

    def test_build_resume_args(self):
        assert self.provider.build_resume_args("abc123") == ["--resume", "abc123"]
        assert self.provider.build_resume_args("") == []

    def test_unsupported_inputs_emit_no_flags(self):
        assert self.provider.build_tool_args(["Read"], ["Bash"]) == []
        assert self.provider.build_max_turns_args(12) == []
        assert self.provider.build_mcp_args(["/tmp/mcp.json"]) == []
        assert self.provider.build_plugin_args(["/tmp/plugins"]) == []
        assert self.provider.build_effort_args("high") == []

    def test_build_command_minimal_stream(self):
        cmd = self.provider.build_command(
            prompt="do it", output_format="stream-json",
        )
        assert cmd == [
            "gemini", "--output-format", "stream-json", "-p", "do it",
        ]

    def test_build_command_full_drops_unsupported_and_keeps_prompt_last(self):
        cmd = self.provider.build_command(
            prompt="do it",
            allowed_tools=["Read", "Bash"],
            disallowed_tools=["Write"],
            model="gemini-2.5-flash",
            output_format="stream-json",
            max_turns=20,
            mcp_configs=["/tmp/mcp.json"],
            plugin_dirs=["/tmp/plugins"],
            skip_permissions=True,
            effort="high",
            resume_session_id="sess-1",
        )
        assert cmd == [
            "gemini",
            "--resume", "sess-1",
            "--approval-mode", "yolo",
            "-m", "gemini-2.5-flash",
            "--output-format", "stream-json",
            "-p", "do it",
        ]

    def test_build_command_prepends_system_prompt(self):
        cmd = self.provider.build_command(prompt="task", system_prompt="RULES")
        assert cmd[-2] == "-p"
        assert cmd[-1] == "RULES\n\ntask"

    def test_build_command_system_prompt_file_falls_back_to_inline(self, tmp_path):
        path = tmp_path / "sys.md"
        path.write_text("FILE RULES")
        cmd = self.provider.build_command(
            prompt="task",
            system_prompt="INLINE RULES",
            system_prompt_file=str(path),
        )
        # No file flag exists; the inline prompt is what actually reaches the CLI.
        assert str(path) not in cmd
        assert cmd[-1] == "INLINE RULES\n\ntask"


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

class TestGeminiProviderResolution:
    def setup_method(self):
        from app.provider import reset_provider
        reset_provider()

    def teardown_method(self):
        from app.provider import reset_provider
        reset_provider()

    @patch.dict(os.environ, {"KOAN_CLI_PROVIDER": "gemini"}, clear=False)
    def test_env_var_gemini(self):
        from app.provider import get_provider, get_provider_name
        assert get_provider_name() == "gemini"
        assert isinstance(get_provider(), GeminiProvider)


# ---------------------------------------------------------------------------
# Stream samples — text, summary, usage
# ---------------------------------------------------------------------------

class TestGeminiStreamSamples:
    def test_stream_success_text(self):
        events = _parse_ndjson(gemini_samples.STREAM_SUCCESS)
        assert _join_stream_text(events) == gemini_samples.STREAM_SUCCESS_RESULT_TEXT

    def test_stream_multi_delta_concatenates_without_newlines(self):
        events = _parse_ndjson(gemini_samples.STREAM_MULTI_DELTA)
        assert _join_stream_text(events) == gemini_samples.STREAM_MULTI_DELTA_RESULT_TEXT

    def test_stream_truncated_partial(self):
        events = _parse_ndjson(gemini_samples.STREAM_TRUNCATED)
        assert _join_stream_text(events) == gemini_samples.STREAM_TRUNCATED_PARTIAL_TEXT

    def test_user_message_is_not_assistant_text(self):
        """The echoed prompt must never be collected as model output."""
        echo = {"type": "message", "role": "user", "content": "ping"}
        assert _extract_assistant_text_chunks(echo) == []
        assert _is_text_delta_event(echo) is False

    def test_result_event_carries_no_assistant_text(self):
        result = {"type": "result", "status": "success", "stats": {}}
        assert _extract_result_text(result) is None

    def test_summarize_init_message_and_result(self):
        events = _parse_ndjson(gemini_samples.STREAM_SUCCESS)
        summaries = [_summarize_stream_event(e) for e in events]
        assert summaries[0] == "[cli] session init (model=gemini-2.5-pro)"
        assert summaries[1] == "[cli] user turn"
        assert summaries[2] == "[cli] assistant — text: pong"
        assert summaries[3] == "[cli] result: success"

    def test_summarize_tool_use_carries_input_preview(self):
        events = _parse_ndjson(gemini_samples.STREAM_MULTI_DELTA)
        tool_use = next(e for e in events if e["type"] == "tool_use")
        assert (
            _summarize_stream_event(tool_use)
            == "[cli] assistant — tool_use: read_file: src/main.py"
        )

    def test_summarize_tool_result_success_and_error(self):
        events = _parse_ndjson(gemini_samples.STREAM_TOOL_ERROR)
        failed = next(e for e in events if e["type"] == "tool_result")
        summary = _summarize_stream_event(failed)
        assert summary.startswith("[cli] tool_result tool-fixture")
        assert "(error)" in summary
        assert "command not found: make" in summary

        ok_events = _parse_ndjson(gemini_samples.STREAM_MULTI_DELTA)
        ok = next(e for e in ok_events if e["type"] == "tool_result")
        assert "(error)" not in _summarize_stream_event(ok)

    def test_summarize_error_event_reports_message(self):
        events = _parse_ndjson(gemini_samples.STREAM_TOOL_ERROR)
        err = next(e for e in events if e["type"] == "error")
        assert "Tool execution failed" in _summarize_stream_event(err)

    def test_usage_snapshot_subtracts_cached_from_input(self):
        events = _parse_ndjson(gemini_samples.STREAM_SUCCESS)
        usage = None
        for event in events:
            snap = _usage_snapshot_from_event(event)
            if snap is not None:
                usage = snap
        assert usage == gemini_samples.STREAM_SUCCESS_USAGE

    def test_usage_snapshot_without_cache(self):
        events = _parse_ndjson(gemini_samples.STREAM_MULTI_DELTA)
        usage = None
        for event in events:
            snap = _usage_snapshot_from_event(event)
            if snap is not None:
                usage = snap
        assert usage["input_tokens"] == 2400
        assert usage["output_tokens"] == 200
        assert usage["cache_read_input_tokens"] == 0
        assert usage["model"] == "gemini-2.5-flash"

    def test_non_result_events_report_no_usage(self):
        events = _parse_ndjson(gemini_samples.STREAM_SUCCESS)
        for event in events[:-1]:
            assert _usage_snapshot_from_event(event) is None

    def test_json_object_response_field(self):
        data = json.loads(gemini_samples.JSON_OBJECT_SUCCESS)
        assert data["response"] == gemini_samples.JSON_OBJECT_SUCCESS_TEXT


# ---------------------------------------------------------------------------
# Mission-path token parsing (token_parser sees the raw stdout stream)
# ---------------------------------------------------------------------------

class TestGeminiTokenParser:
    def test_stats_shape_extracts_tokens_and_model(self, tmp_path):
        from app.token_parser import extract_tokens
        stream = tmp_path / "stdout.jsonl"
        stream.write_text(gemini_samples.STREAM_SUCCESS)
        result = extract_tokens(stream)
        assert result is not None
        assert result.input_tokens == 980
        assert result.output_tokens == 40
        assert result.cache_read_input_tokens == 400
        assert result.model == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Quota / auth detection
# ---------------------------------------------------------------------------

class TestGeminiFailureDetection:
    def setup_method(self):
        self.provider = GeminiProvider()

    def test_quota_on_stderr(self):
        assert self.provider.detect_quota_exhaustion(
            stderr_text=gemini_samples.QUOTA_STDERR, exit_code=1,
        ) is True

    def test_quota_ignores_success_stdout_prose(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text="I hit the rate limit in the docs example",
            exit_code=0,
        ) is False

    def test_quota_on_failed_run_stdout(self):
        assert self.provider.detect_quota_exhaustion(
            stdout_text='{"error":{"status":"RESOURCE_EXHAUSTED"}}',
            exit_code=1,
        ) is True

    def test_auth_on_stderr(self):
        assert self.provider.detect_auth_failure(
            stderr_text=gemini_samples.AUTH_STDERR, exit_code=1,
        ) is True

    def test_auth_ignores_success(self):
        assert self.provider.detect_auth_failure(
            stderr_text=gemini_samples.AUTH_STDERR, exit_code=0,
        ) is False


class TestGeminiQuotaProbe:
    def setup_method(self):
        self.provider = GeminiProvider()

    def test_probe_available_on_success(self):
        fake = subprocess.CompletedProcess(
            args=["gemini"], returncode=0, stdout='{"response":"ok"}', stderr="",
        )
        with patch("app.cli_exec.run_cli", return_value=fake):
            ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""

    def test_probe_unavailable_on_auth_failure(self):
        fake = subprocess.CompletedProcess(
            args=["gemini"],
            returncode=1,
            stdout="",
            stderr=gemini_samples.AUTH_STDERR,
        )
        with patch("app.cli_exec.run_cli", return_value=fake):
            ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is False
        assert "API key not valid" in detail

    def test_probe_timeout_reports_available(self):
        with patch(
            "app.cli_exec.run_cli",
            side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=1),
        ):
            ok, detail = self.provider.check_quota_available("/tmp/project")
        assert ok is True
        assert detail == ""


# ---------------------------------------------------------------------------
# Onboarding wiring
# ---------------------------------------------------------------------------

class TestGeminiOnboarding:
    def test_provider_tools_and_list(self):
        from app.onboarding import PROVIDER_TOOLS, PROVIDERS
        assert PROVIDER_TOOLS.get("gemini") == "gemini"
        assert any(p[0] == "gemini" for p in PROVIDERS)
