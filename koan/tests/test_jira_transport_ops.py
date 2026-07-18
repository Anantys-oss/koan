"""Tests for Jira issue-description update + native issue-link transport ops."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app import jira_notifications


@contextmanager
def _mock_auth():
    with patch.object(
        jira_notifications,
        "_jira_auth_from_config",
        return_value=("https://org.atlassian.net", "Basic xxx"),
    ):
        yield


class TestJiraUpdateIssueDescription:
    def test_puts_adf_description_to_correct_endpoint(self):
        with _mock_auth(), patch.object(
            jira_notifications, "_jira_put", return_value={}
        ) as put:
            ok = jira_notifications.jira_update_issue_description("PROJ-12", "## H\n\n- a")
        assert ok is True
        _base, _auth, path, body = put.call_args[0]
        assert path == "/rest/api/3/issue/PROJ-12"
        desc = body["fields"]["description"]
        assert desc["type"] == "doc"
        assert desc["content"][0]["type"] == "heading"

    def test_returns_false_on_transport_failure(self):
        with _mock_auth(), patch.object(
            jira_notifications, "_jira_put", return_value=None
        ):
            assert jira_notifications.jira_update_issue_description("PROJ-12", "x") is False

    def test_returns_false_on_blank_key(self):
        assert jira_notifications.jira_update_issue_description("", "x") is False


class TestJiraLinkIssues:
    def test_posts_relates_link_and_returns_true_on_2xx(self):
        resp = MagicMock()
        resp.status = 201
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        with _mock_auth(), patch("urllib.request.urlopen", return_value=resp) as urlopen:
            ok = jira_notifications.jira_link_issues("PROJ-1", "PROJ-2")
        assert ok is True
        req = urlopen.call_args[0][0]
        assert req.full_url.endswith("/rest/api/3/issueLink")
        import json as _json

        payload = _json.loads(req.data.decode("utf-8"))
        assert payload["type"]["name"] == "Relates"
        assert payload["outwardIssue"]["key"] == "PROJ-1"
        assert payload["inwardIssue"]["key"] == "PROJ-2"

    def test_custom_link_type(self):
        resp = MagicMock()
        resp.status = 201
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        with _mock_auth(), patch("urllib.request.urlopen", return_value=resp):
            assert jira_notifications.jira_link_issues("A-1", "A-2", "Blocks") is True

    def test_returns_false_on_exception(self):
        with _mock_auth(), patch(
            "urllib.request.urlopen", side_effect=OSError("boom")
        ):
            assert jira_notifications.jira_link_issues("PROJ-1", "PROJ-2") is False

    def test_returns_false_on_blank_keys(self):
        assert jira_notifications.jira_link_issues("", "PROJ-2") is False
        assert jira_notifications.jira_link_issues("PROJ-1", "") is False
