"""Behavior tests for the Haze CLI provider."""

from unittest.mock import MagicMock, patch

import app.onboarding as onboarding
import app.run as run
from app.provider import get_provider_by_name, get_provider_name, reset_provider
from app.provider.haze import HazeProvider


# ---------------------------------------------------------------------------
# Phase 1: stagnation monitor exemption
# ---------------------------------------------------------------------------

def test_stagnation_monitor_skipped_for_non_incremental_provider():
    proc = MagicMock()
    silent = MagicMock()
    silent.emits_incremental_progress.return_value = False
    with patch("app.config.get_stagnation_config",
               return_value={"enabled": True, "check_interval_seconds": 60,
                             "abort_after_cycles": 3, "sample_lines": 50}), \
         patch("app.provider.get_provider", return_value=silent):
        assert run._start_stagnation_monitor("/tmp/out.txt", proc, "proj") is None


def test_stagnation_monitor_started_for_incremental_provider():
    proc = MagicMock()
    streaming = MagicMock()
    streaming.emits_incremental_progress.return_value = True
    started = MagicMock()
    with patch("app.config.get_stagnation_config",
               return_value={"enabled": True, "check_interval_seconds": 60,
                             "abort_after_cycles": 3, "sample_lines": 50}), \
         patch("app.provider.get_provider", return_value=streaming), \
         patch("app.stagnation_monitor.StagnationMonitor", return_value=started):
        assert run._start_stagnation_monitor("/tmp/out.txt", proc, "proj") is started


# ---------------------------------------------------------------------------
# Phase 2: command construction
# ---------------------------------------------------------------------------

def test_haze_binary_and_progress_capability():
    p = HazeProvider()
    assert p.name == "haze"
    assert p.binary() == "haze"
    assert p.emits_incremental_progress() is False


def test_haze_build_command_json_mission():
    cmd = HazeProvider().build_command(
        prompt="do the thing", model="anthropic:claude-sonnet-4-6",
        output_format="json",
    )
    assert cmd[0] == "haze"
    assert "-p" in cmd and cmd[cmd.index("-p") + 1] == "do the thing"
    assert cmd[cmd.index("-m") + 1] == "anthropic:claude-sonnet-4-6"
    assert cmd[cmd.index("--output") + 1] == "json"


def test_haze_text_default_and_system_prompt_prepended():
    cmd = HazeProvider().build_command(prompt="hi", system_prompt="You are X")
    # No model, default text mode → no --output flag
    assert "--output" not in cmd
    assert cmd[cmd.index("-p") + 1] == "You are X\n\nhi"


def test_haze_ignores_unsupported_features():
    cmd = HazeProvider().build_command(
        prompt="hi", max_turns=10, mcp_configs=["/x.json"],
        allowed_tools=["Bash"], plugin_dirs=["/p"], fallback="sonnet",
    )
    for flag in ("--max-turns", "--mcp-config", "--allow-tool",
                 "--fallback-model"):
        assert flag not in cmd


# ---------------------------------------------------------------------------
# Phase 3: quota/auth detection
# ---------------------------------------------------------------------------

def test_haze_detects_quota_in_stderr():
    p = HazeProvider()
    assert p.detect_quota_exhaustion(stderr_text="HTTP 429 too many requests", exit_code=1)


def test_haze_quota_envelope_stdout_only_on_failure():
    p = HazeProvider()
    envelope = '{"type":"error","status":"error","result":"rate limit exceeded"}'
    assert p.detect_quota_exhaustion(stdout_text=envelope, exit_code=1)
    # Benign success output mentioning the phrase must NOT trip a pause.
    ok = '{"type":"message","status":"ok","result":"I will not hit a rate limit here"}'
    assert not p.detect_quota_exhaustion(stdout_text=ok, exit_code=0)


def test_haze_detects_auth_failure():
    p = HazeProvider()
    assert p.detect_auth_failure(stderr_text="401 Unauthorized", exit_code=1)
    assert not p.detect_auth_failure(stderr_text="all good", exit_code=0)


def test_haze_quota_probe_degrades_to_available_on_error():
    p = HazeProvider()
    with patch("app.provider.haze.subprocess.run", side_effect=OSError("boom")):
        ok, _ = p.check_quota_available("/tmp")
    assert ok is True


# ---------------------------------------------------------------------------
# Phase 4: registry resolution
# ---------------------------------------------------------------------------

def test_haze_resolves_from_registry():
    reset_provider()
    assert isinstance(get_provider_by_name("haze"), HazeProvider)


def test_haze_resolves_from_env(monkeypatch):
    reset_provider()
    monkeypatch.setenv("KOAN_CLI_PROVIDER", "haze")
    assert get_provider_name() == "haze"
    reset_provider()


# ---------------------------------------------------------------------------
# Phase 5: onboarding integration
# ---------------------------------------------------------------------------

def test_onboarding_haze_ready_when_binary_present():
    with patch.object(onboarding, "_check_tool",
                      lambda t: "/usr/local/bin/haze" if t == "haze" else None):
        ready, msg = onboarding._provider_ready("haze")
    assert ready is True
    assert "haze" in msg


def test_onboarding_haze_in_provider_list():
    assert any(key == "haze" for key, _ in onboarding.PROVIDERS)


# ---------------------------------------------------------------------------
# Phase 6: metered-provider token extraction
# ---------------------------------------------------------------------------

def test_haze_openai_style_usage_extracted(tmp_path):
    """Haze envelope with prompt_tokens/completion_tokens feeds budget gating."""
    from app.token_parser import extract_tokens

    envelope = tmp_path / "out.json"
    envelope.write_text(
        '{"type":"result","status":"ok","result":"done",'
        '"usage":{"prompt_tokens":1200,"completion_tokens":350}}'
    )
    result = extract_tokens(envelope)
    assert result is not None
    assert result.input_tokens == 1200
    assert result.output_tokens == 350
