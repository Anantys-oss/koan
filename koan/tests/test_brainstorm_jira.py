"""Brainstorm runner tests for the Jira issue-tracker path (and GitHub parity).

These exercise `run_brainstorm` with the Claude decomposition and the tracker
service layer mocked, so no network or LLM is touched.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from skills.core.brainstorm import brainstorm_runner


REQUIRED_SECTIONS = brainstorm_runner.REQUIRED_ISSUE_SECTIONS


def _issue_body(extra: str = "") -> str:
    """A body containing every required section (+ optional extra text)."""
    lines = []
    for header in REQUIRED_SECTIONS:
        lines.append(header)
        lines.append("content")
        lines.append("")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _decomposition(cross_ref: bool = False) -> str:
    """Two-issue decomposition; issue 1 optionally references SUB-2."""
    extra1 = "See SUB-2 for the dependency." if cross_ref else ""
    return json.dumps({
        "master_summary": "Master summary text",
        "issues": [
            {"title": "First issue", "body": _issue_body(extra1)},
            {"title": "Second issue", "body": _issue_body()},
        ],
    })


def _run(provider, create_urls, decomposition=None, link_side_effect=None,
         update_side_effect=None):
    """Run run_brainstorm with all tracker seams mocked.

    Returns a dict of the mocks so tests can assert on calls.
    """
    decomposition = decomposition or _decomposition()
    create_issue = MagicMock(side_effect=list(create_urls))
    update_issue = MagicMock(
        side_effect=update_side_effect, return_value=True,
    ) if update_side_effect is None else MagicMock(side_effect=update_side_effect)
    link_issues = MagicMock(
        side_effect=link_side_effect, return_value=True,
    ) if link_side_effect is None else MagicMock(side_effect=link_side_effect)

    client = SimpleNamespace(repo=None)

    with (
        patch.object(brainstorm_runner, "project_name_for_path", return_value="proj"),
        patch.object(brainstorm_runner, "tracker_is_configured", return_value=True),
        patch.object(brainstorm_runner, "tracker_supports_labels",
                     return_value=(provider == "github")),
        patch.object(brainstorm_runner, "client_for_project", return_value=client),
        patch.object(brainstorm_runner, "create_issue", create_issue),
        patch.object(brainstorm_runner, "_build_decompose_prompt", return_value="P"),
        patch.object(brainstorm_runner, "_call_claude_with_prompt",
                     return_value=decomposition),
        patch.object(brainstorm_runner, "_ensure_label"),
        patch.object(brainstorm_runner, "update_issue", update_issue),
        patch.object(brainstorm_runner, "link_issues", link_issues),
    ):
        success, summary = brainstorm_runner.run_brainstorm(
            project_path="/tmp/proj",
            topic="Improve caching",
            tag="caching",
            notify_fn=lambda *_a, **_k: None,
        )

    return {
        "success": success,
        "summary": summary,
        "create_issue": create_issue,
        "update_issue": update_issue,
        "link_issues": link_issues,
    }


class TestJiraCreateRouting:
    def test_all_issues_created_via_service_layer(self):
        urls = [
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
            "https://test/browse/PROJ-100",  # master
        ]
        res = _run("jira", urls)
        assert res["success"] is True
        # 2 sub-issues + 1 master
        assert res["create_issue"].call_count == 3
        # bodies passed through to create_issue unchanged (rich rendering
        # happens at the transport layer, verified elsewhere)
        first_body = res["create_issue"].call_args_list[0].args[3]
        assert "## Why This Matters" in first_body

    def test_summary_reports_master_url(self):
        urls = [
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
            "https://test/browse/PROJ-100",
        ]
        res = _run("jira", urls)
        assert "PROJ-100" in res["summary"]


class TestSubReferenceResolution:
    def test_jira_sub_refs_become_real_keys(self):
        urls = [
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
            "https://test/browse/PROJ-100",
        ]
        res = _run("jira", urls, decomposition=_decomposition(cross_ref=True))
        assert res["success"] is True
        # issue 1 (PROJ-1) referenced SUB-2 → must be updated to PROJ-2
        calls = {c.args[0]: c.args[1] for c in res["update_issue"].call_args_list}
        assert "https://test/browse/PROJ-1" in calls
        body = calls["https://test/browse/PROJ-1"]
        assert "PROJ-2" in body
        assert "SUB-2" not in body

    def test_github_sub_refs_become_hash_numbers(self):
        urls = [
            "https://github.com/o/r/issues/1",
            "https://github.com/o/r/issues/2",
            "https://github.com/o/r/issues/100",
        ]
        res = _run("github", urls, decomposition=_decomposition(cross_ref=True))
        assert res["success"] is True
        calls = {c.args[0]: c.args[1] for c in res["update_issue"].call_args_list}
        body = calls["https://github.com/o/r/issues/1"]
        assert "#2" in body
        assert "SUB-2" not in body

    def test_update_failure_is_non_fatal(self):
        urls = [
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
            "https://test/browse/PROJ-100",
        ]
        res = _run(
            "jira", urls,
            decomposition=_decomposition(cross_ref=True),
            update_side_effect=RuntimeError("boom"),
        )
        # run still succeeds despite the update raising
        assert res["success"] is True


class TestMasterLinking:
    def test_jira_links_master_to_each_sub(self):
        urls = [
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
            "https://test/browse/PROJ-100",  # master
        ]
        res = _run("jira", urls)
        # one link per created sub-issue (2), master → sub
        assert res["link_issues"].call_count == 2
        parents = {c.args[0] for c in res["link_issues"].call_args_list}
        children = {c.args[1] for c in res["link_issues"].call_args_list}
        assert parents == {"https://test/browse/PROJ-100"}
        assert children == {
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
        }

    def test_github_run_succeeds_with_linking_step(self):
        # The runner invokes the neutral link_issues service uniformly; on
        # GitHub the *backend* is a no-op (verified in the client tests), so no
        # native links are created and the run is unaffected.
        urls = [
            "https://github.com/o/r/issues/1",
            "https://github.com/o/r/issues/2",
            "https://github.com/o/r/issues/100",
        ]
        res = _run("github", urls)
        assert res["success"] is True

    def test_link_failure_is_non_fatal(self):
        urls = [
            "https://test/browse/PROJ-1",
            "https://test/browse/PROJ-2",
            "https://test/browse/PROJ-100",
        ]
        res = _run("jira", urls, link_side_effect=RuntimeError("link boom"))
        assert res["success"] is True
        assert "PROJ-100" in res["summary"]
