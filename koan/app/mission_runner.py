"""
Kōan -- Mission execution pipeline.

Handles the full lifecycle of a single mission run:
1. Build the Claude CLI command (prompt, tools, flags)
2. Parse Claude JSON output (extract text from various response shapes)
3. Post-mission processing (usage tracking, pending.md archival, reflection,
   auto-merge)

CLI interface:
    python -m app.mission_runner build-command \\
        --instance ... --autonomous-mode ... [--mission-title ...]
    python -m app.mission_runner parse-output <json_file>
    python -m app.mission_runner post-mission \\
        --instance ... --project-name ... --project-path ... \\
        --run-num N --max-runs N --exit-code N \\
        --stdout-file ... --stderr-file ... \\
        [--mission-title ...] [--autonomous-mode ...] [--start-time N]
"""

import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Maximum wall-clock time for the entire post-mission pipeline (seconds).
# Individual steps have their own timeouts (tests: 120s, reflection: 60s,
# verification: 10s), but without an overall ceiling, accumulated steps
# can block the agent loop for too long.  5 minutes is generous — typical
# runs finish in 30-60s.
# Configurable via post_mission_timeout in config.yaml.
POST_MISSION_TIMEOUT = 300  # default; overridden by config at runtime


def _log_runner(category: str, message: str):
    """Log mission runner events via run_log.log() for timestamps and color."""
    try:
        from app.run_log import log as _run_log
        _run_log(category, message)
    except ImportError:
        print(f"[{category}] {message}", file=sys.stderr)


def _resolve_post_mission_timeout() -> int:
    """Read post_mission_timeout from config, falling back to module constant."""
    from app.config import get_post_mission_timeout
    return get_post_mission_timeout()

# Status icons shared by _PipelineTracker.summary_lines() and
# _notify_pipeline_failures() — single source of truth.
_STATUS_ICONS = {"success": "✓", "fail": "✗", "skipped": "–", "timeout": "⏱"}


def _get_koan_root(instance_dir: str) -> str:
    """Resolve KOAN_ROOT from env or instance directory parent."""
    return os.environ.get("KOAN_ROOT", str(Path(instance_dir).parent))


class _PipelineTracker:
    """Accumulates step outcomes for the post-mission pipeline.

    Each step is recorded as success/fail/skipped/timeout with optional
    detail (e.g. error message or elapsed time).
    """

    VALID_STATUSES = ("success", "fail", "skipped", "timeout")

    def __init__(self):
        self.steps: Dict[str, dict] = {}

    def record(self, step: str, status: str, detail: str = "") -> None:
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        self.steps[step] = {"status": status, "detail": detail}

    def run_step(self, step: str, fn, *args, pipeline_expired=None, **kwargs):
        """Run a step function, recording its outcome automatically.

        If pipeline_expired is set, records 'timeout' and skips execution.
        On exception, records 'fail' with the error message and returns None.
        On success, records 'success' and returns the function's result.
        """
        if pipeline_expired is not None and pipeline_expired.is_set():
            self.record(step, "timeout", "pipeline deadline exceeded")
            return None
        try:
            t0 = time.monotonic()
            result = fn(*args, **kwargs)
            elapsed = time.monotonic() - t0
            self.record(step, "success", f"{elapsed:.1f}s")
            return result
        except Exception as e:
            elapsed = time.monotonic() - t0
            self.record(step, "fail", f"failed after {elapsed:.0f}s: {e}")
            _log_runner("error", f"{step} failed after {elapsed:.0f}s: {e}")
            return None

    def summary_lines(self) -> List[str]:
        """Return a compact summary of all recorded steps."""
        lines = []
        for step, info in self.steps.items():
            status = info["status"]
            icon = _STATUS_ICONS.get(status, "?")
            detail = f" ({info['detail']})" if info["detail"] else ""
            lines.append(f"  {icon} {step}: {status}{detail}")
        return lines

    def has_failures(self) -> bool:
        return any(s["status"] == "fail" for s in self.steps.values())

    def has_issues(self) -> bool:
        """Return True if any step failed, timed out, or was skipped."""
        return any(
            s["status"] in ("fail", "timeout", "skipped")
            for s in self.steps.values()
        )

    def to_dict(self) -> Dict[str, dict]:
        return dict(self.steps)


def _write_pipeline_summary(
    instance_dir: str,
    project_name: str,
    tracker: _PipelineTracker,
    mission_title: str = "",
    stdout_file: str = "",
    mission_tier: Optional[str] = None,
    tokens: Optional[dict] = None,
) -> None:
    """Append a pipeline outcome summary to today's journal.

    Args:
        tokens: Pre-extracted token details (from extract_tokens_detailed).
            When provided, skips redundant file read + JSON parse for cache line.
    """
    try:
        from app.journal import append_to_journal

        lines = tracker.summary_lines()
        if not lines:
            return

        # Append cache metrics from this mission's output
        if stdout_file or tokens:
            cache_line = _extract_cache_line(stdout_file, tokens=tokens)
            if cache_line:
                lines.append(f"  📊 {cache_line}")

        now = datetime.now().strftime("%H:%M")
        header = f"\n### Pipeline summary — {now}"
        if mission_title:
            header += f"\nMission: {mission_title}"
        if mission_tier:
            header += f"\nComplexity: {mission_tier}"
        entry = header + "\n" + "\n".join(lines) + "\n"
        append_to_journal(Path(instance_dir), project_name, entry)
    except Exception as e:
        _log_runner("error", f"Pipeline summary write failed: {e}")


def _ensure_tokens(stdout_file: str, tokens: Optional[dict] = None) -> Optional[dict]:
    """Resolve token details, reading from file only if not pre-extracted."""
    if tokens is not None:
        return tokens
    from app.token_parser import extract_tokens
    result = extract_tokens(Path(stdout_file))
    return result.to_dict() if result is not None else None


def _extract_cache_line(stdout_file: str, tokens: Optional[dict] = None) -> str:
    """Extract a compact cache performance line from Claude JSON output.

    Args:
        stdout_file: Path to Claude stdout capture file.
        tokens: Pre-extracted token details (from extract_tokens_detailed).
            When provided, skips redundant file read + JSON parse.
    """
    try:
        from app.cost_tracker import format_mission_cache_line

        tokens = _ensure_tokens(stdout_file, tokens)
        if tokens is None:
            return ""
        return format_mission_cache_line(
            cache_read=tokens.get("cache_read_input_tokens", 0),
            cache_create=tokens.get("cache_creation_input_tokens", 0),
            input_tokens=tokens.get("input_tokens", 0),
        )
    except Exception as e:
        _log_runner("error", f"Cache line extraction failed: {e}")
        return ""


def build_mission_command(
    prompt: str,
    autonomous_mode: str = "implement",
    extra_flags: str = "",
    project_name: str = "",
    plugin_dirs: Optional[List[str]] = None,
    system_prompt: str = "",
    tier: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Build the CLI command for mission execution (provider-agnostic).

    Args:
        prompt: The full agent prompt text (user prompt).
        autonomous_mode: Current mode (review/implement/deep).
        extra_flags: Space-separated extra CLI flags from config.
        project_name: Optional project name for per-project tool overrides.
        plugin_dirs: Optional list of plugin directory paths to load.
        system_prompt: Optional system prompt for cache-friendly positioning.
            When the provider supports it, the prompt is written to a 0600
            temp file and passed via ``--append-system-prompt-file`` so it
            doesn't leak via ``ps``.
        tier: Optional complexity tier ("trivial"/"simple"/"medium"/"complex")
            from the pre-classifier.  When set, overrides model and max_turns
            per the complexity_routing config (unless REVIEW mode is active).

    Returns:
        ``(cmd, cleanup_paths)`` — the command list ready for subprocess and
        a list of temp-file paths the caller MUST unlink after the
        subprocess exits.  ``cleanup_paths`` is empty when no temp files
        were created.
    """
    from app.config import get_mission_tools, get_model_config, get_mcp_configs
    try:
        from app.config import get_effort_for_mode
    except ImportError:
        get_effort_for_mode = lambda _mode="": ""  # noqa: E731
    from app.provider import build_full_command_managed

    # Get mission tools (comma-separated list)
    # REVIEW mode: enforce read-only at tool level (no Bash/Write/Edit)
    if autonomous_mode == "review":
        tools_list = ["Read", "Glob", "Grep"]
    else:
        tools_str = get_mission_tools(project_name)
        tools_list = [t.strip() for t in tools_str.split(",") if t.strip()]

    # Get model configuration with per-project overrides
    models = get_model_config(project_name)
    model = models["mission"]
    if autonomous_mode == "review" and models["review_mode"]:
        # REVIEW mode takes precedence over tier override (safety > cost)
        model = models["review_mode"]
    fallback = models["fallback"]

    # Apply complexity tier overrides (model, max_turns).
    # REVIEW mode guard already resolved above — tier only applies when NOT review.
    max_turns_override = None
    if tier and autonomous_mode != "review":
        try:
            from app.config import get_complexity_routing_config
            routing = get_complexity_routing_config(project_name)
            if routing and routing.get("enabled"):
                tier_cfg = routing.get("tiers", {}).get(tier, {})
                tier_model = tier_cfg.get("model", "")
                if tier_model:
                    model = tier_model
                tier_turns = tier_cfg.get("max_turns")
                if tier_turns:
                    max_turns_override = int(tier_turns)
        except Exception as e:
            print(f"[mission_runner] complexity routing config error (non-blocking): {e}",
                  file=sys.stderr)

    # Get MCP server configs
    mcp_configs = get_mcp_configs(project_name)

    # Extended thinking — activated when config enables it, the mission
    # is classified as "critical" tier, AND the autonomous mode qualifies.
    # Driven by complexity tier rather than a blanket boolean so only the
    # most complex missions benefit from extended reasoning.
    from app.config import should_enable_thinking, get_thinking_config
    thinking_enabled = should_enable_thinking(autonomous_mode, tier=tier or "")
    thinking_budget = 0
    if thinking_enabled:
        thinking_budget = get_thinking_config()["budget_tokens"]

    # When thinking is active it implies max effort — skip regular effort
    # to avoid duplicate/conflicting --effort flags.
    effort = "" if thinking_enabled else get_effort_for_mode(autonomous_mode)

    # Build provider-specific command (file-mode system prompt when supported)
    cmd, cleanup_paths = build_full_command_managed(
        prompt=prompt,
        allowed_tools=tools_list,
        model=model,
        fallback=fallback,
        output_format="json",
        max_turns=max_turns_override or 0,
        mcp_configs=mcp_configs,
        plugin_dirs=plugin_dirs,
        system_prompt=system_prompt,
        effort=effort,
    )

    # Append thinking args directly — kept outside build_full_command so
    # the provider stack doesn't need thinking-specific parameters.
    if thinking_enabled:
        from app.provider import get_provider
        cmd.extend(get_provider().build_thinking_args(
            enabled=True, budget_tokens=thinking_budget,
        ))

    # Append any extra flags from config
    if extra_flags.strip():
        cmd.extend(extra_flags.strip().split())

    return cmd, cleanup_paths


def get_mission_flags(autonomous_mode: str = "", project_name: str = "") -> str:
    """Get CLI flags for mission role from config.

    Args:
        autonomous_mode: Current mode (review/implement/deep).
        project_name: Optional project name for per-project model overrides.

    Returns:
        Space-separated CLI flags string (may be empty).
    """
    from app.config import get_claude_flags_for_role

    return get_claude_flags_for_role("mission", autonomous_mode, project_name)


def check_json_success(stdout_file: str) -> bool:
    """Check if Claude CLI JSON output indicates a successful session.

    The Claude Code CLI can exit with non-zero even when the session
    completed successfully.  This function parses the JSON output and
    returns True when the session result signals success, allowing the
    caller to override a misleading exit code.

    Checks (in order):
    - ``is_error`` is explicitly ``False``
    - ``subtype`` equals ``"success"``
    """
    try:
        raw = Path(stdout_file).read_text()
        if not raw.strip():
            return False
        data = json.loads(raw)
        if not isinstance(data, dict):
            return False
        # Explicit error flag takes priority
        if data.get("is_error") is True:
            return False
        if data.get("is_error") is False:
            return True
        if data.get("subtype") == "success":
            return True
        return False
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def parse_claude_output(raw_text: str) -> str:
    """Extract human-readable text from Claude JSON output.

    Handles multiple JSON response shapes:
    - {"result": "..."}
    - {"content": "..."}
    - {"text": "..."}
    Falls back to raw text if JSON parsing fails.

    Args:
        raw_text: Raw stdout from Claude CLI (JSON or plain text).

    Returns:
        Extracted text content.
    """
    if not raw_text.strip():
        return ""

    try:
        data = json.loads(raw_text)
        # Try common response keys in order
        for key in ("result", "content", "text"):
            if key in data and isinstance(data[key], str):
                return data[key]
        # If none match, return the raw text
        return raw_text.strip()
    except (json.JSONDecodeError, TypeError):
        return raw_text.strip()


def _read_pending_content(instance_dir: str) -> str:
    """Read pending.md content before archival for session classification."""
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    try:
        return pending_path.read_text()
    except (OSError, FileNotFoundError):
        return ""


def _read_stdout_summary(stdout_file: str, max_chars: int = 2000) -> str:
    """Extract text summary from Claude stdout file for session classification.

    When the agent deletes pending.md as part of its Mission Completion
    Checklist, the pending content is empty. The stdout file contains
    Claude's full JSON output which often includes productive signals
    (branch names, PR numbers, test results).

    Returns a truncated text extract, or empty string on error.
    """
    try:
        stdout_path = Path(stdout_file)
        if not stdout_path.exists():
            return ""
        raw = stdout_path.read_text(errors="replace")
        if not raw.strip():
            return ""
        text = parse_claude_output(raw)
        return text[:max_chars] if text else ""
    except (OSError, FileNotFoundError):
        return ""


def _record_session_outcome(
    instance_dir: str,
    project_name: str,
    autonomous_mode: str,
    duration_minutes: int,
    journal_content: str,
    mission_title: str = "",
    mission_type: Optional[str] = None,
) -> None:
    """Record session outcome for staleness tracking (fire-and-forget).

    Args:
        mission_type: Explicit mission type override (e.g. "contemplative").
            When provided, bypasses classify_mission_type().
    """
    try:
        from app.session_tracker import record_outcome
        record_outcome(
            instance_dir=instance_dir,
            project=project_name,
            mode=autonomous_mode or "unknown",
            duration_minutes=duration_minutes,
            journal_content=journal_content,
            mission_title=mission_title,
            mission_type=mission_type,
        )
    except Exception as e:
        _log_runner("error", f"Session outcome recording failed: {e}")

    # Append to JSONL truth log so this session is never lost to compaction
    try:
        from app.memory_manager import append_memory_entry
        summary_parts = []
        if mission_title:
            summary_parts.append(f"Mission: {mission_title}")
        if autonomous_mode:
            summary_parts.append(f"Mode: {autonomous_mode}")
        if duration_minutes:
            summary_parts.append(f"Duration: {duration_minutes}min")
        if journal_content:
            summary_parts.append(journal_content[:500])
        content = " | ".join(summary_parts) if summary_parts else mission_title or "session"
        append_memory_entry(instance_dir, "session", project_name or None, content)
    except Exception as e:
        _log_runner("error", f"JSONL session log failed: {e}")


def _record_skill_metric(
    instance_dir: str,
    project_name: str,
    mission_title: str,
    exit_code: int,
    pending_content: str,
    quality_report: Optional[dict],
) -> None:
    """Record per-project skill metric for fix/implement missions (fire-and-forget)."""
    try:
        from app.session_tracker import classify_mission_type, detect_pr_created
        mission_type = classify_mission_type(mission_title)
        if mission_type != "implement":
            return

        # Only record when a PR was produced (the interesting signal)
        if not detect_pr_created(pending_content):
            return

        # Determine CI status from quality pipeline test results
        ci_status = "none"
        if quality_report and isinstance(quality_report.get("tests"), dict):
            tests = quality_report["tests"]
            if tests.get("skipped"):
                ci_status = "none"
            elif tests.get("passed"):
                ci_status = "pass"
            else:
                ci_status = "fail"

        # Extract PR URL from pending content (best-effort)
        import re
        pr_match = re.search(r'(https://github\.com/[^\s)]+/pull/\d+)', pending_content)
        pr_url = pr_match.group(1) if pr_match else ""

        # Derive skill type from mission title
        skill_type = "fix" if "/fix " in mission_title.lower() else "implement"

        from app.skill_metrics import record_pr_metric
        record_pr_metric(instance_dir, project_name, skill_type, pr_url, ci_status)
    except Exception as e:
        _log_runner("error", f"Skill metric recording failed: {e}")


def _record_cost_event(
    instance_dir: str,
    project_name: str,
    stdout_file: str,
    autonomous_mode: str,
    mission_title: str,
    mission_type: str = "",
    tokens: Optional[dict] = None,
) -> None:
    """Record structured usage event to JSONL cost tracker (fire-and-forget).

    Args:
        tokens: Pre-extracted token details (from extract_tokens_detailed).
            When provided, skips redundant file read + JSON parse.
    """
    try:
        from app.cost_tracker import record_usage

        tokens = _ensure_tokens(stdout_file, tokens)
        if tokens is None:
            return

        record_usage(
            instance_dir=Path(instance_dir),
            project=project_name or "_global",
            model=tokens["model"],
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            mode=autonomous_mode,
            mission=mission_title,
            cache_creation_input_tokens=tokens.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=tokens.get("cache_read_input_tokens", 0),
            cost_usd=tokens.get("cost_usd", 0.0),
            mission_type=mission_type,
        )
    except Exception as e:
        _log_runner("error", f"Cost tracking failed: {e}")


def _log_activity_usage(
    instance_dir: str,
    project_name: str,
    stdout_file: str,
    autonomous_mode: str,
    mission_title: str,
    duration_seconds: int = 0,
    tokens: Optional[dict] = None,
) -> None:
    """Log activity usage to logs/usage.log (fire-and-forget).

    Args:
        tokens: Pre-extracted token details (from extract_tokens_detailed).
            When provided, skips redundant file read + JSON parse.
    """
    try:
        from app.activity_usage_logger import log_activity_usage

        tokens = _ensure_tokens(stdout_file, tokens)
        if tokens is None:
            return

        activity_type = "mission" if mission_title else autonomous_mode or "autonomous"
        description = mission_title or f"autonomous ({autonomous_mode})"

        log_activity_usage(
            project=project_name or "_global",
            activity_type=activity_type,
            description=description,
            duration_seconds=duration_seconds,
            input_tokens=tokens["input_tokens"],
            output_tokens=tokens["output_tokens"],
            cache_read_tokens=tokens.get("cache_read_input_tokens", 0),
            cache_creation_tokens=tokens.get("cache_creation_input_tokens", 0),
            cost_usd=tokens.get("cost_usd", 0.0),
            model=tokens.get("model", ""),
        )
    except Exception as e:
        print(f"[mission_runner] Activity usage logging failed: {e}", file=sys.stderr)


def archive_pending(instance_dir: str, project_name: str, run_num: int) -> bool:
    """Archive pending.md to daily journal if agent didn't clean it up.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        run_num: Current run number.

    Returns:
        True if pending.md was archived, False if it didn't exist.
    """
    pending_path = Path(instance_dir) / "journal" / "pending.md"
    try:
        pending_content = pending_path.read_text()
    except (OSError, FileNotFoundError):
        return False

    # Append pending content to daily journal (with file locking)
    from app.journal import append_to_journal
    now = datetime.now().strftime("%H:%M")
    entry = f"\n## Run {run_num} — {now} (auto-archived from pending)\n\n{pending_content}"

    append_to_journal(Path(instance_dir), project_name, entry)

    pending_path.unlink(missing_ok=True)
    return True


def update_usage(stdout_file: str, usage_state: str, usage_md: str) -> bool:
    """Update token usage state from Claude JSON output.

    Args:
        stdout_file: Path to Claude stdout capture file.
        usage_state: Path to usage_state.json.
        usage_md: Path to usage.md.

    Returns:
        True if update succeeded.
    """
    try:
        from app.usage_estimator import cmd_update

        cost_pct = cmd_update(Path(stdout_file), Path(usage_state), Path(usage_md))
    except Exception as e:
        _log_runner("error", f"Usage update failed: {e}")
        return False

    if cost_pct is not None:
        try:
            from app.burn_rate import record_run
            record_run(Path(usage_md).parent, cost_pct)
        except Exception as e:  # pragma: no cover - defensive
            _log_runner("error", f"Burn rate record failed: {e}")
    return True


def trigger_reflection(
    instance_dir: str,
    mission_title: str,
    duration_minutes: int,
    project_name: str = "",
) -> bool:
    """Trigger post-mission reflection if the mission was significant.

    Reads today's journal file for the project to provide context to the
    reflection prompt. The dual heuristic (keyword + substantial journal)
    prevents noise from trivial missions.

    Args:
        instance_dir: Path to instance directory.
        mission_title: Mission description text.
        duration_minutes: Duration in minutes.
        project_name: Current project name (for journal file lookup).

    Returns:
        True if reflection was generated.
    """
    try:
        from app.post_mission_reflection import (
            _read_journal_file,
            is_significant_mission,
            run_reflection,
            write_to_journal,
        )

        inst = Path(instance_dir)
        journal_content = _read_journal_file(inst, project_name)

        if not is_significant_mission(mission_title, duration_minutes, journal_content):
            return False

        reflection = run_reflection(inst, mission_title, journal_content)
        if reflection:
            write_to_journal(inst, reflection)
            return True
    except Exception as e:
        _log_runner("error", f"Reflection failed: {e}")
    return False


def _get_quality_gate_mode(
    instance_dir: str,
    project_name: str,
    projects_config: Optional[dict] = None,
) -> str:
    """Get the quality gate mode for a project.

    Args:
        projects_config: Pre-loaded projects config dict. When provided,
            skips redundant load_projects_config() call.

    Returns one of: "strict", "warn", "off". Default: "warn".
    """
    try:
        from app.projects_config import get_project_config
        config = projects_config
        if config is None:
            from app.projects_config import load_projects_config
            koan_root = _get_koan_root(instance_dir)
            config = load_projects_config(koan_root)
        if config:
            project_config = get_project_config(config, project_name)
            pr_quality = project_config.get("pr_quality", {})
            gate = pr_quality.get("gate", "warn")
            if gate in ("strict", "warn", "off"):
                return gate
    except Exception as e:
        _log_runner("error", f"Quality gate config error: {e}")
    return "warn"


def _run_quality_pipeline(
    instance_dir: str,
    project_name: str,
    project_path: str,
    report_fn,
    projects_config: Optional[dict] = None,
) -> dict:
    """Run the post-mission quality pipeline.

    Wraps pr_quality.run_quality_pipeline with project config resolution.
    Raises on error — caller (_PipelineTracker.run_step) handles recording.

    Args:
        projects_config: Pre-loaded projects config dict to avoid redundant I/O.
    """
    from app.config import get_branch_prefix
    from app.pr_quality import run_quality_pipeline

    branch_prefix = get_branch_prefix()
    gate_mode = _get_quality_gate_mode(
        instance_dir, project_name, projects_config=projects_config,
    )

    return run_quality_pipeline(
        project_path=project_path,
        branch_prefix=branch_prefix,
        run_tests=True,
        test_timeout=120,
        gate_mode=gate_mode,
        status_callback=report_fn,
    )


def _run_lint_gate(
    instance_dir: str, project_name: str, project_path: str
):
    """Run lint gate, returning LintResult or None.

    Raises on error — caller (_PipelineTracker.run_step) handles recording.
    """
    from app.lint_gate import run_lint_gate
    return run_lint_gate(project_path, project_name, instance_dir)


def _is_lint_blocking(
    instance_dir: str,
    project_name: str,
    projects_config: Optional[dict] = None,
) -> bool:
    """Check if lint gate is configured as blocking for a project.

    Args:
        projects_config: Pre-loaded projects config dict to avoid redundant I/O.
    """
    try:
        from app.lint_gate import get_project_lint_config
        config = projects_config
        if config is None:
            from app.projects_config import load_projects_config
            koan_root = _get_koan_root(instance_dir)
            config = load_projects_config(koan_root)
        if not config:
            return False
        lint_config = get_project_lint_config(config, project_name)
        return lint_config.get("blocking", True) and lint_config.get("enabled", False)
    except Exception as e:
        _log_runner("error", f"Lint config check failed: {e}")
        return False


def _run_mission_verification(
    project_path: str,
    mission_title: str,
    exit_code: int,
    instance_dir: str,
):
    """Run post-mission semantic verification.

    Returns VerifyResult. Raises on error — caller handles recording.
    """
    from app.mission_verifier import verify_mission, format_verify_result
    from app.config import get_branch_prefix

    branch_prefix = get_branch_prefix()
    result = verify_mission(
        project_path=project_path,
        mission_title=mission_title,
        exit_code=exit_code,
        branch_prefix=branch_prefix,
    )
    # Log result to console
    print(f"[mission_runner] {format_verify_result(result)}")
    return result


def check_auto_merge(
    instance_dir: str,
    project_name: str,
    project_path: str,
    quality_report: Optional[dict] = None,
    lint_blocked: bool = False,
    verify_blocked: bool = False,
    projects_config: Optional[dict] = None,
) -> Optional[str]:
    """Check if current branch should be auto-merged.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        project_path: Path to project directory.
        quality_report: Optional quality pipeline results for gating.
        lint_blocked: Whether lint gate is blocking auto-merge.
        verify_blocked: Whether verification failure is blocking auto-merge.
        projects_config: Pre-loaded projects config dict to avoid redundant I/O.

    Returns:
        Branch name if auto-merge was attempted, None otherwise.
    """
    try:
        from app.git_sync import run_git
        branch = run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
        if not branch:
            return None
        from app.config import get_branch_prefix
        if not branch.startswith(get_branch_prefix()):
            return None

        # Lint gate block
        if lint_blocked:
            print("[mission_runner] Auto-merge blocked by lint gate")
            return None

        # Verification block
        if verify_blocked:
            print("[mission_runner] Auto-merge blocked by verification failure")
            return None

        # Check if auto-merge is configured for this project
        from app.git_auto_merge import auto_merge_branch
        from app.projects_config import get_project_auto_merge

        config = projects_config
        if config is None:
            from app.projects_config import load_projects_config
            koan_root = _get_koan_root(instance_dir)
            config = load_projects_config(koan_root)
        auto_merge_cfg = get_project_auto_merge(config, project_name) if config else {}
        auto_merge_enabled = auto_merge_cfg.get("enabled", False)

        # Quality gate check — only post comments when auto-merge is configured.
        # Without auto-merge, quality info is already in the PR description.
        if quality_report and auto_merge_enabled:
            from app.pr_quality import should_block_auto_merge, post_quality_comment
            gate_mode = _get_quality_gate_mode(
                instance_dir, project_name, projects_config=config,
            )
            if should_block_auto_merge(quality_report, gate_mode):
                _log_runner("mission", f"Auto-merge blocked by quality gate ({gate_mode})")
                try:
                    post_quality_comment(project_path, quality_report)
                except Exception as e:
                    _log_runner("error", f"Quality comment failed: {e}")
                return None

        auto_merge_branch(instance_dir, project_name, project_path, branch)
        return branch
    except Exception as e:
        _log_runner("error", f"Auto-merge check failed: {e}")
        return None


def _notify_pipeline_failures(
    tracker: _PipelineTracker,
    mission_title: str = "",
    instance_dir: str = "",
) -> None:
    """Write a warning to outbox.md if the post-mission pipeline had issues.

    Reports failed, timed-out, and skipped steps so users can see when
    steps like reflection or auto_merge silently fail to complete.

    Writing to outbox.md instead of calling Telegram directly ensures the
    bridge retries delivery on transient network errors.
    """
    if not tracker.has_issues():
        return
    try:
        from app.utils import append_to_outbox

        _ISSUE_ICONS = {"fail": "✗", "timeout": "⏱", "skipped": "–"}
        issues = []
        for name, info in tracker.steps.items():
            icon = _ISSUE_ICONS.get(info["status"])
            if icon is None:
                continue
            label = f"{icon} {name}"
            if info["detail"]:
                label += f" ({info['detail']})"
            issues.append(label)
        if not issues:
            return

        prefix = f"[{mission_title}] " if mission_title else ""
        msg = f"⚠️ {prefix}Pipeline issues: {', '.join(issues)}"
        from app.notify import NotificationPriority
        outbox_path = Path(instance_dir) / "outbox.md"
        append_to_outbox(outbox_path, msg + "\n", priority=NotificationPriority.WARNING)
    except Exception as e:
        _log_runner("error", f"Pipeline failure notification failed: {e}")


# Alert markers are matched case-insensitively. Word boundaries (\b) keep
# short fragments like "no PR" from triggering on prose ("no problem",
# "no projects"). Markdown-bolded markers (**SKIP**, **FAIL**, …) match
# without word boundaries because the ** delimiters already anchor them.
_RESULT_ALERT_REGEX = re.compile(
    r"""
    \*\*\s*(?:skip|fail(?:ed)?|error|blocked)\s*\*\*    # **SKIP**, **FAIL**, **FAILED**, **ERROR**, **BLOCKED**
    | \b(?:skip|fail|error|blocked)\s*[—–\-]{1,2}       # SKIP —, FAIL --, ERROR -, etc.
    | \bmission\s+(?:blocked|aborted)\b
    | \bpermission\s+deadlock\b
    | \bhard\s+stop\b
    | \bno\s+branch,?\s+no\s+commits\b
    | \bno\s+PR\b                                       # word-bounded — no "no problem"/"no projects"
    | \bno\s+code\s+changes\b
    | \bcould(?:\s+not|n[’']?t)\s+execute\b             # could not / couldn't / couldn’t execute
    | \bnever\s+produced\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_RESULT_FORWARD_MAX_CHARS = 4000

# Lazy registry cache — skills rarely change at runtime, so we build the
# registry once per process. Rebuild requires a restart, matching how skill
# registration works elsewhere.
_skill_registry_cache: Optional[Any] = None


def _resolve_forward_result_markers() -> list:
    """Collect mission-title markers from skills with ``forward_result: true``.

    Builds the skill registry lazily from the koan core skills directory plus
    the operator's ``$KOAN_ROOT/instance/skills/`` tree. Each opted-in skill
    contributes auto-derived slash-command forms (``/{cmd.name}``,
    ``/{alias}``, ``/{scope}.{name}``) and any explicit ``title_markers``.
    """
    global _skill_registry_cache
    try:
        if _skill_registry_cache is None:
            from app.skills import build_registry
            extra_dirs = []
            koan_root = os.environ.get("KOAN_ROOT")
            if koan_root:
                instance_skills = Path(koan_root) / "instance" / "skills"
                if instance_skills.is_dir():
                    extra_dirs.append(instance_skills)
            _skill_registry_cache = build_registry(extra_dirs)
        from app.skills import collect_forward_result_markers
        return collect_forward_result_markers(_skill_registry_cache)
    except Exception as e:
        _log_runner("error", f"Forward-result marker resolution failed: {e}")
        return []


def _should_forward_result(mission_title: str, result_text: str) -> Tuple[bool, bool]:
    """Decide whether to forward this mission's result to outbox.

    Returns ``(should_forward, is_alert)``. ``is_alert`` only governs the
    icon (⚠️ for alerts, ℹ️ for customer-facing successes); the caller picks
    its own notification priority.
    """
    body = (result_text or "").strip()
    if not body:
        return (False, False)

    is_alert = bool(_RESULT_ALERT_REGEX.search(body))

    lowered_title = (mission_title or "").lower()
    markers = _resolve_forward_result_markers()
    is_customer_facing = any(
        marker in lowered_title for marker in markers if marker
    )

    return (is_alert or is_customer_facing, is_alert)


def _notify_mission_result(
    mission_title: str,
    instance_dir: str,
    stdout_file: str,
    start_time: int,
    exit_code: int,
    outbox_baseline_mtime: Optional[float] = None,
) -> None:
    """Forward the Claude session's result text to outbox.md.

    Activates when the result text is either an alert outcome
    (SKIP/FAIL/ERROR/BLOCKED) or a skill that opted into result forwarding
    via ``forward_result: true`` in its SKILL.md, on both successful and
    failed Claude exits — failure exits often carry the most useful error
    context, so they are forwarded too.

    Idempotency: skipped silently when the Claude session itself wrote to
    outbox.md during execution. The caller should pass
    ``outbox_baseline_mtime`` captured **before** any post-mission step ran,
    so writes from later pipeline steps (failure notifier, reflection,
    pr_review_learning, …) do not suppress this notification. When
    ``outbox_baseline_mtime`` is None, the current mtime is read at call
    time (legacy/test path).
    """
    try:
        from app.config import get_notify_mission_results
        if not get_notify_mission_results():
            return
    except Exception as e:
        # Fail open: default-True if config check is broken
        _log_runner("error", f"notify_mission_results config check failed: {e}")

    try:
        result_text = _read_stdout_summary(stdout_file, max_chars=_RESULT_FORWARD_MAX_CHARS)
        should_forward, is_alert = _should_forward_result(mission_title, result_text)
        if not should_forward:
            return

        outbox_path = Path(instance_dir) / "outbox.md"

        try:
            mtime: Optional[float]
            if outbox_baseline_mtime is not None:
                mtime = outbox_baseline_mtime
            elif outbox_path.exists():
                mtime = outbox_path.stat().st_mtime
            else:
                mtime = None
            if start_time > 0 and mtime is not None and mtime > start_time:
                return
        except OSError:
            pass

        title_short = (mission_title or "").strip()
        if len(title_short) > 120:
            title_short = title_short[:117] + "…"

        icon = "⚠️" if is_alert else "ℹ️"
        # Non-zero exits get the alert icon even when the body lacks keyword
        # markers — the failure itself is the signal.
        if exit_code != 0:
            icon = "⚠️"
        prefix_line = f"{icon} {title_short}" if title_short else icon

        body = result_text.strip()
        msg = f"{prefix_line}\n\n{body}\n"

        from app.utils import append_to_outbox
        from app.notify import NotificationPriority
        # Customer-facing mission completions are responses to user commands —
        # always send at ACTION priority so they pass the default min_priority
        # filter. is_alert only affects the visual icon (⚠️ vs ℹ️).
        append_to_outbox(outbox_path, msg, priority=NotificationPriority.ACTION)
    except Exception as e:
        _log_runner("error", f"Mission result notification failed: {e}")


def _fire_post_mission_hook(
    instance_dir: str,
    project_name: str,
    project_path: str,
    exit_code: int,
    mission_title: str,
    duration_minutes: int,
    result: dict,
    stdout_file: Optional[str] = None,
) -> Dict[str, str]:
    """Fire post_mission hooks with full context.

    When ``stdout_file`` is provided, the truncated stdout summary is
    pre-read and passed to hooks as ``result_text`` so individual hooks
    can inspect the mission output without re-implementing file I/O.

    Returns a dict mapping failed handler names to error messages.
    Empty dict means all hooks succeeded.
    """
    result_text = ""
    if stdout_file:
        try:
            result_text = _read_stdout_summary(
                stdout_file, max_chars=_RESULT_FORWARD_MAX_CHARS,
            )
        except Exception as e:
            _log_runner("error", f"post_mission hook stdout read failed: {e}")

    try:
        from app.hooks import fire_hook
        return fire_hook(
            "post_mission",
            instance_dir=instance_dir,
            project_name=project_name,
            project_path=project_path,
            exit_code=exit_code,
            mission_title=mission_title,
            duration_minutes=duration_minutes,
            result=dict(result),
            result_text=result_text,
        )
    except Exception as e:
        _log_runner("error", f"post_mission hook error: {e}")
        return {"_fire_post_mission_hook": str(e)}


def check_security_review(
    instance_dir: str,
    project_name: str,
    project_path: str,
) -> bool:
    """Run differential security review on the current branch.

    Analyzes the diff for security-sensitive patterns and blast radius.
    Configured via security_review section in projects.yaml.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        project_path: Path to project directory.

    Returns:
        True if auto-merge should proceed, False if blocked by review.
    """
    try:
        from app.security_review import check_security_review as _check

        return _check(instance_dir, project_name, project_path)
    except Exception as e:
        print(f"[mission_runner] Security review failed: {e}", file=sys.stderr)
        return True  # Don't block on failures


def run_post_mission(
    instance_dir: str,
    project_name: str,
    project_path: str,
    run_num: int,
    exit_code: int,
    stdout_file: str,
    stderr_file: str,
    mission_title: str = "",
    autonomous_mode: str = "",
    start_time: int = 0,
    status_callback: Optional[Callable[[str], None]] = None,
    mission_tier: Optional[str] = None,
) -> dict:
    """Run the complete post-mission processing pipeline.

    This replaces ~50 lines of bash that call 5 different Python scripts.

    Args:
        instance_dir: Path to instance directory.
        project_name: Current project name.
        project_path: Path to project directory.
        run_num: Current run number.
        exit_code: Claude CLI exit code.
        stdout_file: Path to Claude stdout capture file.
        stderr_file: Path to Claude stderr capture file.
        mission_title: Mission description (empty for autonomous).
        autonomous_mode: Current mode (review/implement/deep).
        start_time: Mission start time as unix timestamp.
        status_callback: Optional callable to report progress during finalization.
            Called with a short description of the current step.

    Returns:
        Dict with keys:
            success (bool): Whether Claude exited successfully.
            usage_updated (bool): Whether usage tracking was updated.
            pending_archived (bool): Whether pending.md was archived.
            reflection_written (bool): Whether a reflection was generated.
            security_review_passed (bool): Whether security review passed.
            auto_merge_branch (str|None): Branch name if auto-merge attempted.
            quota_exhausted (bool): Whether quota exhaustion was detected.
            quota_info (tuple|None): (reset_display, resume_message) if exhausted.
    """
    result = {
        "success": exit_code == 0,
        "usage_updated": False,
        "pending_archived": False,
        "reflection_written": False,
        "security_review_passed": True,
        "auto_merge_branch": None,
        "quota_exhausted": False,
        "quota_info": None,
        "cost_tracking_failed": False,
    }

    tracker = _PipelineTracker()

    # Snapshot outbox.md mtime BEFORE any post-mission step runs, so the
    # mission-result notifier can distinguish "Claude wrote during the
    # session" from "a later pipeline step (failure notifier, reflection,
    # pr_review_learning, …) wrote to outbox." Without this snapshot, any
    # downstream outbox write would erroneously suppress the result body.
    _outbox_baseline_mtime: Optional[float] = None
    try:
        _outbox_path = Path(instance_dir) / "outbox.md"
        if _outbox_path.exists():
            _outbox_baseline_mtime = _outbox_path.stat().st_mtime
    except OSError:
        _outbox_baseline_mtime = None

    # Overall pipeline deadline — prevents accumulated steps from blocking
    # the agent loop indefinitely.
    _pm_timeout = _resolve_post_mission_timeout()
    _pipeline_expired = threading.Event()
    _deadline_timer = threading.Timer(
        _pm_timeout,
        lambda: (
            _pipeline_expired.set(),
            print(
                f"[mission_runner] Post-mission pipeline exceeded {_pm_timeout}s — "
                "skipping remaining steps",
                file=sys.stderr,
            ),
        ),
    )
    _deadline_timer.daemon = True
    _deadline_timer.start()

    try:
        def _report(step: str) -> None:
            if status_callback:
                status_callback(step)

        # Pre-extract token details once — reused by cost tracking, activity
        # logging, and cache line extraction instead of parsing the same JSON
        # file 3 times.
        _tokens = None
        try:
            from app.token_parser import extract_tokens
            _result = extract_tokens(Path(stdout_file))
            _tokens = _result.to_dict() if _result is not None else None
        except Exception as e:
            _log_runner("error", f"Token extraction failed: {e}")

        # Flag silent cost-tracking gaps so operators can detect them
        if _tokens is None and exit_code == 0:
            result["cost_tracking_failed"] = True
            print(
                "[mission_runner] WARNING: cost tracking failed — "
                "token extraction returned None after successful run",
                file=sys.stderr,
            )

        # Pre-load projects config once — reused by quality gate, lint gate,
        # and auto-merge instead of loading projects.yaml 3 times.
        _projects_config = None
        _koan_root = _get_koan_root(instance_dir)
        try:
            from app.projects_config import load_projects_config
            _projects_config = load_projects_config(_koan_root)
        except Exception as e:
            _log_runner("error", f"Projects config load failed: {e}")

        # 1. Update token usage from JSON output
        _report("updating usage stats")
        usage_state = os.path.join(instance_dir, "usage_state.json")
        usage_md = os.path.join(instance_dir, "usage.md")
        result["usage_updated"] = update_usage(stdout_file, usage_state, usage_md)
        tracker.record("usage_update", "success" if result["usage_updated"] else "fail")

        # 1b. Record structured usage to JSONL cost tracker
        from app.session_tracker import classify_mission_type as _classify_type
        _mission_type = _classify_type(mission_title)
        _record_cost_event(
            instance_dir, project_name, stdout_file,
            autonomous_mode, mission_title, mission_type=_mission_type,
            tokens=_tokens,
        )

        # 2. Compute duration (needed for quota early-return, reflection, and outcome tracking)
        if start_time > 0:
            duration_seconds = int(datetime.now().timestamp()) - start_time
            duration_minutes = duration_seconds // 60
        else:
            duration_seconds = 0
            duration_minutes = 0

        # 2b. Log activity usage to logs/usage.log (human-readable, rotated)
        _log_activity_usage(
            instance_dir, project_name, stdout_file,
            autonomous_mode, mission_title, duration_seconds,
            tokens=_tokens,
        )

        # 3. Check for quota exhaustion
        _report("checking quota")
        from app.quota_handler import handle_quota_exhaustion, QUOTA_CHECK_UNRELIABLE

        quota_result = handle_quota_exhaustion(
            koan_root=_koan_root,
            instance_dir=instance_dir,
            project_name=project_name,
            run_count=run_num,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
        )
        if quota_result is QUOTA_CHECK_UNRELIABLE:
            _log_runner("quota", f"⚠️  Quota check unreliable for {project_name} — "
                        "could not read log files, skipping quota detection")
            tracker.record("quota_check", "skipped", "unreliable — log files unreadable")
        elif quota_result is not None:
            result["quota_exhausted"] = True
            result["quota_info"] = quota_result
            tracker.record("quota_check", "success", "quota exhausted — early return")
            # Record session outcome BEFORE early return so the session tracker
            # doesn't lose visibility on quota-limited sessions (which biases
            # staleness calculations toward "stale" for productive projects).
            pending_content = _read_pending_content(instance_dir)
            if not pending_content.strip():
                pending_content = _read_stdout_summary(stdout_file)
            _record_session_outcome(
                instance_dir, project_name, autonomous_mode,
                duration_minutes, pending_content,
                mission_title=mission_title,
            )
            # Fire post_mission hooks before early return so hooks see quota events
            _fire_post_mission_hook(
                instance_dir, project_name, project_path,
                exit_code, mission_title, duration_minutes, result,
                stdout_file=stdout_file,
            )
            result["pipeline_steps"] = tracker.to_dict()
            _write_pipeline_summary(
                instance_dir, project_name, tracker, mission_title,
                mission_tier=mission_tier, tokens=_tokens,
            )
            return result  # Early return — no further processing on quota exhaustion
        tracker.record("quota_check", "success", "no exhaustion")

        # 4. Archive pending.md if agent didn't clean up
        _report("archiving journal")
        # Read pending content before archival for session outcome tracking.
        # When the agent follows Mission Completion Checklist, it deletes
        # pending.md before exiting — so we fall back to stdout content.
        pending_content = _read_pending_content(instance_dir)
        if not pending_content.strip():
            pending_content = _read_stdout_summary(stdout_file)
        result["pending_archived"] = archive_pending(instance_dir, project_name, run_num)
        tracker.record("journal_archive", "success" if result["pending_archived"] else "skipped",
                        "archived" if result["pending_archived"] else "nothing to archive")

        # 5. Post-mission processing (only on success)
        quality_report = None
        if exit_code == 0:
            verify_result = None
            quality_report = {}
            lint_result = None

            # Mission verification (RARV Verify phase — semantic checks)
            _report("verifying mission output")
            verify_result = tracker.run_step(
                "verification",
                _run_mission_verification,
                project_path, mission_title, exit_code, instance_dir,
                pipeline_expired=_pipeline_expired,
            )
            if verify_result is not None:
                if not verify_result.passed:
                    tracker.record("verification", "fail",
                                   verify_result.summary or "verification failed")
                result["verification"] = {
                    "passed": verify_result.passed,
                    "summary": verify_result.summary,
                    "warnings": len(verify_result.warnings),
                    "failures": len(verify_result.failures),
                }

            # Quality pipeline (scan, tests, branch hygiene, PR enrichment)
            _report("running quality pipeline")
            quality_report = tracker.run_step(
                "quality_pipeline",
                _run_quality_pipeline,
                instance_dir, project_name, project_path, _report,
                projects_config=_projects_config,
                pipeline_expired=_pipeline_expired,
            )
            if quality_report is None:
                quality_report = {}
            result["quality"] = quality_report

            # Lint gate
            _report("running lint gate")
            lint_result = tracker.run_step(
                "lint_gate",
                _run_lint_gate,
                instance_dir, project_name, project_path,
                pipeline_expired=_pipeline_expired,
            )
            if lint_result is not None:
                result["lint_passed"] = lint_result.passed

            # Reflection
            _report("running reflection")
            reflection_result = tracker.run_step(
                "reflection",
                trigger_reflection,
                instance_dir,
                mission_title if mission_title else f"Autonomous {autonomous_mode} on {project_name}",
                duration_minutes,
                project_name=project_name,
                pipeline_expired=_pipeline_expired,
            )
            result["reflection_written"] = bool(reflection_result)

            # Differential security review (before auto-merge)
            _report("security review")
            security_passed = tracker.run_step(
                "security_review",
                check_security_review,
                instance_dir, project_name, project_path,
                pipeline_expired=_pipeline_expired,
            )
            if security_passed is None:
                security_passed = True
            result["security_review_passed"] = security_passed

            # Auto-merge check (respects quality gate + lint gate + verification + security review)
            _report("checking auto-merge")
            lint_blocking = lint_result is not None and not lint_result.passed and _is_lint_blocking(instance_dir, project_name, projects_config=_projects_config)
            verify_blocking = verify_result is not None and not verify_result.passed
            security_blocking = not result.get("security_review_passed", True)
            if not security_blocking:
                merge_result = tracker.run_step(
                    "auto_merge",
                    check_auto_merge,
                    instance_dir, project_name, project_path,
                    quality_report=quality_report,
                    lint_blocked=lint_blocking,
                    verify_blocked=verify_blocking,
                    projects_config=_projects_config,
                    pipeline_expired=_pipeline_expired,
                )
                result["auto_merge_branch"] = merge_result
            else:
                tracker.record("auto_merge", "skipped", "blocked by security review")
        else:
            # Non-zero exit — skip success-only steps
            for step in ("verification", "quality_pipeline", "lint_gate", "reflection", "security_review", "auto_merge"):
                tracker.record(step, "skipped", "non-zero exit code")

        # 7. Record session outcome for staleness tracking
        # Always runs — even after deadline — since it's a fast local write.
        _report("recording session outcome")
        _record_session_outcome(
            instance_dir, project_name, autonomous_mode,
            duration_minutes, pending_content,
            mission_title=mission_title,
        )
        tracker.record("session_outcome", "success")

        # 7a-bis. Record skill-level metrics for fix/implement missions.
        _record_skill_metric(
            instance_dir, project_name, mission_title,
            exit_code, pending_content, quality_report,
        )

        # 7a. Update Thompson Sampling bandit with mission outcome.
        # Non-zero exit is always a failure; for zero-exit, classify via
        # session content so "empty" sessions also count as failures.
        try:
            from app.bandit import load_bandit_state, update_bandit, save_bandit_state
            if exit_code != 0:
                bandit_success = False
            else:
                from app.session_tracker import classify_session
                outcome_type = classify_session(pending_content, mission_title=mission_title)
                bandit_success = outcome_type == "productive"
            _bandit_state = load_bandit_state(instance_dir)
            update_bandit(_bandit_state, project_name, success=bandit_success)
            save_bandit_state(_bandit_state, instance_dir)
        except Exception as e:
            _log_runner("error", f"Bandit update failed: {e}")

        # 7b. Update daily metrics snapshot (fast local write)
        try:
            from app.daily_snapshot import update_daily_snapshot
            update_daily_snapshot(instance_dir)
        except Exception as e:
            _report(f"daily snapshot failed: {e}")

        # 8. Fire post-mission hooks
        if not _pipeline_expired.is_set():
            _report("running hooks")
            hook_failures = _fire_post_mission_hook(
                instance_dir, project_name, project_path,
                exit_code, mission_title, duration_minutes, result,
                stdout_file=stdout_file,
            )
            if hook_failures:
                failed_names = ", ".join(sorted(hook_failures))
                tracker.record("hooks", "fail", f"failed: {failed_names}")
            else:
                tracker.record("hooks", "success")
        else:
            tracker.record("hooks", "timeout", "pipeline deadline exceeded")

        # Write pipeline summary to journal and include in result
        result["pipeline_steps"] = tracker.to_dict()
        _write_pipeline_summary(
            instance_dir, project_name, tracker, mission_title,
            stdout_file=stdout_file,
            mission_tier=mission_tier,
            tokens=_tokens,
        )

        # Notify user of pipeline failures via outbox (retried by bridge)
        _notify_pipeline_failures(tracker, mission_title, instance_dir)

        # Forward Claude's result text to outbox so SKIP/ERROR/BLOCKED
        # outcomes (and customer-facing skill results) reach Telegram even
        # when the session's sandbox blocked writes to instance/.
        # The baseline mtime captured at function entry lets the notifier
        # ignore writes made by later pipeline steps (failure notifier,
        # reflection, pr_review_learning) when deciding whether the Claude
        # session itself already informed the user.
        _notify_mission_result(
            mission_title=mission_title,
            instance_dir=instance_dir,
            stdout_file=stdout_file,
            start_time=start_time,
            exit_code=exit_code,
            outbox_baseline_mtime=_outbox_baseline_mtime,
        )

        return result
    finally:
        _deadline_timer.cancel()


def commit_instance(instance_dir: str, message: str = "") -> bool:
    """Commit and push instance directory changes.

    Args:
        instance_dir: Path to instance directory.
        message: Custom commit message.  Falls back to timestamped default.

    Returns:
        True if a commit was created.
    """
    try:
        from app.git_sync import run_git

        run_git(instance_dir, "add", "-A")

        # Check if there are staged changes
        status = run_git(instance_dir, "diff", "--cached", "--name-only")
        if not status:
            return False  # No changes

        if not message:
            message = f"koan: {datetime.now().strftime('%Y-%m-%d-%H:%M')}"
        run_git(instance_dir, "commit", "-m", message)

        # Push to the current branch — skip if HEAD is detached
        branch = run_git(instance_dir, "rev-parse", "--abbrev-ref", "HEAD")
        if not branch or branch == "HEAD":
            print("[commit_instance] Skipping push: detached HEAD", file=sys.stderr)
            return True
        run_git(instance_dir, "push", "origin", branch)
        return True
    except Exception as e:
        print(f"[commit_instance] Instance commit failed: {e}", file=sys.stderr)
        return False


# --- CLI interface ---

def _cli_build_command(args: list) -> None:
    """CLI: python -m app.mission_runner build-command ..."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--autonomous-mode", default="implement")
    parser.add_argument("--extra-flags", default="")
    parsed = parser.parse_args(args)

    cmd, _cleanup_paths = build_mission_command(
        prompt=parsed.prompt,
        autonomous_mode=parsed.autonomous_mode,
        extra_flags=parsed.extra_flags,
    )
    # Output as space-separated for bash consumption
    # (prompt will be handled separately via file)
    print("\n".join(cmd))
    # NOTE: any temp system-prompt file referenced in cmd is leaked here —
    # this CLI subcommand is a debug/inspection helper, not the real launch
    # path. The agent loop uses build_mission_command() directly and cleans
    # up via cmd_cleanup_paths in run.py / session_manager.py.


def _cli_parse_output(args: list) -> None:
    """CLI: python -m app.mission_runner parse-output <json_file>"""
    if len(args) < 1:
        print("Usage: mission_runner.py parse-output <json_file>", file=sys.stderr)
        sys.exit(1)

    filepath = args[0]
    try:
        raw = Path(filepath).read_text()
    except OSError as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        sys.exit(1)

    text = parse_claude_output(raw)
    if text:
        print(text)


def _cli_post_mission(args: list) -> None:
    """CLI: python -m app.mission_runner post-mission ..."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--run-num", type=int, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--stdout-file", required=True)
    parser.add_argument("--stderr-file", required=True)
    parser.add_argument("--mission-title", default="")
    parser.add_argument("--autonomous-mode", default="")
    parser.add_argument("--start-time", type=int, default=0)
    parsed = parser.parse_args(args)

    result = run_post_mission(
        instance_dir=parsed.instance,
        project_name=parsed.project_name,
        project_path=parsed.project_path,
        run_num=parsed.run_num,
        exit_code=parsed.exit_code,
        stdout_file=parsed.stdout_file,
        stderr_file=parsed.stderr_file,
        mission_title=parsed.mission_title,
        autonomous_mode=parsed.autonomous_mode,
        start_time=parsed.start_time,
    )

    # Output key results for bash consumption
    if result["quota_exhausted"] and result["quota_info"]:
        reset_display, resume_msg = result["quota_info"]
        print(f"QUOTA_EXHAUSTED|{reset_display}|{resume_msg}")
        sys.exit(2)  # Special exit code for quota exhaustion

    if result.get("cost_tracking_failed"):
        print("COST_TRACKING_FAILED", file=sys.stderr)
    if result["pending_archived"]:
        print("PENDING_ARCHIVED", file=sys.stderr)
    if result["auto_merge_branch"]:
        print(f"AUTO_MERGE|{result['auto_merge_branch']}", file=sys.stderr)

    # Emit per-step failure signals so run.py / monitoring can identify
    # which post-mission step caused the exit-code-1 path.
    for step_name, step_info in result.get("pipeline_steps", {}).items():
        if step_info["status"] in ("fail", "timeout"):
            print(f"STEP_FAILED|{step_name}", file=sys.stderr)

    sys.exit(0 if result["success"] else 1)


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: mission_runner.py <build-command|parse-output|post-mission> [args]",
            file=sys.stderr,
        )
        sys.exit(1)

    subcommand = sys.argv[1]
    remaining = sys.argv[2:]

    if subcommand == "build-command":
        _cli_build_command(remaining)
    elif subcommand == "parse-output":
        _cli_parse_output(remaining)
    elif subcommand == "post-mission":
        _cli_post_mission(remaining)
    else:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
