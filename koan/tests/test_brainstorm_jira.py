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
        patch.object(brainstorm_runner, "tracker_provider", return_value=provider),
        patch.object(brainstorm_runner, "client_for_project", return_value=client),
        patch.object(brainstorm_runner, "create_issue", create_issue),
        patch.object(brainstorm_runner, "_build_decompose_prompt", return_value="P"),
        patch.object(brainstorm_runner, "_call_claude_with_prompt",
                     return_value=decomposition),
        patch.object(brainstorm_runner, "_ensure_label"),
        patch("app.issue_tracker.update_issue", update_issue),
        patch("app.issue_tracker.link_issues", link_issues),
        patch.object(brainstorm_runner, "issue_edit") as issue_edit,
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
        "issue_edit": issue_edit,
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
