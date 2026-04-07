"""Tests for the /list core skill — mission listing."""

import re
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# mission_prefix() unit tests
# ---------------------------------------------------------------------------

class TestMissionPrefix:
    """Test the mission_prefix helper for category detection."""

    def test_plan_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /plan add dark mode") == "\U0001f9e0"

    def test_implement_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /implement https://github.com/issue/1") == "\U0001f528"

    def test_rebase_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /rebase https://github.com/pr/42") == "\U0001f504"

    def test_recreate_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /recreate https://github.com/pr/42") == "\U0001f501"

    def test_ai_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /ai backend") == "\u2728"

    def test_magic_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /magic koan") == "\u2728"

    def test_fix_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /fix https://github.com/owner/repo/issues/42") == "\U0001f41e"

    def test_fix_with_project_tag(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- [project:backend] /fix https://github.com/o/r/issues/1") == "\U0001f41e"

    def test_regular_mission_gets_clipboard(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- fix the login bug") == "\U0001f4cb"

    def test_review_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /review https://github.com/o/r/pull/42") == "\U0001f50d"

    def test_check_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /check https://github.com/pr/42") == "\U0001f50d"

    def test_refactor_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /refactor https://github.com/o/r/pull/5") == "\U0001f6e0\ufe0f"

    def test_claudemd_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /claudemd koan") == "\U0001f4dd"

    def test_claude_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /claude backend") == "\U0001f4dd"

    def test_claude_md_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /claude_md frontend") == "\U0001f4dd"

    def test_security_audit_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /security_audit koan") == "\U0001f6e1\ufe0f"

    def test_security_alias_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /security koan") == "\U0001f6e1\ufe0f"

    def test_audit_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /audit koan") == "\U0001f50e"

    def test_incident_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- /incident koan server down") == "\U0001f6a8"

    def test_unknown_command_gets_generic_prefix(self):
        from skills.core.list.handler import mission_prefix
        # Unknown slash commands now get the generic 📋 prefix
        # instead of no prefix at all
        assert mission_prefix("- /unknown_skill do thing") == "\U0001f4cb"

    def test_project_tag_with_plan(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- [project:koan] /plan add feature") == "\U0001f9e0"

    def test_project_tag_with_regular_mission(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- [project:webapp] fix CSRF") == "\U0001f4cb"

    def test_projet_tag_french(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("- [projet:koan] /rebase https://url") == "\U0001f504"

    def test_no_dash_prefix(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("/plan something") == "\U0001f9e0"

    def test_scoped_command_gets_generic_prefix(self):
        from skills.core.list.handler import mission_prefix
        # Scoped commands without a specific category get the generic prefix
        assert mission_prefix("- /custom.myskill do thing") == "\U0001f4cb"

    def test_empty_string(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("") == "\U0001f4cb"

    def test_whitespace_handling(self):
        from skills.core.list.handler import mission_prefix
        assert mission_prefix("  - /plan add feature  ") == "\U0001f9e0"

    def test_case_insensitive_command(self):
        from skills.core.list.handler import mission_prefix
        # Commands in mission text are always lowercase, but test robustness
        assert mission_prefix("- /Plan add feature") == "\U0001f9e0"


# ---------------------------------------------------------------------------
# Handler tests (direct handler invocation)
# ---------------------------------------------------------------------------

class TestListHandler:
    """Test the list skill handler directly."""

    def _make_ctx(self, tmp_path, missions_content=None):
        """Create a SkillContext with optional missions.md."""
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        if missions_content is not None:
            (instance_dir / "missions.md").write_text(missions_content)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="list",
        )

    def test_no_missions_file(self, tmp_path):
        from skills.core.list.handler import handle

        ctx = self._make_ctx(tmp_path)
        result = handle(ctx)
        assert "No missions file" in result

    def test_empty_missions(self, tmp_path):
        from skills.core.list.handler import handle

        ctx = self._make_ctx(tmp_path, "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
        result = handle(ctx)
        assert "No missions pending or in progress" in result

    def test_pending_missions(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - fix the login bug
            - add dark mode
            - refactor auth module

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "PENDING" in result
        assert "\U0001f4cb fix the login bug" in result
        assert "\U0001f4cb add dark mode" in result
        assert "\U0001f4cb refactor auth module" in result
        assert "IN PROGRESS" not in result

    def test_in_progress_missions(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            ## In Progress

            - implement new feature

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "IN PROGRESS" in result
        assert "\U0001f4cb implement new feature" in result
        assert "PENDING" not in result

    def test_both_sections(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - fix bug A
            - fix bug B

            ## In Progress

            - working on feature X

            ## Done

            - done task
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "IN PROGRESS" in result
        assert "\U0001f4cb working on feature X" in result
        assert "PENDING" in result
        assert "\U0001f4cb fix bug A" in result
        assert "\U0001f4cb fix bug B" in result
        # IN PROGRESS should appear before PENDING
        assert result.index("IN PROGRESS") < result.index("PENDING")

    def test_project_tags_displayed(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - [project:koan] fix the parser
            - [project:webapp] add CSRF

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "[koan]" in result
        assert "[webapp]" in result
        assert "fix the parser" in result
        assert "add CSRF" in result
        # No raw tag format in output
        assert "[project:" not in result

    def test_long_missions_truncated(self, tmp_path):
        from skills.core.list.handler import handle

        long_mission = "- " + "x" * 200
        missions = f"# Missions\n\n## Pending\n\n{long_mission}\n\n## In Progress\n\n## Done\n"
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "..." in result

    def test_french_section_headers(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - mission en fran\u00e7ais

            ## In Progress

            - t\u00e2che active

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "IN PROGRESS" in result
        assert "PENDING" in result

    def test_plan_mission_gets_brain_prefix(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - [project:koan] /plan add dark mode
            - [project:koan] /implement https://github.com/issue/1
            - [project:koan] /fix https://github.com/owner/repo/issues/5
            - [project:koan] /rebase https://github.com/pr/42
            - [project:koan] /review https://github.com/o/r/pull/10
            - [project:koan] /refactor https://github.com/o/r/pull/11
            - fix some bug

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "\U0001f9e0" in result  # plan
        assert "\U0001f528" in result  # implement
        assert "\U0001f41e" in result  # fix
        assert "\U0001f504" in result  # rebase
        assert "\U0001f50d" in result  # review
        assert "\U0001f6e0\ufe0f" in result  # refactor
        assert "\U0001f4cb" in result  # regular mission

    def test_check_command_gets_check_prefix(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - /check https://github.com/pr/42

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        # /check gets its SKILL.md emoji (🔍, matching the handler ack)
        assert "\U0001f50d" in result

    def test_unknown_command_gets_generic_prefix(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - /some_unknown_skill do thing

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        # Unknown slash commands now get the generic 📋 prefix
        assert "\U0001f4cb" in result

    def test_mixed_categories(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - [project:koan] /plan add feature
            - [project:koan] /ai backend
            - [project:koan] /recreate https://github.com/pr/1
            - [project:koan] /magic koan

            ## In Progress

            - [project:koan] /implement https://github.com/issue/5

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        # Check all categories present
        assert "\U0001f9e0" in result  # plan
        assert "\u2728" in result  # ai/magic (appears twice)
        assert "\U0001f501" in result  # recreate
        assert "\U0001f528" in result  # implement


# ---------------------------------------------------------------------------
# _clean_mission helper
# ---------------------------------------------------------------------------

class TestCleanMission:
    def test_strip_dash_prefix(self):
        from app.missions import clean_mission_display
        assert clean_mission_display("- fix the bug") == "fix the bug"

    def test_strip_project_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] fix parser")
        assert result == "[koan] fix parser"

    def test_strip_projet_tag(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [projet:webapp] add feature")
        assert result == "[webapp] add feature"

    def test_no_tag(self):
        from app.missions import clean_mission_display
        assert clean_mission_display("- simple task") == "simple task"

    def test_truncation(self):
        from app.missions import clean_mission_display
        long = "- " + "a" * 200
        result = clean_mission_display(long)
        assert result.endswith("...")
        assert len(result) == 120

    def test_github_origin_marker_stripped(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] /rebase https://github.com/o/r/pull/1 📬")
        assert "📬" not in result
        assert result == "[koan] /rebase https://github.com/o/r/pull/1"

    def test_no_marker_unchanged(self):
        from app.missions import clean_mission_display
        result = clean_mission_display("- [project:koan] /plan add feature")
        assert result == "[koan] /plan add feature"


# ---------------------------------------------------------------------------
# GitHub origin marker display in /list
# ---------------------------------------------------------------------------

class TestGithubOriginMarker:
    """Test that 📬 marker is repositioned from trailing to leading in /list."""

    def _make_ctx(self, tmp_path, missions_content):
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        (instance_dir / "missions.md").write_text(missions_content)
        return SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="list",
        )

    def test_github_marker_prepended_to_prefix(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - [project:koan] /rebase https://github.com/o/r/pull/1 📬

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        # 📬 should appear before the 🔄 rebase prefix
        assert "📬🔄" in result
        # Trailing marker should not appear in display text
        lines = result.split("\n")
        rebase_line = [l for l in lines if "/rebase" in l][0]
        assert not rebase_line.rstrip().endswith("📬")

    def test_no_marker_no_change(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - [project:koan] /plan add dark mode

            ## In Progress

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "📬" not in result
        assert "🧠" in result

    def test_marker_on_in_progress(self, tmp_path):
        from skills.core.list.handler import handle

        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            ## In Progress

            - [project:koan] /check https://github.com/o/r/pull/5 📬

            ## Done
        """)
        ctx = self._make_ctx(tmp_path, missions)
        result = handle(ctx)
        assert "📬🔍" in result


# ---------------------------------------------------------------------------
# Integration: command routing via awake.py
# ---------------------------------------------------------------------------

class TestListCommandRouting:
    """Test that /list, /queue, /ls route to the list skill via awake."""

    @patch("app.command_handlers.send_telegram")
    def test_list_routes_via_skill(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- test mission\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/list")
        mock_send.assert_called_once()
        output = mock_send.call_args[0][0]
        assert "PENDING" in output
        assert "test mission" in output

    @patch("app.command_handlers.send_telegram")
    def test_queue_alias_routes_to_list(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- queued task\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/queue")
        mock_send.assert_called_once()
        assert "queued task" in mock_send.call_args[0][0]

    @patch("app.command_handlers.send_telegram")
    def test_ls_alias_routes_to_list(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n- ls task\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/ls")
        mock_send.assert_called_once()
        assert "ls task" in mock_send.call_args[0][0]

    @patch("app.command_handlers.send_telegram")
    def test_list_empty_queue(self, mock_send, tmp_path):
        from app.command_handlers import handle_command

        missions_file = tmp_path / "missions.md"
        missions_file.write_text(
            "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
        )
        with patch("app.command_handlers.KOAN_ROOT", tmp_path), \
             patch("app.command_handlers.INSTANCE_DIR", tmp_path), \
             patch("app.command_handlers.MISSIONS_FILE", missions_file):
            handle_command("/list")
        mock_send.assert_called_once()
        assert "No missions" in mock_send.call_args[0][0]

    @patch("app.command_handlers.send_telegram")
    def test_list_appears_in_help(self, mock_send, tmp_path):
        """Verify /list is included in /help missions group output."""
        from app.command_handlers import _handle_help_detail

        _handle_help_detail("missions")
        mock_send.assert_called_once()
        help_text = mock_send.call_args[0][0]
        assert "/list" in help_text
        assert "missions" in help_text.lower()


# ---------------------------------------------------------------------------
# Friendly timestamp formatting
# ---------------------------------------------------------------------------

class TestFriendlyTimestamps:
    """Test the timestamp humanization in /list display."""

    def test_format_time_am(self):
        from skills.core.list.handler import _format_time_friendly
        assert _format_time_friendly(9, 0) == "9am"

    def test_format_time_pm(self):
        from skills.core.list.handler import _format_time_friendly
        assert _format_time_friendly(14, 0) == "2pm"

    def test_format_time_noon(self):
        from skills.core.list.handler import _format_time_friendly
        assert _format_time_friendly(12, 0) == "12pm"

    def test_format_time_midnight(self):
        from skills.core.list.handler import _format_time_friendly
        assert _format_time_friendly(0, 0) == "12am"

    def test_format_time_with_minutes(self):
        from skills.core.list.handler import _format_time_friendly
        assert _format_time_friendly(9, 30) == "9:30am"

    def test_format_time_pm_with_minutes(self):
        from skills.core.list.handler import _format_time_friendly
        assert _format_time_friendly(20, 14) == "8:14pm"

    def test_today_timestamp(self):
        from skills.core.list.handler import _format_friendly_timestamp
        now = datetime(2026, 4, 7, 22, 0)
        result = _format_friendly_timestamp("2026-04-07T20:14", now)
        assert result == "@ 8:14pm"

    def test_today_round_hour(self):
        from skills.core.list.handler import _format_friendly_timestamp
        now = datetime(2026, 4, 7, 22, 0)
        result = _format_friendly_timestamp("2026-04-07T09:00", now)
        assert result == "@ 9am"

    def test_this_week_not_today(self):
        from skills.core.list.handler import _format_friendly_timestamp
        # 2026-04-07 is Tuesday; 2026-04-06 is Monday (same week)
        now = datetime(2026, 4, 7, 22, 0)
        result = _format_friendly_timestamp("2026-04-06T09:00", now)
        assert result == "Mon @ 9am"

    def test_older_than_this_week(self):
        from skills.core.list.handler import _format_friendly_timestamp
        # 2026-04-07 is Tuesday; 2026-03-31 is the previous Tuesday
        now = datetime(2026, 4, 7, 22, 0)
        result = _format_friendly_timestamp("2026-03-31T09:00", now)
        assert result == "Tue 3/31 @ 9am"

    def test_humanize_queued_only(self):
        from skills.core.list.handler import _humanize_timestamps
        now = datetime(2026, 4, 7, 22, 0)
        text = "fix bug ⏳(2026-04-07T20:14)"
        result = _humanize_timestamps(text, now)
        assert result == "fix bug ⏳@ 8:14pm"

    def test_humanize_queued_and_started(self):
        from skills.core.list.handler import _humanize_timestamps
        now = datetime(2026, 4, 7, 22, 0)
        text = "fix bug ⏳(2026-04-07T20:14) ▶(2026-04-07T20:20)"
        result = _humanize_timestamps(text, now)
        # Should show last timestamp (started)
        assert result == "fix bug ▶@ 8:20pm"

    def test_humanize_no_timestamps(self):
        from skills.core.list.handler import _humanize_timestamps
        text = "fix bug"
        result = _humanize_timestamps(text)
        assert result == "fix bug"

    def test_humanize_unparseable_timestamp(self):
        from skills.core.list.handler import _format_friendly_timestamp
        now = datetime(2026, 4, 7, 22, 0)
        result = _format_friendly_timestamp("not-a-date", now)
        assert result == "not-a-date"

    def test_humanize_space_format(self):
        """Handle ✅/❌ format with space instead of T."""
        from skills.core.list.handler import _humanize_timestamps
        now = datetime(2026, 4, 7, 22, 0)
        text = "fix bug ⏳(2026-04-07T20:14) ▶(2026-04-07T20:20) ✅(2026-04-07 21:00)"
        result = _humanize_timestamps(text, now)
        assert result == "fix bug ✅@ 9pm"

    def test_list_handler_renders_friendly_timestamps(self, tmp_path):
        """Integration test: /list shows friendly timestamps, not raw ISO."""
        from skills.core.list.handler import handle

        now = datetime(2026, 4, 7, 22, 0)
        missions = textwrap.dedent("""\
            # Missions

            ## Pending

            - fix the login bug ⏳(2026-04-07T20:14)
            - [project:koan] add dark mode ⏳(2026-04-06T09:00)

            ## In Progress

            - [project:koan] working on it ⏳(2026-04-07T14:00) ▶(2026-04-07T14:05)

            ## Done
        """)
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir(exist_ok=True)
        (instance_dir / "missions.md").write_text(missions)
        ctx = SkillContext(
            koan_root=tmp_path,
            instance_dir=instance_dir,
            command_name="list",
        )

        with patch("skills.core.list.handler.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime = datetime.strptime
            result = handle(ctx)

        # Raw ISO timestamps should not appear
        assert "2026-04-07T20:14" not in result
        assert "2026-04-06T09:00" not in result
        # Friendly timestamps should appear
        assert "⏳@ 8:14pm" in result
        assert "⏳Mon @ 9am" in result
        assert "▶@ 2:05pm" in result
