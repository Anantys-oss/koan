"""Tests for the /private_security_audit skill -- handler, runner, dispatch."""

import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext
from skills.core.audit.audit_runner import (
    AuditFinding,
    _write_findings_to_journal,
    run_audit,
)


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------

HANDLER_PATH = (
    Path(__file__).parent.parent
    / "skills" / "core" / "private_security_audit" / "handler.py"
)


def _load_handler():
    """Load the private_security_audit handler module dynamically."""
    spec = importlib.util.spec_from_file_location(
        "private_security_audit_handler", str(HANDLER_PATH),
    )
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
    missions_path = instance_dir / "missions.md"
    missions_path.write_text(
        "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
    )
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="private_security_audit",
        args="",
        send_message=MagicMock(),
    )


class TestHandleRouting:
    def test_help_flag_returns_usage(self, handler, ctx):
        ctx.args = "--help"
        result = handler.handle(ctx)
        assert "Usage:" in result
        assert "journal" in result.lower()

    def test_no_args_returns_error(self, handler, ctx):
        ctx.args = ""
        result = handler.handle(ctx)
        assert "❌" in result
        assert "Usage:" in result


class TestHandleQueueMission:
    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_named_project(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan"
        result = handler.handle(ctx)

        assert "queued" in result.lower()
        assert "journal" in result.lower()
        mock_insert.assert_called_once()
        mission_entry = mock_insert.call_args[0][1]
        assert "[project:koan]" in mission_entry
        assert "/private_security_audit" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_with_extra_context_and_limit(self, mock_insert, mock_resolve, handler, ctx):
        ctx.args = "koan focus on tokens limit=3"
        handler.handle(ctx)
        mission_entry = mock_insert.call_args[0][1]
        assert "/private_security_audit focus on tokens" in mission_entry
        assert "limit=3" in mission_entry

    @patch("app.utils.resolve_project_path", return_value="/path/koan")
    @patch("app.utils.insert_pending_mission")
    def test_default_limit_omitted_from_entry(
        self, mock_insert, mock_resolve, handler, ctx,
    ):
        ctx.args = "koan"
        handler.handle(ctx)
        mission_entry = mock_insert.call_args[0][1]
        assert "limit=" not in mission_entry

    @patch("app.utils.resolve_project_path", return_value=None)
    @patch("app.utils.get_known_projects", return_value=[("web", "/path/web")])
    def test_unknown_project(self, mock_projects, mock_resolve, handler, ctx):
        ctx.args = "nonexistent"
        result = handler.handle(ctx)
        assert "❌" in result
        assert "nonexistent" in result


# ---------------------------------------------------------------------------
# Journal-writing helper
# ---------------------------------------------------------------------------

class TestWriteFindingsToJournal:
    def _finding(self, **kw):
        defaults = dict(
            title="SQL injection in login",
            severity="critical",
            category="injection",
            location="src/auth.py:42",
            problem="Raw SQL string interpolation with user input.",
            why="Allows authentication bypass and data exfiltration.",
            suggested_fix="Use parameterized queries via the ORM.",
            effort="small",
        )
        defaults.update(kw)
        return AuditFinding(**defaults)

    def test_creates_dated_journal_file(self, tmp_path):
        findings = [self._finding()]
        path = _write_findings_to_journal(tmp_path, "demo", findings)

        assert path.exists()
        assert path.name == "demo.md"
        assert path.parent.parent.name == "journal"

    def test_writes_all_finding_fields(self, tmp_path):
        findings = [self._finding()]
        path = _write_findings_to_journal(tmp_path, "demo", findings)
        content = path.read_text()

        assert "Private Security Audit" in content
        assert "SQL injection in login" in content
        assert "src/auth.py:42" in content
        assert "Raw SQL string interpolation" in content
        assert "authentication bypass" in content
        assert "parameterized queries" in content

    def test_includes_focus_context_when_provided(self, tmp_path):
        findings = [self._finding()]
        path = _write_findings_to_journal(
            tmp_path, "demo", findings, extra_context="focus on auth",
        )
        assert "focus on auth" in path.read_text()

    def test_appends_rather_than_overwriting(self, tmp_path):
        findings_a = [self._finding(title="First finding")]
        findings_b = [self._finding(title="Second finding")]

        path_a = _write_findings_to_journal(tmp_path, "demo", findings_a)
        path_b = _write_findings_to_journal(tmp_path, "demo", findings_b)
        assert path_a == path_b

        content = path_a.read_text()
        assert "First finding" in content
        assert "Second finding" in content

    def test_handles_multiple_findings(self, tmp_path):
        findings = [
            self._finding(title="First", severity="critical"),
            self._finding(title="Second", severity="high"),
            self._finding(title="Third", severity="medium"),
        ]
        path = _write_findings_to_journal(tmp_path, "demo", findings)
        content = path.read_text()
        assert "First" in content
        assert "Second" in content
        assert "Third" in content


# ---------------------------------------------------------------------------
# run_audit journal_only mode
# ---------------------------------------------------------------------------

SAMPLE_OUTPUT = """\
---FINDING---
TITLE: SSRF in image proxy
SEVERITY: critical
CATEGORY: ssrf
LOCATION: src/proxy.py:18
PROBLEM: URL parameter forwarded to requests.get without allowlist.
WHY: Lets attackers fetch internal metadata endpoints.
SUGGESTED_FIX: Validate host against an allowlist.
EFFORT: small
"""


AUDIT_SKILL_DIR = (
    Path(__file__).parent.parent / "skills" / "core" / "audit"
)


class TestRunAuditJournalOnly:
    @patch("skills.core.audit.audit_runner._run_claude_audit")
    @patch("skills.core.audit.audit_runner.create_issues")
    def test_skips_create_issues(self, mock_create, mock_claude, tmp_path):
        mock_claude.return_value = SAMPLE_OUTPUT
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        success, summary = run_audit(
            project_path=str(tmp_path),
            project_name="demo",
            instance_dir=str(instance_dir),
            notify_fn=lambda *_: None,
            journal_only=True,
            skill_dir=AUDIT_SKILL_DIR,
        )

        assert success
        mock_create.assert_not_called()
        assert "no GitHub issues created" in summary or "private" in summary.lower()

    @patch("skills.core.audit.audit_runner._run_claude_audit")
    @patch("skills.core.audit.audit_runner.create_issues")
    def test_writes_journal_entry(self, mock_create, mock_claude, tmp_path):
        mock_claude.return_value = SAMPLE_OUTPUT
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        run_audit(
            project_path=str(tmp_path),
            project_name="demo",
            instance_dir=str(instance_dir),
            notify_fn=lambda *_: None,
            journal_only=True,
            skill_dir=AUDIT_SKILL_DIR,
        )

        journal_root = instance_dir / "journal"
        dated_dirs = list(journal_root.iterdir())
        assert len(dated_dirs) == 1
        journal_file = dated_dirs[0] / "demo.md"
        assert journal_file.exists()
        content = journal_file.read_text()
        assert "SSRF in image proxy" in content
        assert "src/proxy.py:18" in content

    @patch("skills.core.audit.audit_runner._run_claude_audit")
    @patch("skills.core.audit.audit_runner.create_issues")
    def test_saves_private_report_file(self, mock_create, mock_claude, tmp_path):
        mock_claude.return_value = SAMPLE_OUTPUT
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        run_audit(
            project_path=str(tmp_path),
            project_name="demo",
            instance_dir=str(instance_dir),
            notify_fn=lambda *_: None,
            journal_only=True,
            report_name="private_security_audit",
            skill_dir=AUDIT_SKILL_DIR,
        )

        report = (
            instance_dir / "memory" / "projects" / "demo"
            / "private_security_audit.md"
        )
        assert report.exists()

    @patch("skills.core.audit.audit_runner._run_claude_audit")
    @patch("skills.core.audit.audit_runner.create_issues")
    def test_default_mode_still_calls_create_issues(
        self, mock_create, mock_claude, tmp_path,
    ):
        mock_create.return_value = ["https://github.com/o/r/issues/1"]
        mock_claude.return_value = SAMPLE_OUTPUT
        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()

        run_audit(
            project_path=str(tmp_path),
            project_name="demo",
            instance_dir=str(instance_dir),
            notify_fn=lambda *_: None,
            skill_dir=AUDIT_SKILL_DIR,
        )
        mock_create.assert_called_once()


# ---------------------------------------------------------------------------
# private_security_audit_runner wrapper
# ---------------------------------------------------------------------------

class TestPrivateSecurityAuditRunner:
    @patch("skills.core.private_security_audit.private_security_audit_runner.run_audit")
    def test_forces_journal_only_and_disables_pvrs(self, mock_run, tmp_path):
        from skills.core.private_security_audit.private_security_audit_runner import (
            run_private_security_audit,
        )
        mock_run.return_value = (True, "done")

        run_private_security_audit(
            project_path=str(tmp_path),
            project_name="demo",
            instance_dir=str(tmp_path / "instance"),
            extra_context="auth",
            max_issues=3,
            notify_fn=lambda *_: None,
        )

        kwargs = mock_run.call_args.kwargs
        assert kwargs["journal_only"] is True
        assert kwargs["pvrs_mode"] == "false"
        assert kwargs["max_issues"] == 3
        assert kwargs["extra_context"] == "auth"
        assert kwargs["report_name"] == "private_security_audit"


# ---------------------------------------------------------------------------
# skill_dispatch wiring
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_canonical_command_resolves(self):
        from app.skill_dispatch import (
            _SKILL_RUNNERS,
            build_skill_command,
        )
        assert (
            _SKILL_RUNNERS["private_security_audit"]
            == "skills.core.private_security_audit.private_security_audit_runner"
        )
        cmd = build_skill_command(
            command="private_security_audit",
            args="focus on auth limit=3",
            project_name="demo",
            project_path="/tmp/demo",
            koan_root="/tmp/koan",
            instance_dir="/tmp/instance",
        )
        assert cmd is not None
        assert any("private_security_audit_runner" in tok for tok in cmd)
        assert "--max-issues" in cmd
        assert "3" in cmd

    def test_alias_resolves(self):
        from app.skill_dispatch import _SKILL_RUNNERS, _resolve_canonical
        assert _resolve_canonical("psecu") == "private_security_audit"
        assert _resolve_canonical("private_security") == "private_security_audit"
        # Aliases share the same runner module
        assert (
            _SKILL_RUNNERS["psecu"]
            == _SKILL_RUNNERS["private_security_audit"]
        )

    def test_alias_builds_command(self):
        from app.skill_dispatch import build_skill_command
        cmd = build_skill_command(
            command="psecu",
            args="",
            project_name="demo",
            project_path="/tmp/demo",
            koan_root="/tmp/koan",
            instance_dir="/tmp/instance",
        )
        assert cmd is not None
        assert any("private_security_audit_runner" in tok for tok in cmd)
