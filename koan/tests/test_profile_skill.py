"""Tests for the /profile core skill — handler and skill dispatch."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.skills import SkillContext


# ---------------------------------------------------------------------------
# Import handler
# ---------------------------------------------------------------------------

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "profile" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("profile_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_md = instance_dir / "missions.md"
    missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="profile",
        args="",
        send_message=MagicMock(),
    )


# ---------------------------------------------------------------------------
# handle() — usage / routing
# ---------------------------------------------------------------------------

class TestHandleRouting:
    def test_no_args_returns_usage(self, handler, ctx):
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "/profile" in result

    def test_usage_shows_examples(self, handler, ctx):
        result = handler.handle(ctx)
        assert "project-name" in result or "pr-url" in result


# ---------------------------------------------------------------------------
# handle() — project name queuing
# ---------------------------------------------------------------------------

class TestProjectNameQueuing:
    def test_project_name_queues_mission(self, handler, ctx):
        ctx.args = "koan"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Profile queued" in result
            assert "koan" in result
            mock_insert.assert_called_once()

    def test_project_mission_entry_format(self, handler, ctx):
        ctx.args = "koan"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.resolve_project_path", return_value="/home/koan"), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            entry = mock_insert.call_args[0][1]
            assert "[project:koan]" in entry
            assert "/profile" in entry

    def test_unknown_project_returns_error(self, handler, ctx):
        ctx.args = "nonexistent"
        with patch("app.utils.resolve_project_path", return_value=None), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "\u274c" in result
            assert "nonexistent" in result

    def test_unknown_project_lists_known(self, handler, ctx):
        ctx.args = "nonexistent"
        with patch("app.utils.resolve_project_path", return_value=None), \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan"), ("myapp", "/home/myapp")]):
            result = handler.handle(ctx)
            assert "koan" in result


# ---------------------------------------------------------------------------
# handle() — PR URL queuing
# ---------------------------------------------------------------------------

class TestPrUrlQueuing:
    def test_pr_url_queues_mission(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Profile queued" in result
            assert "PR" in result or "#42" in result
            mock_insert.assert_called_once()

    def test_pr_mission_contains_url(self, handler, ctx):
        ctx.args = "https://github.com/sukria/koan/pull/42"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            handler.handle(ctx)
            entry = mock_insert.call_args[0][1]
            assert "github.com/sukria/koan/pull/42" in entry

    def test_pr_url_in_text_extracted(self, handler, ctx):
        ctx.args = "please profile https://github.com/sukria/koan/pull/99 thanks"
        with patch("app.utils.insert_pending_mission") as mock_insert, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/home/koan")]):
            result = handler.handle(ctx)
            assert "Profile queued" in result


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "profile" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill is not None
        assert skill.name == "profile"
        assert skill.scope == "core"

    def test_skill_not_worker(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "profile" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill.worker is False

    def test_skill_has_aliases(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "profile" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        aliases = skill.commands[0].aliases
        assert "perf" in aliases
        assert "benchmark" in aliases

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("profile")
        assert skill is not None
        assert skill.name == "profile"

    def test_alias_perf_registered(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("perf")
        assert skill is not None
        assert skill.name == "profile"

    def test_alias_benchmark_registered(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("benchmark")
        assert skill is not None
        assert skill.name == "profile"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()

    def test_skill_github_enabled(self):
        from app.skills import parse_skill_md
        skill_path = Path(__file__).parent.parent / "skills" / "core" / "profile" / "SKILL.md"
        skill = parse_skill_md(skill_path)
        assert skill.github_enabled is True


# ---------------------------------------------------------------------------
# skill_dispatch — profile command building
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_profile_in_skill_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS
        assert "profile" in _SKILL_RUNNERS

    def test_build_profile_cmd_basic(self):
        from app.skill_dispatch import build_skill_command
        cmd = build_skill_command(
            command="profile",
            args="",
            project_name="koan",
            project_path="/home/koan",
            koan_root="/koan-root",
            instance_dir="/instance",
        )
        assert cmd is not None
        assert "--project-path" in cmd
        assert "/home/koan" in cmd
        assert "--instance-dir" in cmd

    def test_build_profile_cmd_with_pr_url(self):
        from app.skill_dispatch import build_skill_command
        cmd = build_skill_command(
            command="profile",
            args="https://github.com/sukria/koan/pull/42",
            project_name="koan",
            project_path="/home/koan",
            koan_root="/koan-root",
            instance_dir="/instance",
        )
        assert cmd is not None
        assert "--pr-url" in cmd
        assert "https://github.com/sukria/koan/pull/42" in cmd

    def test_dispatch_profile_mission(self):
        from app.skill_dispatch import dispatch_skill_mission
        with patch("app.skill_dispatch.is_known_project", return_value=True):
            cmd = dispatch_skill_mission(
                mission_text="[project:koan] /profile",
                project_name="koan",
                project_path="/home/koan",
                koan_root="/koan-root",
                instance_dir="/instance",
            )
        assert cmd is not None
        assert "profile_runner" in " ".join(cmd) or "profile.profile_runner" in " ".join(cmd)


# ---------------------------------------------------------------------------
# profile_runner — unit tests
# ---------------------------------------------------------------------------

class TestProfileRunner:
    """Tests for the profile_runner module (prompt, parsing, saving)."""

    def test_runner_module_importable(self):
        import importlib
        mod = importlib.import_module("skills.core.profile.profile_runner")
        assert hasattr(mod, "run_profile")
        assert hasattr(mod, "main")

    def test_build_prompt_basic(self):
        from skills.core.profile.profile_runner import build_profile_prompt
        skill_dir = Path(__file__).parent.parent / "skills" / "core" / "profile"
        prompt = build_profile_prompt("myproject", skill_dir=skill_dir)
        assert "myproject" in prompt
        assert "performance" in prompt.lower() or "profile" in prompt.lower()

    def test_build_prompt_with_pr_url(self):
        from skills.core.profile.profile_runner import build_profile_prompt
        skill_dir = Path(__file__).parent.parent / "skills" / "core" / "profile"
        prompt = build_profile_prompt(
            "myproject",
            pr_url="https://github.com/owner/repo/pull/42",
            skill_dir=skill_dir,
        )
        assert "github.com/owner/repo/pull/42" in prompt
        assert "PR Context" in prompt

    def test_extract_report_body_with_header(self):
        from skills.core.profile.profile_runner import _extract_report_body
        raw = "Some preamble\n\nPerformance Profile — koan\n\n## Summary\nGood."
        result = _extract_report_body(raw)
        assert result.startswith("Performance Profile")

    def test_extract_report_body_with_summary(self):
        from skills.core.profile.profile_runner import _extract_report_body
        raw = "Blah blah\n\n## Summary\nOverview here."
        result = _extract_report_body(raw)
        assert result.startswith("## Summary")

    def test_extract_report_body_fallback(self):
        from skills.core.profile.profile_runner import _extract_report_body
        raw = "Just plain text output"
        result = _extract_report_body(raw)
        assert result == "Just plain text output"

    def test_extract_perf_score(self):
        from skills.core.profile.profile_runner import _extract_perf_score
        report = "**Performance Score**: 4/10\nSome details"
        assert _extract_perf_score(report) == 4

    def test_extract_perf_score_none_if_missing(self):
        from skills.core.profile.profile_runner import _extract_perf_score
        assert _extract_perf_score("No score here") is None

    def test_extract_perf_score_rejects_out_of_range(self):
        from skills.core.profile.profile_runner import _extract_perf_score
        assert _extract_perf_score("**Performance Score**: 15/10") is None

    def test_extract_missions(self):
        from skills.core.profile.profile_runner import _extract_missions
        report = (
            "## Findings\nStuff\n\n"
            "## Suggested Missions\n"
            "1. Optimize the hot loop in parser.py\n"
            "2. Add caching to API calls\n"
            "3. Reduce startup imports\n"
        )
        missions = _extract_missions(report)
        assert len(missions) == 3
        assert "Optimize" in missions[0]

    def test_extract_missions_empty(self):
        from skills.core.profile.profile_runner import _extract_missions
        assert _extract_missions("No missions section") == []

    def test_save_report(self, tmp_path):
        from skills.core.profile.profile_runner import _save_report
        report_path = _save_report(tmp_path, "myproject", "Test report", 5)
        assert report_path.exists()
        content = report_path.read_text()
        assert "Test report" in content
        assert "Performance score: 5/10" in content

    def test_save_report_creates_dirs(self, tmp_path):
        from skills.core.profile.profile_runner import _save_report
        report_path = _save_report(tmp_path, "newproject", "Report", None)
        assert report_path.exists()
        assert "newproject" in str(report_path)

    def test_queue_missions(self, tmp_path):
        instance_dir = tmp_path
        missions_md = instance_dir / "missions.md"
        missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
        from skills.core.profile.profile_runner import _queue_missions
        queued = _queue_missions(instance_dir, "koan", ["Fix slow loop", "Add cache"])
        assert queued == 2
        content = missions_md.read_text()
        assert "[project:koan]" in content

    def test_run_profile_success(self, tmp_path):
        from skills.core.profile.profile_runner import run_profile
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_md = instance_dir / "missions.md"
        missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")
        skill_dir = Path(__file__).parent.parent / "skills" / "core" / "profile"

        raw_output = (
            "Performance Profile — testproject\n\n"
            "## Summary\nLooks good.\n\n"
            "**Performance Score**: 3/10\n\n"
            "## Findings\n\n### Critical\nNone\n\n"
            "## Suggested Missions\n"
            "1. Add connection pooling\n"
        )
        with patch(
            "skills.core.profile.profile_runner._run_claude_scan",
            return_value=raw_output,
        ):
            success, summary = run_profile(
                project_path="/fake/path",
                project_name="testproject",
                instance_dir=str(instance_dir),
                notify_fn=MagicMock(),
                skill_dir=skill_dir,
                queue_missions=True,
            )
        assert success is True
        assert "performance_profile.md" in summary
        assert "3/10" in summary

    def test_run_profile_failure(self, tmp_path):
        from skills.core.profile.profile_runner import run_profile
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        skill_dir = Path(__file__).parent.parent / "skills" / "core" / "profile"

        with patch(
            "skills.core.profile.profile_runner._run_claude_scan",
            side_effect=RuntimeError("CLI failed"),
        ):
            success, summary = run_profile(
                project_path="/fake/path",
                project_name="testproject",
                instance_dir=str(instance_dir),
                notify_fn=MagicMock(),
                skill_dir=skill_dir,
            )
        assert success is False
        assert "failed" in summary.lower()

    def test_run_profile_empty_output(self, tmp_path):
        from skills.core.profile.profile_runner import run_profile
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        skill_dir = Path(__file__).parent.parent / "skills" / "core" / "profile"

        with patch(
            "skills.core.profile.profile_runner._run_claude_scan",
            return_value="",
        ):
            success, summary = run_profile(
                project_path="/fake/path",
                project_name="testproject",
                instance_dir=str(instance_dir),
                notify_fn=MagicMock(),
                skill_dir=skill_dir,
            )
        assert success is False
        assert "no output" in summary.lower()

    def test_main_cli(self, tmp_path):
        from skills.core.profile.profile_runner import main
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        missions_md = instance_dir / "missions.md"
        missions_md.write_text("## Pending\n\n## In Progress\n\n## Done\n")

        with patch(
            "skills.core.profile.profile_runner._run_claude_scan",
            return_value="## Summary\nOK\n**Performance Score**: 2/10",
        ):
            exit_code = main([
                "--project-path", str(tmp_path),
                "--project-name", "test",
                "--instance-dir", str(instance_dir),
                "--no-queue",
            ])
        assert exit_code == 0
