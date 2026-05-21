"""Tests for security_learnings.py — security intelligence layer for /security_audit."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instance(tmp_path: Path) -> str:
    instance = tmp_path / "instance"
    instance.mkdir()
    return str(instance)


# ---------------------------------------------------------------------------
# Imports under KOAN_ROOT
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def koan_root_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path / "koan_root"))


# ---------------------------------------------------------------------------
# SecurityLearning dataclass
# ---------------------------------------------------------------------------

class TestSecurityLearningDataclass:
    def test_fields_set_correctly(self):
        from skills.core.audit.security_learnings import SecurityLearning
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="SQL injection via string concat",
            source="audit-session",
            scope="local",
        )
        assert sl.category == "detection_pattern"
        assert sl.trust_level == "ephemeral"
        assert sl.content == "SQL injection via string concat"
        assert sl.source == "audit-session"
        assert sl.scope == "local"
        assert sl.created_at  # auto-filled

    def test_default_scope_is_local(self):
        from skills.core.audit.security_learnings import SecurityLearning
        sl = SecurityLearning(
            category="framework_weakness",
            trust_level="verified",
            content="Flask debug mode exposed",
            source="audit-session",
        )
        assert sl.scope == "local"


# ---------------------------------------------------------------------------
# write_security_learning / read_security_learnings round-trip
# ---------------------------------------------------------------------------

class TestWriteReadRoundTrip:
    def test_local_write_read(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            read_security_learnings,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Parameterized query missing in login handler",
            source="audit-session",
            scope="local",
        )
        write_security_learning(instance, "my-toolkit", sl)
        content = read_security_learnings(instance, "my-toolkit")
        assert "detection_pattern" in content
        assert "ephemeral" in content
        assert "Parameterized query missing in login handler" in content

    def test_global_write_excluded_from_local_only(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            read_security_learnings,
        )
        instance = _make_instance(tmp_path)
        global_sl = SecurityLearning(
            category="remediation_knowledge",
            trust_level="trusted",
            content="Use parameterized queries to prevent SQL injection",
            source="audit-session",
            scope="global",
        )
        local_sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Project-specific auth bypass in login.py",
            source="audit-session",
            scope="local",
        )
        write_security_learning(instance, "my-toolkit", global_sl)
        write_security_learning(instance, "my-toolkit", local_sl)

        # global_only=True must not return local
        global_content = read_security_learnings(instance, "my-toolkit", global_only=True)
        assert "Use parameterized queries" in global_content
        assert "Project-specific auth bypass" not in global_content

        # combined read includes both
        combined = read_security_learnings(instance, "my-toolkit")
        assert "Use parameterized queries" in combined
        assert "Project-specific auth bypass" in combined

    def test_directory_auto_created(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            _project_security_path,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Test auto-dir creation",
            source="audit-session",
        )
        write_security_learning(instance, "new-project", sl)
        assert _project_security_path(instance, "new-project").exists()

    def test_global_directory_auto_created(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            _global_learnings_path,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="trusted",
            content="Global auto-dir creation test",
            source="audit-session",
            scope="global",
        )
        write_security_learning(instance, "any-project", sl)
        assert _global_learnings_path(instance).exists()

    def test_exact_string_dedup(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            _project_security_path,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Duplicate entry check",
            source="audit-session",
        )
        write_security_learning(instance, "my-toolkit", sl)
        write_security_learning(instance, "my-toolkit", sl)
        path = _project_security_path(instance, "my-toolkit")
        lines = [l for l in path.read_text().splitlines() if "Duplicate entry check" in l]
        assert len(lines) == 1

    def test_empty_instance_returns_empty_string(self, tmp_path):
        from skills.core.audit.security_learnings import read_security_learnings
        instance = _make_instance(tmp_path)
        result = read_security_learnings(instance, "my-toolkit")
        assert result == ""


# ---------------------------------------------------------------------------
# Trust escalation
# ---------------------------------------------------------------------------

class TestTrustEscalation:
    def test_ephemeral_stays_after_one_session(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            escalate_trust,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Single session learning",
            source="audit-session",
        )
        result = escalate_trust(instance, "proj-a", [sl])
        assert result[0].trust_level == "ephemeral"

    def test_escalates_to_trusted_after_two_distinct_projects(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            escalate_trust,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Repeated learning across projects",
            source="audit-session",
        )
        # First session (proj-a)
        escalate_trust(instance, "proj-a", [sl])
        # Second session (proj-b) — 2 sessions + 2 projects → trusted
        sl2 = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Repeated learning across projects",
            source="audit-session",
        )
        result = escalate_trust(instance, "proj-b", [sl2])
        assert result[0].trust_level == "trusted"

    def test_escalates_same_project_recurrence(self, tmp_path):
        """Same-project repeated sessions must escalate ephemeral→verified."""
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            escalate_trust,
        )
        instance = _make_instance(tmp_path)
        content = "Same project repeated learning"
        sl1 = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content=content,
            source="audit-session",
        )
        escalate_trust(instance, "proj-a", [sl1])
        sl2 = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content=content,
            source="audit-session",
        )
        result = escalate_trust(instance, "proj-a", [sl2])
        assert result[0].trust_level == "verified"

    def test_trust_tracker_persists_across_calls(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            escalate_trust,
            _read_trust_tracker,
        )
        instance = _make_instance(tmp_path)
        content = "Persistent trust test"
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content=content,
            source="audit-session",
        )
        escalate_trust(instance, "proj-a", [sl])
        tracker = _read_trust_tracker(instance)
        assert "session_counts" in tracker

    def test_tracker_corruption_handled_gracefully(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            escalate_trust,
            _trust_tracker_path,
        )
        instance = _make_instance(tmp_path)
        tracker_path = _trust_tracker_path(instance)
        tracker_path.parent.mkdir(parents=True, exist_ok=True)
        tracker_path.write_text("not valid json {{{{")
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="After corruption",
            source="audit-session",
        )
        # Should not raise
        result = escalate_trust(instance, "proj-a", [sl])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# build_security_memory_block injection cap
# ---------------------------------------------------------------------------

class TestBuildSecurityMemoryBlock:
    def test_returns_empty_when_no_learnings(self, tmp_path):
        from skills.core.audit.security_learnings import build_security_memory_block
        instance = _make_instance(tmp_path)
        result = build_security_memory_block(instance, "my-toolkit")
        assert result == ""

    def test_ephemeral_excluded_from_block(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            build_security_memory_block,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="ephemeral",
            content="Ephemeral should be excluded",
            source="audit-session",
        )
        write_security_learning(instance, "my-toolkit", sl)
        result = build_security_memory_block(instance, "my-toolkit")
        assert result == ""

    def test_verified_included_in_block(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            build_security_memory_block,
        )
        instance = _make_instance(tmp_path)
        sl = SecurityLearning(
            category="detection_pattern",
            trust_level="verified",
            content="Verified learning should appear",
            source="audit-session",
        )
        write_security_learning(instance, "my-toolkit", sl)
        result = build_security_memory_block(instance, "my-toolkit")
        assert "## Security Intelligence" in result
        assert "Verified learning should appear" in result

    def test_injection_capped_at_max_lines(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            build_security_memory_block,
            MAX_INJECTION_LINES,
        )
        instance = _make_instance(tmp_path)
        # Write more than MAX_INJECTION_LINES verified entries
        for i in range(MAX_INJECTION_LINES + 50):
            sl = SecurityLearning(
                category="detection_pattern",
                trust_level="verified",
                content=f"Learning number {i} about security patterns in code",
                source="audit-session",
            )
            write_security_learning(instance, "my-toolkit", sl)
        result = build_security_memory_block(instance, "my-toolkit")
        content_lines = [
            l for l in result.splitlines()
            if l.strip() and not l.startswith("#")
        ]
        assert len(content_lines) <= MAX_INJECTION_LINES

    def test_trusted_sorted_before_verified(self, tmp_path):
        from skills.core.audit.security_learnings import (
            SecurityLearning,
            write_security_learning,
            build_security_memory_block,
        )
        instance = _make_instance(tmp_path)
        verified_sl = SecurityLearning(
            category="detection_pattern",
            trust_level="verified",
            content="Verified entry",
            source="audit-session",
        )
        trusted_sl = SecurityLearning(
            category="detection_pattern",
            trust_level="trusted",
            content="Trusted entry",
            source="audit-session",
        )
        write_security_learning(instance, "my-toolkit", verified_sl)
        write_security_learning(instance, "my-toolkit", trusted_sl)
        result = build_security_memory_block(instance, "my-toolkit")
        trusted_pos = result.find("Trusted entry")
        verified_pos = result.find("Verified entry")
        assert trusted_pos < verified_pos


# ---------------------------------------------------------------------------
# _parse_extraction_output
# ---------------------------------------------------------------------------

class TestParseExtractionOutput:
    def test_parse_single_block(self):
        from skills.core.audit.security_learnings import _parse_extraction_output
        raw = """---LEARNING---
CATEGORY: detection_pattern
TRUST: ephemeral
SCOPE: local
CONTENT: Missing input validation on user-supplied file paths
SOURCE: audit-session"""
        entries = _parse_extraction_output(raw)
        assert len(entries) == 1
        assert entries[0].category == "detection_pattern"
        assert entries[0].trust_level == "ephemeral"
        assert entries[0].scope == "local"
        assert "file paths" in entries[0].content

    def test_parse_multiple_blocks(self):
        from skills.core.audit.security_learnings import _parse_extraction_output
        raw = """---LEARNING---
CATEGORY: remediation_knowledge
TRUST: ephemeral
SCOPE: global
CONTENT: Always use parameterized queries
SOURCE: audit-session
---LEARNING---
CATEGORY: framework_weakness
TRUST: ephemeral
SCOPE: local
CONTENT: Flask debug mode active in dev config
SOURCE: audit-session"""
        entries = _parse_extraction_output(raw)
        assert len(entries) == 2
        assert entries[0].category == "remediation_knowledge"
        assert entries[1].category == "framework_weakness"

    def test_empty_output_returns_empty_list(self):
        from skills.core.audit.security_learnings import _parse_extraction_output
        assert _parse_extraction_output("") == []
        assert _parse_extraction_output("   ") == []

    def test_invalid_category_defaults_to_detection_pattern(self):
        from skills.core.audit.security_learnings import _parse_extraction_output
        raw = """---LEARNING---
CATEGORY: not_a_real_category
TRUST: ephemeral
SCOPE: local
CONTENT: Some content
SOURCE: audit-session"""
        entries = _parse_extraction_output(raw)
        assert entries[0].category == "detection_pattern"

    def test_block_without_content_skipped(self):
        from skills.core.audit.security_learnings import _parse_extraction_output
        raw = """---LEARNING---
CATEGORY: detection_pattern
TRUST: ephemeral
SCOPE: local
SOURCE: audit-session"""
        entries = _parse_extraction_output(raw)
        assert entries == []


# ---------------------------------------------------------------------------
# extract_security_learnings — no findings guard
# ---------------------------------------------------------------------------

class TestExtractSecurityLearnings:
    def test_empty_audit_output_returns_empty(self, tmp_path):
        from skills.core.audit.security_learnings import extract_security_learnings
        instance = _make_instance(tmp_path)
        result = extract_security_learnings("", "my-toolkit", instance, str(tmp_path))
        assert result == []

    def test_whitespace_only_output_returns_empty(self, tmp_path):
        from skills.core.audit.security_learnings import extract_security_learnings
        instance = _make_instance(tmp_path)
        result = extract_security_learnings("   \n  ", "my-toolkit", instance, str(tmp_path))
        assert result == []

    def test_cli_failure_returns_empty(self, tmp_path):
        from skills.core.audit.security_learnings import extract_security_learnings
        instance = _make_instance(tmp_path)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "rate limit exceeded"
        mock_result.stdout = ""

        with patch("app.cli_exec.run_cli_with_retry", return_value=mock_result):
            result = extract_security_learnings(
                "Some audit output with findings", "my-toolkit", instance, str(tmp_path)
            )
        assert result == []

    def test_cli_exception_returns_empty(self, tmp_path):
        from skills.core.audit.security_learnings import extract_security_learnings
        instance = _make_instance(tmp_path)

        with patch("app.cli_exec.run_cli_with_retry", side_effect=RuntimeError("quota exhausted")):
            result = extract_security_learnings(
                "Some audit output", "my-toolkit", instance, str(tmp_path)
            )
        assert result == []

    def test_successful_extraction_writes_file(self, tmp_path):
        from skills.core.audit.security_learnings import (
            extract_security_learnings,
            _project_security_path,
        )
        instance = _make_instance(tmp_path)

        cli_output = (
            "---LEARNING---\n"
            "CATEGORY: detection_pattern\n"
            "TRUST: ephemeral\n"
            "SCOPE: local\n"
            "CONTENT: Missing CSRF token on form submissions\n"
            "SOURCE: audit-session\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = cli_output
        mock_result.stderr = ""

        with patch("app.cli_exec.run_cli_with_retry", return_value=mock_result):
            result = extract_security_learnings(
                "Audit found CSRF issue in forms", "my-toolkit", instance, str(tmp_path)
            )

        assert len(result) == 1
        path = _project_security_path(instance, "my-toolkit")
        assert path.exists()
        assert "CSRF token" in path.read_text()


# ---------------------------------------------------------------------------
# Integration: run_audit produces security_learnings.md
# ---------------------------------------------------------------------------

class TestRunAuditIntegration:
    def test_security_learnings_file_exists_after_audit(self, tmp_path, monkeypatch):
        """After run_audit completes, security_learnings.md exists (or extraction ran)."""
        import importlib
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        from skills.core.audit.audit_runner import run_audit

        instance_dir = str(tmp_path / "instance")
        Path(instance_dir).mkdir()

        # Resolve skill_dir so audit.md is found in the skill's prompts/ dir
        skill_dir = Path(importlib.util.find_spec("skills.core.audit.audit_runner").origin).parent

        canned_output = (
            "---FINDING---\n"
            "TITLE: robustness: Missing input validation\n"
            "SEVERITY: high\n"
            "CATEGORY: robustness\n"
            "LOCATION: src/app.py:10-20\n"
            "PROBLEM: No validation on user input.\n"
            "WHY: Could lead to injection.\n"
            "SUGGESTED_FIX: Add validation.\n"
            "EFFORT: small\n"
        )

        def _fake_run_audit_cli(prompt, project_path):
            return canned_output

        def _fake_create_issues(findings, project_path, notify_fn=None, pvrs_mode="auto", pvrs_threshold="high"):
            from skills.core.audit.audit_runner import IssueCreationResult
            return IssueCreationResult(
                created=0, reused=0,
                urls=["https://github.com/test/repo/issues/1"] * len(findings),
            )

        extract_called = []

        def _fake_extract(audit_output, project_name, instance_dir_, project_path):
            extract_called.append(True)
            return []

        with (
            patch("skills.core.audit.audit_runner._run_claude_audit", side_effect=_fake_run_audit_cli),
            patch("skills.core.audit.audit_runner.create_issues", side_effect=_fake_create_issues),
            patch("skills.core.audit.security_learnings.extract_security_learnings", side_effect=_fake_extract),
        ):
            success, summary = run_audit(
                project_path=str(tmp_path),
                project_name="my-toolkit",
                instance_dir=instance_dir,
                skill_dir=skill_dir,
                notify_fn=lambda *a, **kw: None,
            )

        assert success
        assert extract_called  # extraction was called


# ---------------------------------------------------------------------------
# compact_security_learnings in MemoryManager
# ---------------------------------------------------------------------------

class TestCompactSecurityLearnings:
    def test_returns_skipped_when_no_file(self, tmp_path):
        from app.memory_manager import MemoryManager
        mgr = MemoryManager(str(tmp_path / "instance"))
        result = mgr.compact_security_learnings("nonexistent-project")
        assert result["skipped"] is True
        assert result["original_lines"] == 0

    def test_returns_skipped_when_below_threshold(self, tmp_path):
        from app.memory_manager import MemoryManager
        instance = tmp_path / "instance"
        sec_path = instance / "memory" / "projects" / "my-toolkit" / "security_learnings.md"
        sec_path.parent.mkdir(parents=True)
        # Write a few lines — well below the default max_lines=100
        sec_path.write_text("# Security Intelligence\n\n- [detection_pattern][verified] Short file\n")

        mgr = MemoryManager(str(instance))
        result = mgr.compact_security_learnings("my-toolkit", max_lines=100)
        assert result["skipped"] is True

    def test_cli_failure_falls_back_to_truncation(self, tmp_path):
        from app.memory_manager import MemoryManager
        instance = tmp_path / "instance"
        sec_path = instance / "memory" / "projects" / "my-toolkit" / "security_learnings.md"
        sec_path.parent.mkdir(parents=True)
        # Write 200 content lines to exceed max_lines=50
        lines = ["# Security Intelligence", ""]
        for i in range(200):
            lines.append(f"- [detection_pattern][verified] Entry {i}  <!-- source:audit-session created:2024-01-01 scope:local -->")
        sec_path.write_text("\n".join(lines))

        mgr = MemoryManager(str(instance))
        with patch.object(mgr, "_run_security_compaction_cli", side_effect=RuntimeError("quota")):
            result = mgr.compact_security_learnings("my-toolkit", max_lines=50)

        assert result["skipped"] is False
        assert result.get("fallback") is True
