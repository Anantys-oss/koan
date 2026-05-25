"""Tests for PVRS-aware security audit routing.

Covers:
- github.py: check_pvrs_enabled(), security_advisory_report(), detect_ecosystem()
- audit_runner.py: PVRS routing in create_issues(), _should_use_pvrs()
- projects_config.py: get_project_security_config()
- security_audit_runner.py: _load_pvrs_config()
- Integration: mixed-severity findings with PVRS routing and fallback
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.github import check_pvrs_enabled, detect_ecosystem, security_advisory_report
from app.projects_config import get_project_security_config
from skills.core.audit.audit_runner import (
    AuditFinding,
    _build_advisory_description,
    _should_use_pvrs,
    _slugify_finding_title,
    _write_local_finding,
    create_issues,
)


# ---------------------------------------------------------------------------
# check_pvrs_enabled
# ---------------------------------------------------------------------------

class TestCheckPvrsEnabled:
    @patch("app.github.api")
    def test_returns_true_when_enabled(self, mock_api):
        mock_api.return_value = json.dumps({"enabled": True})
        assert check_pvrs_enabled("owner/repo") is True

    @patch("app.github.api")
    def test_returns_false_when_disabled(self, mock_api):
        mock_api.return_value = json.dumps({"enabled": False})
        assert check_pvrs_enabled("owner/repo") is False

    @patch("app.github.api", side_effect=RuntimeError("403 Forbidden"))
    def test_returns_false_on_api_error(self, mock_api):
        assert check_pvrs_enabled("owner/repo") is False

    @patch("app.github.api", return_value="not json")
    def test_returns_false_on_invalid_json(self, mock_api):
        assert check_pvrs_enabled("owner/repo") is False

    @patch("app.github.api", return_value=json.dumps({}))
    def test_returns_false_when_key_missing(self, mock_api):
        assert check_pvrs_enabled("owner/repo") is False


# ---------------------------------------------------------------------------
# security_advisory_report
# ---------------------------------------------------------------------------

class TestSecurityAdvisoryReport:
    @patch("app.leak_detector.scan_and_redact", side_effect=lambda x, **kw: x)
    @patch("app.github.api")
    def test_returns_advisory_url(self, mock_api, mock_redact):
        mock_api.return_value = json.dumps({
            "html_url": "https://github.com/o/r/security/advisories/GHSA-1234",
            "ghsa_id": "GHSA-1234",
        })
        url = security_advisory_report(
            summary="SQL injection",
            description="Found SQLi in auth.py",
            severity="critical",
            ecosystem="pip",
            package_name="myapp",
            repo="owner/repo",
        )
        assert url == "https://github.com/o/r/security/advisories/GHSA-1234"

        # Verify the API was called with POST and raw_body=True
        call_args = mock_api.call_args
        assert call_args[1]["method"] == "POST"
        assert call_args[1]["raw_body"] is True
        assert "security-advisories/reports" in call_args[0][0]

    @patch("app.leak_detector.scan_and_redact", side_effect=lambda x, **kw: x)
    @patch("app.github.api")
    def test_returns_ghsa_id_when_no_url(self, mock_api, mock_redact):
        mock_api.return_value = json.dumps({
            "ghsa_id": "GHSA-5678",
        })
        url = security_advisory_report(
            summary="XSS", description="found xss",
            severity="high", repo="owner/repo",
        )
        assert "GHSA-5678" in url

    @patch("app.leak_detector.scan_and_redact", side_effect=lambda x, **kw: x)
    @patch("app.github.api", side_effect=RuntimeError("422"))
    def test_raises_on_api_failure(self, mock_api, mock_redact):
        with pytest.raises(RuntimeError):
            security_advisory_report(
                summary="Bug", description="desc",
                severity="high", repo="owner/repo",
            )

    @patch("app.leak_detector.scan_and_redact", side_effect=lambda x, **kw: x)
    @patch("app.github.api")
    def test_payload_structure(self, mock_api, mock_redact):
        mock_api.return_value = json.dumps({"html_url": "https://example.com"})
        security_advisory_report(
            summary="Path traversal",
            description="Found path traversal in upload handler",
            severity="high",
            ecosystem="npm",
            package_name="my-pkg",
            repo="owner/repo",
        )
        # Verify the JSON payload sent via stdin
        call_kwargs = mock_api.call_args[1]
        payload = json.loads(call_kwargs["input_data"])
        assert payload["summary"] == "Path traversal"
        assert payload["severity"] == "high"
        assert payload["vulnerabilities"][0]["package"]["ecosystem"] == "npm"
        assert payload["vulnerabilities"][0]["package"]["name"] == "my-pkg"


# ---------------------------------------------------------------------------
# detect_ecosystem
# ---------------------------------------------------------------------------

class TestDetectEcosystem:
    def test_python_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        assert detect_ecosystem(str(tmp_path)) == "pip"

    def test_python_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        assert detect_ecosystem(str(tmp_path)) == "pip"

    def test_node_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        assert detect_ecosystem(str(tmp_path)) == "npm"

    def test_go_module(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example\n")
        assert detect_ecosystem(str(tmp_path)) == "go"

    def test_rust_cargo(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert detect_ecosystem(str(tmp_path)) == "cargo"

    def test_ruby_gemfile(self, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
        assert detect_ecosystem(str(tmp_path)) == "rubygems"

    def test_php_composer(self, tmp_path):
        (tmp_path / "composer.json").write_text("{}\n")
        assert detect_ecosystem(str(tmp_path)) == "composer"

    def test_java_maven(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>\n")
        assert detect_ecosystem(str(tmp_path)) == "maven"

    def test_unknown_project(self, tmp_path):
        (tmp_path / "README.md").write_text("hello\n")
        assert detect_ecosystem(str(tmp_path)) == "other"

    def test_python_preferred_over_node(self, tmp_path):
        """When both exist, Python is detected first (order matters)."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        (tmp_path / "package.json").write_text("{}\n")
        assert detect_ecosystem(str(tmp_path)) == "pip"


# ---------------------------------------------------------------------------
# get_project_security_config
# ---------------------------------------------------------------------------

class TestGetProjectSecurityConfig:
    def test_defaults_when_no_security_section(self):
        config = {"defaults": {}, "projects": {"app": {"path": "/a"}}}
        result = get_project_security_config(config, "app")
        assert result == {"pvrs": "auto", "pvrs_threshold": "high"}

    def test_reads_from_defaults(self):
        config = {
            "defaults": {"security": {"pvrs": "false", "pvrs_threshold": "medium"}},
            "projects": {"app": {"path": "/a"}},
        }
        result = get_project_security_config(config, "app")
        assert result["pvrs"] == "false"
        assert result["pvrs_threshold"] == "medium"

    def test_project_overrides_defaults(self):
        config = {
            "defaults": {"security": {"pvrs": "auto", "pvrs_threshold": "high"}},
            "projects": {
                "app": {
                    "path": "/a",
                    "security": {"pvrs": "true", "pvrs_threshold": "critical"},
                }
            },
        }
        result = get_project_security_config(config, "app")
        assert result["pvrs"] == "true"
        assert result["pvrs_threshold"] == "critical"

    def test_invalid_pvrs_value_falls_back_to_auto(self):
        config = {
            "defaults": {"security": {"pvrs": "bogus"}},
            "projects": {},
        }
        result = get_project_security_config(config, "app")
        assert result["pvrs"] == "auto"

    def test_invalid_threshold_falls_back_to_high(self):
        config = {
            "defaults": {"security": {"pvrs_threshold": "extreme"}},
            "projects": {},
        }
        result = get_project_security_config(config, "app")
        assert result["pvrs_threshold"] == "high"

    def test_security_not_dict_treated_as_empty(self):
        config = {
            "defaults": {"security": "not-a-dict"},
            "projects": {},
        }
        result = get_project_security_config(config, "app")
        assert result == {"pvrs": "auto", "pvrs_threshold": "high"}


# ---------------------------------------------------------------------------
# _should_use_pvrs
# ---------------------------------------------------------------------------

class TestShouldUsePvrs:
    def test_critical_with_high_threshold(self):
        assert _should_use_pvrs("critical", "high") is True

    def test_high_with_high_threshold(self):
        assert _should_use_pvrs("high", "high") is True

    def test_medium_with_high_threshold(self):
        assert _should_use_pvrs("medium", "high") is False

    def test_low_with_high_threshold(self):
        assert _should_use_pvrs("low", "high") is False

    def test_critical_with_critical_threshold(self):
        assert _should_use_pvrs("critical", "critical") is True

    def test_high_with_critical_threshold(self):
        assert _should_use_pvrs("high", "critical") is False

    def test_medium_with_medium_threshold(self):
        assert _should_use_pvrs("medium", "medium") is True

    def test_low_with_low_threshold(self):
        assert _should_use_pvrs("low", "low") is True

    def test_unknown_severity_returns_false(self):
        assert _should_use_pvrs("unknown", "high") is False


# ---------------------------------------------------------------------------
# _build_advisory_description
# ---------------------------------------------------------------------------

class TestBuildAdvisoryDescription:
    def test_includes_key_sections(self):
        finding = AuditFinding(
            title="SQLi in login",
            severity="critical",
            category="injection",
            location="auth.py:42-48",
            problem="SQL injection in login form",
            why="Allows authentication bypass",
            suggested_fix="Use parameterized queries",
        )
        desc = _build_advisory_description(finding)
        assert "## Problem" in desc
        assert "SQL injection in login form" in desc
        assert "## Why This Matters" in desc
        assert "## Suggested Fix" in desc
        assert "`auth.py:42-48`" in desc
        assert "injection" in desc


# ---------------------------------------------------------------------------
# create_issues — PVRS routing
# ---------------------------------------------------------------------------

class TestCreateIssuesPvrsRouting:
    """Test the routing logic in create_issues with PVRS support."""

    @pytest.fixture(autouse=True)
    def _stub_existing_issues(self):
        """PVRS routing tests don't care about dedup — stub the lookup empty."""
        with patch(
            "app.github.list_open_audit_issues", return_value=[],
        ) as m:
            yield m

    def _make_findings(self):
        """Create a mixed-severity set of findings."""
        return [
            AuditFinding(
                title="RCE via deserialization",
                severity="critical", location="api.py:10", problem="p1",
                why="w1", suggested_fix="s1", category="security",
            ),
            AuditFinding(
                title="Hardcoded API key",
                severity="high", location="config.py:5", problem="p2",
                why="w2", suggested_fix="s2", category="secrets",
            ),
            AuditFinding(
                title="Missing HSTS header",
                severity="medium", location="server.py:1", problem="p3",
                why="w3", suggested_fix="s3", category="config",
            ),
            AuditFinding(
                title="Verbose error messages",
                severity="low", location="app.py:20", problem="p4",
                why="w4", suggested_fix="s4", category="info",
            ),
        ]

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=True)
    @patch("app.github.detect_ecosystem", return_value="pip")
    @patch("app.github.security_advisory_report")
    @patch("app.github.issue_create")
    def test_routes_critical_high_to_pvrs(
        self, mock_issue, mock_pvrs, mock_eco, mock_check, mock_repo, tmp_path,
    ):
        mock_pvrs.return_value = "https://github.com/o/r/security/advisories/GHSA-1"
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"

        findings = self._make_findings()
        result = create_issues(
            findings, "/path/proj", pvrs_threshold="high",
            instance_dir=str(tmp_path), project_name="proj",
        )

        # critical + high → PVRS (2 calls)
        assert mock_pvrs.call_count == 2
        # medium + low → public issues (2 calls)
        assert mock_issue.call_count == 2
        assert len(result.urls) == 4
        # Local files written for high+ findings
        assert len(result.local_files) == 2

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=False)
    @patch("app.github.issue_create")
    def test_high_get_local_files_when_pvrs_disabled(
        self, mock_issue, mock_check, mock_repo, tmp_path,
    ):
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"
        findings = self._make_findings()
        result = create_issues(
            findings, "/path/proj", instance_dir=str(tmp_path),
            project_name="proj",
        )

        # medium + low → public issues (2 calls), critical + high → local files
        assert mock_issue.call_count == 2
        assert len(result.local_files) == 2
        # Local files exist on disk
        for finding, path in result.local_files:
            assert path.exists()
            content = path.read_text()
            assert finding.title in content

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.issue_create")
    def test_pvrs_mode_false_skips_detection(self, mock_issue, mock_repo, tmp_path):
        """When pvrs_mode='false', PVRS detection is never called."""
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"
        findings = self._make_findings()

        # Should NOT call check_pvrs_enabled at all
        with patch("app.github.check_pvrs_enabled") as mock_check:
            result = create_issues(
                findings, "/path/proj", pvrs_mode="false",
                instance_dir=str(tmp_path), project_name="proj",
            )
            mock_check.assert_not_called()

        # medium + low → public issues, critical + high → local files
        assert mock_issue.call_count == 2
        assert len(result.local_files) == 2

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=True)
    @patch("app.github.detect_ecosystem", return_value="pip")
    @patch("app.github.security_advisory_report")
    @patch("app.github.issue_create")
    def test_pvrs_mode_true_skips_detection(
        self, mock_issue, mock_pvrs, mock_eco, mock_check, mock_repo, tmp_path,
    ):
        """When pvrs_mode='true', check_pvrs_enabled is NOT called."""
        mock_pvrs.return_value = "https://github.com/advisory/1"
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"

        findings = self._make_findings()
        create_issues(
            findings, "/path/proj", pvrs_mode="true", pvrs_threshold="high",
            instance_dir=str(tmp_path), project_name="proj",
        )

        # check_pvrs_enabled should NOT be called when pvrs_mode is "true"
        mock_check.assert_not_called()
        # But PVRS reports should still be submitted for critical+high
        assert mock_pvrs.call_count == 2

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=True)
    @patch("app.github.detect_ecosystem", return_value="pip")
    @patch("app.github.security_advisory_report",
           side_effect=RuntimeError("403 Forbidden"))
    @patch("app.github.issue_create")
    def test_pvrs_failure_writes_local_file(
        self, mock_issue, mock_pvrs, mock_eco, mock_check, mock_repo, tmp_path,
    ):
        """When PVRS submission fails, write local file (no public issue)."""
        findings = [self._make_findings()[0]]  # critical only

        notify = MagicMock()
        result = create_issues(
            findings, "/path/proj", notify_fn=notify,
            pvrs_threshold="high", instance_dir=str(tmp_path),
            project_name="proj",
        )

        # PVRS was attempted but no public issue created
        assert mock_pvrs.call_count == 1
        assert mock_issue.call_count == 0
        assert len(result.urls) == 0
        # Local file was written with full details
        assert len(result.local_files) == 1
        finding, path = result.local_files[0]
        assert path.exists()
        content = path.read_text()
        assert "RCE via deserialization" in content
        assert "failed" in content
        # Notification includes file path and fix suggestion
        all_calls = [c.args[0] for c in notify.call_args_list]
        assert any("/fix" in c for c in all_calls)

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=True)
    @patch("app.github.detect_ecosystem", return_value="pip")
    @patch("app.github.security_advisory_report")
    @patch("app.github.issue_create")
    def test_threshold_critical_only(
        self, mock_issue, mock_pvrs, mock_eco, mock_check, mock_repo, tmp_path,
    ):
        """With threshold='critical', only critical goes to PVRS."""
        mock_pvrs.return_value = "https://github.com/advisory/1"
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"

        findings = self._make_findings()
        create_issues(
            findings, "/path/proj", pvrs_threshold="critical",
            instance_dir=str(tmp_path), project_name="proj",
        )

        assert mock_pvrs.call_count == 1  # only critical
        # high + medium + low → public issues (critical → local file only)
        assert mock_issue.call_count == 3

    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=True)
    @patch("app.github.detect_ecosystem", return_value="pip")
    @patch("app.github.security_advisory_report")
    @patch("app.github.issue_create")
    def test_notify_fn_reports_pvrs_and_local_file(
        self, mock_issue, mock_pvrs, mock_eco, mock_check, mock_repo, tmp_path,
    ):
        """notify_fn should indicate PVRS status and local file path."""
        mock_pvrs.return_value = "https://github.com/advisory/1"
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"

        notify = MagicMock()
        findings = self._make_findings()[:2]  # critical + high
        create_issues(
            findings, "/path/proj", notify_fn=notify,
            pvrs_threshold="high", instance_dir=str(tmp_path),
            project_name="proj",
        )

        all_calls = [c.args[0] for c in notify.call_args_list]
        # Should have PVRS-enabled announcement
        assert any("PVRS enabled" in c for c in all_calls)
        # Should have local file path and fix suggestion
        assert any("security/proj/" in c for c in all_calls)
        assert any("/fix" in c for c in all_calls)


# ---------------------------------------------------------------------------
# Integration: _load_pvrs_config
# ---------------------------------------------------------------------------

class TestLoadPvrsConfig:
    def test_returns_defaults_when_no_koan_root(self, monkeypatch):
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        from skills.core.security_audit.security_audit_runner import _load_pvrs_config
        result = _load_pvrs_config("myapp")
        assert result == {"pvrs": "auto", "pvrs_threshold": "high"}

    def test_reads_from_projects_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
        yaml_content = (
            "defaults:\n"
            "  security:\n"
            "    pvrs: 'true'\n"
            "    pvrs_threshold: critical\n"
            "projects:\n"
            "  myapp:\n"
            "    path: /tmp/myapp\n"
        )
        (tmp_path / "projects.yaml").write_text(yaml_content)
        from skills.core.security_audit.security_audit_runner import _load_pvrs_config
        result = _load_pvrs_config("myapp")
        assert result["pvrs"] == "true"
        assert result["pvrs_threshold"] == "critical"


# ---------------------------------------------------------------------------
# Integration: full pipeline with PVRS routing
# ---------------------------------------------------------------------------

class TestPvrsIntegration:
    """End-to-end test: mixed findings → correct routing per severity."""

    @patch("skills.core.audit.audit_runner.build_audit_prompt", return_value="prompt")
    @patch("skills.core.audit.audit_runner._run_claude_audit")
    @patch("app.github.resolve_target_repo", return_value="upstream/repo")
    @patch("app.github.check_pvrs_enabled", return_value=True)
    @patch("app.github.detect_ecosystem", return_value="pip")
    @patch("app.github.security_advisory_report")
    @patch("app.github.issue_create")
    def test_run_audit_with_pvrs(
        self, mock_issue, mock_pvrs, mock_eco, mock_check, mock_repo,
        mock_claude, mock_prompt, tmp_path,
    ):
        # Claude output with mixed-severity findings
        mock_claude.return_value = (
            "---FINDING---\n"
            "TITLE: SQL injection in login\n"
            "SEVERITY: critical\n"
            "CATEGORY: injection\n"
            "LOCATION: auth.py:42\n"
            "PROBLEM: Direct string concatenation in SQL query\n"
            "WHY: Allows authentication bypass\n"
            "SUGGESTED_FIX: Use parameterized queries\n"
            "EFFORT: small\n"
            "---FINDING---\n"
            "TITLE: Missing HSTS\n"
            "SEVERITY: medium\n"
            "CATEGORY: config\n"
            "LOCATION: server.py:1\n"
            "PROBLEM: No HSTS header\n"
            "WHY: Downgrade attacks possible\n"
            "SUGGESTED_FIX: Add HSTS header\n"
            "EFFORT: small\n"
        )
        mock_pvrs.return_value = "https://github.com/o/r/security/advisories/GHSA-1"
        mock_issue.return_value = "https://github.com/o/r/issues/1\n"

        from skills.core.audit.audit_runner import run_audit

        instance_dir = tmp_path / "instance"
        instance_dir.mkdir()
        notify = MagicMock()

        success, summary = run_audit(
            project_path="/path/proj",
            project_name="proj",
            instance_dir=str(instance_dir),
            notify_fn=notify,
            pvrs_mode="auto",
            pvrs_threshold="high",
        )

        assert success
        assert "2 findings" in summary
        # critical → PVRS + local file, medium → public issue
        assert mock_pvrs.call_count == 1
        assert mock_issue.call_count == 1

        # Verify local security file written for critical finding
        security_dir = instance_dir / "security" / "proj"
        security_files = list(security_dir.glob("*.critical.*.md"))
        assert len(security_files) == 1
        content = security_files[0].read_text()
        assert "SQL injection in login" in content
        assert "submitted" in content


# ---------------------------------------------------------------------------
# _slugify_finding_title
# ---------------------------------------------------------------------------

class TestSlugifyFindingTitle:
    def test_basic(self):
        assert _slugify_finding_title("SQL Injection in Auth") == "sql-injection-in-auth"

    def test_special_chars(self):
        assert _slugify_finding_title("XSS via <script> tag") == "xss-via-script-tag"

    def test_truncates_long_titles(self):
        long_title = "a" * 100
        assert len(_slugify_finding_title(long_title)) == 60

    def test_strips_leading_trailing_hyphens(self):
        assert _slugify_finding_title("---hello---") == "hello"

    def test_empty_string(self):
        assert _slugify_finding_title("") == ""

    def test_unicode(self):
        assert _slugify_finding_title("Vulnérabilité d'injection") == "vuln-rabilit-d-injection"


# ---------------------------------------------------------------------------
# _write_local_finding
# ---------------------------------------------------------------------------

class TestWriteLocalFinding:
    def _make_finding(self):
        return AuditFinding(
            title="SQL injection in login",
            severity="critical",
            category="injection",
            location="auth.py:42-48",
            problem="Direct string concatenation in SQL query",
            why="Allows authentication bypass",
            suggested_fix="Use parameterized queries",
        )

    def test_creates_file_at_expected_path(self, tmp_path):
        finding = self._make_finding()
        path = _write_local_finding(finding, "myapp", str(tmp_path))

        assert path.exists()
        assert path.parent.name == "myapp"
        assert path.parent.parent.name == "security"
        assert "critical" in path.name
        assert "sql-injection-in-login" in path.name
        assert path.name.endswith(".md")

    def test_file_contains_full_details(self, tmp_path):
        finding = self._make_finding()
        path = _write_local_finding(finding, "myapp", str(tmp_path))

        content = path.read_text()
        assert "# SQL injection in login" in content
        assert "critical" in content
        assert "injection" in content
        assert "`auth.py:42-48`" in content
        assert "Direct string concatenation" in content
        assert "Allows authentication bypass" in content
        assert "Use parameterized queries" in content

    def test_pvrs_status_submitted(self, tmp_path):
        finding = self._make_finding()
        path = _write_local_finding(
            finding, "myapp", str(tmp_path),
            pvrs_status="submitted",
            advisory_url="https://github.com/o/r/security/advisories/GHSA-1",
        )
        content = path.read_text()
        assert "submitted" in content
        assert "GHSA-1" in content

    def test_pvrs_status_failed(self, tmp_path):
        finding = self._make_finding()
        path = _write_local_finding(
            finding, "myapp", str(tmp_path), pvrs_status="failed",
        )
        content = path.read_text()
        assert "failed" in content

    def test_pvrs_status_disabled(self, tmp_path):
        finding = self._make_finding()
        path = _write_local_finding(
            finding, "myapp", str(tmp_path), pvrs_status="disabled",
        )
        content = path.read_text()
        assert "disabled" in content

    def test_creates_directory_structure(self, tmp_path):
        finding = self._make_finding()
        _write_local_finding(finding, "new-project", str(tmp_path))

        assert (tmp_path / "security" / "new-project").is_dir()

    def test_idempotent_overwrite(self, tmp_path):
        finding = self._make_finding()
        path1 = _write_local_finding(finding, "myapp", str(tmp_path))
        path2 = _write_local_finding(finding, "myapp", str(tmp_path))

        assert path1 == path2
        assert path1.exists()
