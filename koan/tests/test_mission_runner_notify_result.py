"""Tests for _notify_mission_result — forwards Claude result text to outbox.md."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_claude_stdout(path: Path, result_text: str) -> None:
    """Write a minimal Claude --output-format=json result blob to path."""
    blob = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result_text,
    }
    path.write_text(json.dumps(blob))


# A single hook used across tests to make customer-facing detection
# deterministic without requiring a real skill registry. Individual tests
# pass the markers they want via the ``markers`` argument to ``patch``.
_MARKER_PATCH_TARGET = "app.mission_runner._resolve_forward_result_markers"


class TestShouldForwardResult:
    def test_empty_body_returns_false(self):
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            assert _should_forward_result("any title", "") == (False, False)
            assert _should_forward_result("any title", "   \n  ") == (False, False)

    def test_skip_marker_flags_alert(self):
        from app.mission_runner import _should_forward_result
        body = "🏁 [my_team] **SKIP — PROJ-53396**\n\nReason..."
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, alert = _should_forward_result("any title", body)
        assert forward is True
        assert alert is True

    def test_error_marker_flags_alert(self):
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, alert = _should_forward_result("", "Result body **ERROR** here")
        assert forward and alert

    def test_blocked_marker_flags_alert(self):
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, alert = _should_forward_result("", "Mission blocked: no access")
        assert forward and alert

    def test_customer_facing_title_forwards_even_on_success(self):
        """A registered skill that opted into forward_result is recognised
        via the markers returned by the registry — even when the body has
        no alert keywords."""
        from app.mission_runner import _should_forward_result
        title = "Use the my-custom-workflow agent to resolve issue PROJ-1"
        body = "Done. PR opened: #42"
        with patch(_MARKER_PATCH_TARGET, return_value=["my-custom-workflow"]):
            forward, alert = _should_forward_result(title, body)
        assert forward is True
        assert alert is False

    def test_neutral_title_and_neutral_body_does_not_forward(self):
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, _ = _should_forward_result(
                "Refactor cache layer", "Refactored 3 files, all tests pass."
            )
        assert forward is False

    def test_no_pr_word_boundary_does_not_match_no_problem(self):
        """Regression: 'no PR' must not match 'no problem' / 'no projects'."""
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            for body in (
                "No problem — refactor complete.",
                "Found no projects with stale branches.",
                "no prior context needed.",
                "no protected branches affected.",
            ):
                forward, alert = _should_forward_result("Refactor", body)
                assert forward is False, f"false-positive on: {body!r}"
                assert alert is False

    def test_no_pr_with_word_boundary_does_match(self):
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, alert = _should_forward_result(
                "Refactor", "Branch pushed but no PR opened — see logs."
            )
        assert forward and alert

    def test_couldnt_execute_variants_match(self):
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            for body in (
                "could not execute the migration step",
                "couldn't execute the test harness",
                "couldn’t execute the test harness",  # typographic apostrophe
            ):
                forward, alert = _should_forward_result("any", body)
                assert forward and alert, f"missed alert on: {body!r}"

    def test_empty_marker_list_means_no_customer_facing_match(self):
        """When no skill has opted in, customer-facing detection is off and
        only body alerts can trigger forwarding."""
        from app.mission_runner import _should_forward_result
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, _ = _should_forward_result(
                "Use the my-custom-workflow agent on PROJ-1",
                "Done. PR opened: #42",
            )
        assert forward is False


class TestNotifyMissionResult:
    def test_writes_action_priority_for_skip_outcome(self, instance_dir, tmp_path):
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(
            stdout_file, "🏁 [my_team] **SKIP — PROJ-53396**\n\nNo access."
        )

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="Run the my-custom-workflow agent to resolve PROJ-53396",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
        )

        content = (instance_dir / "outbox.md").read_text()
        assert "**SKIP — PROJ-53396**" in content
        assert "[priority:action]" in content
        assert "PROJ-53396" in content
        assert "⚠️" in content  # Alert icon stays even when priority is ACTION

    def test_writes_info_icon_for_customer_facing_success(
        self, instance_dir, tmp_path
    ):
        """A skill opted into result forwarding gets ℹ️ on a non-alert body."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "Done. PR #42 opened.")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        with patch(_MARKER_PATCH_TARGET, return_value=["my-custom-workflow"]):
            _notify_mission_result(
                mission_title="Run the my-custom-workflow agent to resolve PROJ-99",
                instance_dir=str(instance_dir),
                stdout_file=str(stdout_file),
                start_time=start_ts,
                exit_code=0,
            )

        content = (instance_dir / "outbox.md").read_text()
        assert "[priority:action]" in content
        assert "PR #42" in content
        assert "ℹ️" in content  # Non-alert icon for success bodies

    def test_permission_deadlock_flagged_as_alert(self, instance_dir, tmp_path):
        """A body containing 'permission deadlock' gets the alert icon
        regardless of skill registration."""
        from app.mission_runner import _notify_mission_result, _should_forward_result

        body = (
            "The agent ran for ~21 minutes and 298 tool calls in an isolated "
            "worktree but never produced any code changes. It hit a permission "
            "deadlock: the session sandbox blocks Write, Edit, and "
            "Bash(git checkout/commit/push)."
        )
        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            forward, alert = _should_forward_result(
                "Run the my-custom-workflow agent to resolve PROJ-53396", body
            )
        assert forward and alert

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, body)
        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="Run the my-custom-workflow agent to resolve PROJ-53396",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
        )

        content = (instance_dir / "outbox.md").read_text()
        assert "[priority:action]" in content
        assert "⚠️" in content
        assert "permission deadlock" in content

    def test_skips_when_outbox_already_modified_after_start(
        self, instance_dir, tmp_path
    ):
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "🏁 [my_team] **SKIP — X-1**\n\nReason")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts + 10, start_ts + 10))
        before = (instance_dir / "outbox.md").read_text()

        _notify_mission_result(
            mission_title="Run the my-custom-workflow agent to resolve X-1",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
        )

        assert (instance_dir / "outbox.md").read_text() == before

    def test_forwards_on_non_zero_exit_with_alert_body(
        self, instance_dir, tmp_path
    ):
        """Non-zero exits forward when body has alert markers — failures
        carry the most useful error context."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "🏁 **SKIP** — sandbox blocked write")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="Run the my-custom-workflow agent to resolve X-1",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=1,
        )

        content = (instance_dir / "outbox.md").read_text()
        assert "**SKIP**" in content
        assert "⚠️" in content  # Non-zero exit is always rendered as alert
        assert "[priority:action]" in content

    def test_non_zero_exit_forces_alert_icon_even_for_customer_facing(
        self, instance_dir, tmp_path
    ):
        """Even a customer-facing mission gets the alert icon on failure."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "Done. PR #42 opened.")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        with patch(_MARKER_PATCH_TARGET, return_value=["my-custom-workflow"]):
            _notify_mission_result(
                mission_title="Run the my-custom-workflow agent to resolve X-9",
                instance_dir=str(instance_dir),
                stdout_file=str(stdout_file),
                start_time=start_ts,
                exit_code=2,
            )

        content = (instance_dir / "outbox.md").read_text()
        assert "⚠️" in content
        assert "ℹ️" not in content

    def test_truncates_long_result(self, instance_dir, tmp_path):
        from app.mission_runner import (
            _notify_mission_result,
            _RESULT_FORWARD_MAX_CHARS,
        )

        stdout_file = tmp_path / "stdout.json"
        big = "🏁 **SKIP — X-1**\n\n" + ("x" * 10_000)
        _write_claude_stdout(stdout_file, big)

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="t",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
        )

        content = (instance_dir / "outbox.md").read_text()
        assert len(content) < _RESULT_FORWARD_MAX_CHARS + 300

    def test_disabled_by_config_flag(self, instance_dir, tmp_path):
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "🏁 **SKIP** — X")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        with patch(
            "app.config.get_notify_mission_results", return_value=False
        ):
            _notify_mission_result(
                mission_title="t",
                instance_dir=str(instance_dir),
                stdout_file=str(stdout_file),
                start_time=start_ts,
                exit_code=0,
            )

        assert (instance_dir / "outbox.md").read_text() == ""

    def test_baseline_mtime_overrides_current_mtime_for_idempotency(
        self, instance_dir, tmp_path
    ):
        """H1 fix: when a baseline mtime is passed, late pipeline writes to
        outbox don't suppress the result notification.

        Simulates the production sequence:
          - mission starts at T
          - Claude session runs, exits without writing to outbox
          - run_post_mission captures baseline mtime = T-10 (pre-Claude)
          - a later pipeline step (e.g. _notify_pipeline_failures) writes,
            bumping current mtime to T+5
          - _notify_mission_result is called and MUST still forward.
        """
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(
            stdout_file, "🏁 **SKIP** — sandbox blocked work"
        )

        start_ts = int(time.time()) - 60
        baseline_mtime = float(start_ts - 10)  # pre-Claude snapshot
        # Simulate a late pipeline write that bumped mtime to "after start":
        os.utime(instance_dir / "outbox.md", (start_ts + 5, start_ts + 5))
        # Pre-fill the file with the late-pipeline warning so we can verify
        # our notification is appended, not replaced:
        (instance_dir / "outbox.md").write_text("⚠️ Pipeline issues: …\n")
        os.utime(instance_dir / "outbox.md", (start_ts + 5, start_ts + 5))

        _notify_mission_result(
            mission_title="any",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
            outbox_baseline_mtime=baseline_mtime,
        )

        content = (instance_dir / "outbox.md").read_text()
        assert "**SKIP**" in content, "baseline-mtime path must forward"
        assert "Pipeline issues" in content, "must append, not replace"

    def test_baseline_mtime_after_start_still_skips(self, instance_dir, tmp_path):
        """Baseline mtime > start_time means Claude itself wrote to outbox
        during the session — still skip to avoid double-notification."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "🏁 **SKIP** — X")

        start_ts = int(time.time()) - 60
        baseline_mtime = float(start_ts + 10)  # Claude wrote during session
        os.utime(instance_dir / "outbox.md", (start_ts - 100, start_ts - 100))
        before = (instance_dir / "outbox.md").read_text()

        _notify_mission_result(
            mission_title="any",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
            outbox_baseline_mtime=baseline_mtime,
        )

        assert (instance_dir / "outbox.md").read_text() == before

    def test_customer_facing_markers_come_from_skill_registry(
        self, instance_dir, tmp_path
    ):
        """M2 redesign: customer-facing detection is skill-driven via the
        SKILL.md ``forward_result: true`` opt-in (exposed through
        ``_resolve_forward_result_markers``). A brand-new marker provided by
        a skill the runtime has no built-in knowledge of must work without
        config edits."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "Operation complete — PR #99.")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        with patch(
            _MARKER_PATCH_TARGET,
            return_value=["/my_fix", "my-custom-workflow"],
        ):
            _notify_mission_result(
                mission_title="/my_fix PROJ-1 please",
                instance_dir=str(instance_dir),
                stdout_file=str(stdout_file),
                start_time=start_ts,
                exit_code=0,
            )

        content = (instance_dir / "outbox.md").read_text()
        assert "PR #99" in content
        assert "[priority:action]" in content

    def test_empty_registry_means_no_customer_facing_forwarding(
        self, instance_dir, tmp_path
    ):
        """With no skill opted into forward_result, customer-facing detection
        is fully off — only body alerts can still trigger forwarding."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "Done. PR #7 opened.")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        with patch(_MARKER_PATCH_TARGET, return_value=[]):
            _notify_mission_result(
                mission_title="/my_fix PROJ-1",
                instance_dir=str(instance_dir),
                stdout_file=str(stdout_file),
                start_time=start_ts,
                exit_code=0,
            )

        assert (instance_dir / "outbox.md").read_text() == ""

    def test_neutral_mission_with_neutral_body_does_not_post(
        self, instance_dir, tmp_path
    ):
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(stdout_file, "Refactored 3 files. Tests pass.")

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="Refactor cache layer",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
        )

        assert (instance_dir / "outbox.md").read_text() == ""

    def test_skill_skip_suppresses_forwarding(self, instance_dir, tmp_path):
        """When a skill exits 0 with '— skipping' in stdout, the result
        notification is suppressed because the skill already sent a direct
        notification (e.g. fix_runner's '⏭ Issue already closed')."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(
            stdout_file,
            "[fix] Starting fix runner\n"
            "Issue #42 (o/r) is already closed — skipping.",
        )

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="/fix https://github.com/o/r/issues/42",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=0,
        )

        assert (instance_dir / "outbox.md").read_text() == ""

    def test_skill_skip_non_zero_still_forwards(self, instance_dir, tmp_path):
        """A non-zero exit with '— skipping' is NOT suppressed — exit!=0
        means something went wrong even if the text mentions skipping."""
        from app.mission_runner import _notify_mission_result

        stdout_file = tmp_path / "stdout.json"
        _write_claude_stdout(
            stdout_file,
            "**SKIP** — Issue #42 is already closed — skipping.",
        )

        start_ts = int(time.time()) - 60
        os.utime(instance_dir / "outbox.md", (start_ts - 10, start_ts - 10))

        _notify_mission_result(
            mission_title="/fix https://github.com/o/r/issues/42",
            instance_dir=str(instance_dir),
            stdout_file=str(stdout_file),
            start_time=start_ts,
            exit_code=1,
        )

        content = (instance_dir / "outbox.md").read_text()
        assert "already closed" in content
