"""Tests for the /brainstorm core skill — handler + runner."""

import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.skills import SkillContext


def _mock_create_issue(create_returns=None):
    """Build a stand-in create_issue service mock for brainstorm runner tests."""
    mock_create = MagicMock()
    if create_returns is not None:
        mock_create.side_effect = create_returns
    return mock_create


# ---------------------------------------------------------------------------
# Import handler functions
# ---------------------------------------------------------------------------

import importlib.util

HANDLER_PATH = Path(__file__).parent.parent / "skills" / "core" / "brainstorm" / "handler.py"
SKILL_DIR = Path(__file__).parent.parent / "skills" / "core" / "brainstorm"


def _load_handler():
    """Load the brainstorm handler module."""
    spec = importlib.util.spec_from_file_location("brainstorm_handler", str(HANDLER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def handler():
    return _load_handler()


@pytest.fixture
def ctx(tmp_path):
    """Create a basic SkillContext for tests."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    missions_path = instance_dir / "missions.md"
    missions_path.write_text("# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n")
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="brainstorm",
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
        assert "/brainstorm" in result
        assert "--tag" in result

    def test_routes_to_brainstorm(self, handler, ctx):
        ctx.args = "Improve caching strategy"
        with patch.object(handler, "_queue_brainstorm", return_value="queued") as mock:
            handler.handle(ctx)
            mock.assert_called_once()

    def test_routes_with_tag(self, handler, ctx):
        ctx.args = "Improve caching --tag prompt-caching"
        with patch.object(handler, "_queue_brainstorm", return_value="queued") as mock:
            handler.handle(ctx)
            mock.assert_called_once()
            # The mission text should contain --tag
            call_args = mock.call_args[0]
            assert "--tag prompt-caching" in call_args[2]  # mission_text

    def test_routes_project_prefixed(self, handler, ctx):
        ctx.args = "koan Improve caching"
        with patch.object(handler, "_queue_brainstorm", return_value="queued") as mock, \
             patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            handler.handle(ctx)
            mock.assert_called_once()

    def test_empty_topic_returns_error(self, handler, ctx):
        ctx.args = "   "
        result = handler.handle(ctx)
        assert "Usage:" in result


# ---------------------------------------------------------------------------
# _extract_tag
# ---------------------------------------------------------------------------

class TestExtractTag:
    def test_no_tag(self, handler):
        tag, remaining = handler._extract_tag("Improve caching")
        assert tag is None
        assert remaining == "Improve caching"

    def test_tag_at_end(self, handler):
        tag, remaining = handler._extract_tag("Improve caching --tag prompt-caching")
        assert tag == "prompt-caching"
        assert remaining == "Improve caching"

    def test_tag_in_middle(self, handler):
        tag, remaining = handler._extract_tag("Improve --tag cache-fix caching strategy")
        assert tag == "cache-fix"
        assert "caching strategy" in remaining

    def test_tag_with_hyphenated_value(self, handler):
        tag, remaining = handler._extract_tag("Topic --tag my-long-tag")
        assert tag == "my-long-tag"


# ---------------------------------------------------------------------------
# _parse_project_arg
# ---------------------------------------------------------------------------

class TestParseProjectArg:
    def test_no_project_prefix(self, handler):
        with patch("app.utils.get_known_projects", return_value=[]):
            project, topic = handler._parse_project_arg("Improve caching")
            assert project is None
            assert topic == "Improve caching"

    def test_project_tag_format(self, handler):
        project, topic = handler._parse_project_arg("[project:koan] Improve caching")
        assert project == "koan"
        assert topic == "Improve caching"

    def test_project_name_prefix(self, handler):
        with patch("app.utils.get_known_projects",
                    return_value=[("koan", "/path")]):
            project, topic = handler._parse_project_arg("koan Improve caching")
            assert project == "koan"
            assert topic == "Improve caching"

    def test_unknown_project_treated_as_topic(self, handler):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            project, topic = handler._parse_project_arg("webapp Improve caching")
            assert project is None
            assert topic == "webapp Improve caching"


# ---------------------------------------------------------------------------
# _queue_brainstorm — mission queuing
# ---------------------------------------------------------------------------

class TestQueueBrainstorm:
    def test_queues_mission(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path/koan")]):
            result = handler._queue_brainstorm(
                ctx, "koan", "/brainstorm Improve caching", "Improve caching",
            )
            assert "queued" in result.lower()
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "/brainstorm Improve caching" in missions
            assert "[project:koan]" in missions

    def test_unknown_project_returns_error(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/path")]):
            result = handler._queue_brainstorm(
                ctx, "unknown", "/brainstorm Topic", "Topic",
            )
            assert "not found" in result

    def test_tag_preserved_in_mission(self, handler, ctx):
        with patch("app.utils.get_known_projects", return_value=[("koan", "/p")]):
            handler._queue_brainstorm(
                ctx, "koan",
                "/brainstorm Improve caching --tag prompt-caching",
                "Improve caching",
            )
            missions = (ctx.instance_dir / "missions.md").read_text()
            assert "--tag prompt-caching" in missions


# ---------------------------------------------------------------------------
# SKILL.md — structure validation
# ---------------------------------------------------------------------------

class TestSkillMd:
    def test_skill_md_parses(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert skill is not None
        assert skill.name == "brainstorm"
        assert skill.scope == "core"
        assert len(skill.commands) == 1
        assert skill.commands[0].name == "brainstorm"

    def test_no_worker_flag(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert skill.worker is False

    def test_github_enabled(self):
        from app.skills import parse_skill_md
        skill = parse_skill_md(SKILL_DIR / "SKILL.md")
        assert skill.github_enabled is True

    def test_skill_registered_in_registry(self):
        from app.skills import build_registry
        registry = build_registry()
        skill = registry.find_by_command("brainstorm")
        assert skill is not None
        assert skill.name == "brainstorm"

    def test_skill_handler_exists(self):
        assert HANDLER_PATH.exists()


# ---------------------------------------------------------------------------
# Decompose prompt
# ---------------------------------------------------------------------------

PROMPT_PATH = SKILL_DIR / "prompts" / "decompose.md"


class TestDecomposePrompt:
    def test_prompt_file_exists(self):
        assert PROMPT_PATH.exists()

    def test_prompt_has_placeholder(self):
        content = PROMPT_PATH.read_text()
        assert "{TOPIC}" in content

    def test_prompt_requests_json(self):
        content = PROMPT_PATH.read_text()
        assert "JSON" in content
        assert "master_summary" in content
        assert "issues" in content


# ---------------------------------------------------------------------------
# brainstorm_runner — unit tests
# ---------------------------------------------------------------------------

from skills.core.brainstorm.brainstorm_runner import (
    _ensure_label,
    _generate_tag,
    _parse_decomposition,
    _build_master_body,
    _extract_master_title,
    _apply_sub_replacements,
    _replace_sub_placeholders,
    _resolve_sub_reference,
    _coerce_top_ranked,
    _coerce_fast_wins,
    _coerce_overall_assessment,
    _validate_issue_bodies,
    _log_prompt_provenance,
    REQUIRED_ISSUE_SECTIONS,
)
import skills.core.brainstorm.brainstorm_runner as brainstorm_runner


class TestGenerateTag:
    def test_basic_topic(self):
        tag = _generate_tag("Improve caching strategy for API responses")
        assert tag == "improve-caching-strategy-api"

    def test_strips_stop_words(self):
        tag = _generate_tag("Add the new feature to the system")
        assert "the" not in tag.split("-")
        assert "to" not in tag.split("-")

    def test_max_four_words(self):
        tag = _generate_tag("one two three four five six seven")
        assert len(tag.split("-")) <= 4

    def test_empty_topic(self):
        tag = _generate_tag("the a an is")
        assert tag == "brainstorm"

    def test_kebab_case(self):
        tag = _generate_tag("Prompt Caching Strategy")
        assert "-" in tag
        assert tag == tag.lower()


class TestParseDecomposition:
    def test_valid_json(self):
        raw = json.dumps({
            "master_summary": "Overview of the initiative.",
            "issues": [
                {"title": "Issue 1", "body": "Body 1"},
                {"title": "Issue 2", "body": "Body 2"},
                {"title": "Issue 3", "body": "Body 3"},
            ]
        })
        data = _parse_decomposition(raw)
        assert len(data["issues"]) == 3
        assert data["master_summary"] == "Overview of the initiative."

    def test_json_with_markdown_fences(self):
        raw = "```json\n" + json.dumps({
            "master_summary": "Summary",
            "issues": [{"title": "T", "body": "B"}],
        }) + "\n```"
        data = _parse_decomposition(raw)
        assert len(data["issues"]) == 1

    def test_json_with_preamble(self):
        raw = "Here is the decomposition:\n\n" + json.dumps({
            "master_summary": "S",
            "issues": [{"title": "T", "body": "B"}],
        })
        data = _parse_decomposition(raw)
        assert len(data["issues"]) == 1

    def test_empty_output_raises(self):
        with pytest.raises(ValueError, match="Empty output"):
            _parse_decomposition("")

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON"):
            _parse_decomposition("Just some text without JSON")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            _parse_decomposition("{invalid json}")

    def test_missing_issues_key_raises(self):
        with pytest.raises(ValueError, match="Missing 'issues'"):
            _parse_decomposition(json.dumps({"master_summary": "x"}))

    def test_missing_title_raises(self):
        with pytest.raises(ValueError, match="missing 'title' or 'body'"):
            _parse_decomposition(json.dumps({
                "master_summary": "x",
                "issues": [{"body": "no title"}],
            }))

    def test_default_master_summary(self):
        data = _parse_decomposition(json.dumps({
            "issues": [{"title": "T", "body": "B"}],
        }))
        assert data["master_summary"] == ""

    def test_synthesis_keys_default_to_none_when_absent(self):
        """Old-shape payloads (no synthesis fields) still parse cleanly."""
        data = _parse_decomposition(json.dumps({
            "master_summary": "S",
            "issues": [{"title": "T", "body": "B"}],
        }))
        assert data["top_ranked"] is None
        assert data["fast_wins"] is None
        assert data["overall_assessment"] is None

    def test_synthesis_keys_passed_through_when_well_formed(self):
        data = _parse_decomposition(json.dumps({
            "master_summary": "S",
            "issues": [
                {"title": "A", "body": "B"},
                {"title": "C", "body": "D"},
                {"title": "E", "body": "F"},
            ],
            "top_ranked": [
                {"position": 2, "rationale": "Highest leverage."},
                {"position": 1, "rationale": "Foundational."},
            ],
            "fast_wins": {
                "under_1_day": ["SUB-1"],
                "under_1_week": ["SUB-2", "SUB-3"],
            },
            "overall_assessment": "Worth pursuing.",
        }))
        assert data["top_ranked"] == [
            {"position": 2, "rationale": "Highest leverage."},
            {"position": 1, "rationale": "Foundational."},
        ]
        assert data["fast_wins"] == {
            "under_1_day": ["SUB-1"],
            "under_1_week": ["SUB-2", "SUB-3"],
        }
        assert data["overall_assessment"] == "Worth pursuing."

    def test_malformed_synthesis_dropped_silently(self):
        """Wrong-typed synthesis values are dropped, not raised — issue
        creation must never be blocked by a bad synthesis blob."""
        data = _parse_decomposition(json.dumps({
            "master_summary": "S",
            "issues": [{"title": "T", "body": "B"}],
            "top_ranked": "not a list",
            "fast_wins": ["not a dict"],
            "overall_assessment": "",
        }))
        assert data["top_ranked"] is None
        assert data["fast_wins"] is None
        assert data["overall_assessment"] is None


class TestBuildMasterBody:
    def test_contains_task_list(self):
        issues = [("1", "Title One", "url1", 1), ("2", "Title Two", "url2", 2)]
        body = _build_master_body("Topic", "Summary", issues, "owner", "repo")
        assert "- [ ] #1" in body
        assert "- [ ] #2" in body
        assert "Title One" in body
        assert "Title Two" in body

    def test_contains_topic(self):
        body = _build_master_body("My topic", "", [("1", "T", "u", 1)], "o", "r")
        assert "My topic" in body

    def test_contains_summary(self):
        body = _build_master_body("T", "My summary", [("1", "T", "u", 1)], "o", "r")
        assert "My summary" in body

    def test_footer(self):
        body = _build_master_body("T", "", [("1", "T", "u", 1)], "o", "r")
        assert "Koan /brainstorm" in body

    def test_no_synthesis_sections_when_keys_absent(self):
        body = _build_master_body(
            "T", "S", [("1", "Alpha", "u", 1), ("2", "Beta", "u", 2)], "o", "r",
        )
        assert "## Top Ranked" not in body
        assert "## Fast Wins" not in body
        assert "## Overall Assessment" not in body

    def test_renders_top_ranked_with_resolved_numbers_and_titles(self):
        issues = [("42", "Alpha", "u1", 1), ("43", "Beta", "u2", 2)]
        top_ranked = [
            {"position": 2, "rationale": "Best ROI; unblocks SUB-1."},
            {"position": 1, "rationale": "Foundational."},
        ]
        body = _build_master_body(
            "T", "", issues, "o", "r", top_ranked=top_ranked,
        )
        assert "## Top Ranked" in body
        assert "1. #43 — Beta" in body
        assert "2. #42 — Alpha" in body
        # SUB-1 inside rationale rewritten to #42
        assert "unblocks #42" in body

    def test_top_ranked_drops_out_of_range_positions(self):
        issues = [("42", "Alpha", "u", 1)]
        top_ranked = [
            {"position": 1, "rationale": "Yes."},
            {"position": 99, "rationale": "Should not appear."},
        ]
        body = _build_master_body(
            "T", "", issues, "o", "r", top_ranked=top_ranked,
        )
        assert "1. #42 — Alpha" in body
        assert "Should not appear" not in body

    def test_renders_fast_wins_with_horizon_headers(self):
        issues = [
            ("10", "Alpha", "u", 1),
            ("11", "Beta", "u", 2),
            ("12", "Gamma", "u", 3),
        ]
        fast_wins = {
            "under_1_day": ["SUB-2"],
            "under_1_week": ["SUB-1", "SUB-3"],
        }
        body = _build_master_body(
            "T", "", issues, "o", "r", fast_wins=fast_wins,
        )
        assert "## Fast Wins" in body
        assert "### < 1 day" in body
        assert "### < 1 week" in body
        # under_1_month not provided, header should be absent
        assert "### < 1 month" not in body
        assert "- #11 — Beta" in body
        assert "- #10 — Alpha" in body
        assert "- #12 — Gamma" in body

    def test_fast_wins_skipped_entirely_when_all_buckets_empty(self):
        issues = [("10", "Alpha", "u", 1)]
        body = _build_master_body(
            "T", "", issues, "o", "r",
            fast_wins={"under_1_day": [], "under_1_week": []},
        )
        # _coerce_fast_wins would have returned None upstream, but
        # _build_master_body should also skip cleanly if it ever receives
        # an all-empty dict directly.
        assert "## Fast Wins" not in body

    def test_renders_overall_assessment_with_sub_replacement(self):
        issues = [("42", "Alpha", "u", 1), ("43", "Beta", "u", 2)]
        body = _build_master_body(
            "T", "", issues, "o", "r",
            overall_assessment="Worth doing. Start with SUB-1, then SUB-2.",
        )
        assert "## Overall Assessment" in body
        assert "Start with #42, then #43." in body

    def test_synthesis_sections_appear_before_subissues_list(self):
        issues = [("42", "Alpha", "u", 1)]
        body = _build_master_body(
            "T", "Summary", issues, "o", "r",
            overall_assessment="Verdict.",
        )
        assert body.index("## Overall Assessment") < body.index("## Sub-Issues")

    def test_gap_in_positions_maps_correctly(self):
        """When issue 2 failed to create, positions 1 and 3 should map to
        original positions, not sequential 1-2."""
        issues = [("42", "Alpha", "u", 1), ("44", "Gamma", "u", 3)]
        top_ranked = [
            {"position": 3, "rationale": "Best."},
            {"position": 1, "rationale": "Second."},
        ]
        body = _build_master_body(
            "T", "", issues, "o", "r", top_ranked=top_ranked,
        )
        assert "1. #44 — Gamma" in body
        assert "2. #42 — Alpha" in body


class TestApplySubReplacements:
    def test_replaces_sub_placeholders(self):
        mapping = {1: "42", 2: "43", 3: "44"}
        text = "Depends on SUB-1 and SUB-2. See also SUB-3."
        result = _apply_sub_replacements(text, mapping)
        assert result == "Depends on #42 and #43. See also #44."

    def test_leaves_unknown_placeholders(self):
        mapping = {1: "42"}
        text = "Depends on SUB-1 and SUB-5."
        result = _apply_sub_replacements(text, mapping)
        assert "#42" in result
        assert "SUB-5" in result

    def test_no_placeholders_unchanged(self):
        mapping = {1: "42"}
        text = "No cross-references here."
        result = _apply_sub_replacements(text, mapping)
        assert result == text

    def test_multiple_occurrences_of_same_placeholder(self):
        mapping = {1: "99"}
        text = "SUB-1 is needed before SUB-1 can be tested."
        result = _apply_sub_replacements(text, mapping)
        assert result == "#99 is needed before #99 can be tested."

    def test_preserves_existing_hash_references(self):
        """Real GitHub #N references in the text should not be touched."""
        mapping = {1: "42"}
        text = "This fixes #10. Depends on SUB-1."
        result = _apply_sub_replacements(text, mapping)
        assert "#10" in result
        assert "#42" in result


class TestReplaceSubPlaceholders:
    def test_calls_issue_edit_for_changed_bodies(self):
        created = [("42", "Title A", "url1", 1), ("43", "Title B", "url2", 2)]
        original = [
            {"title": "Title A", "body": "Depends on SUB-2."},
            {"title": "Title B", "body": "No deps."},
        ]
        with patch("skills.core.brainstorm.brainstorm_runner.issue_edit") as mock_edit:
            _replace_sub_placeholders(created, original, "/fake")
            # Only issue 42 had a placeholder that changed
            mock_edit.assert_called_once_with("42", "Depends on #43.", cwd="/fake", repo=None)

    def test_skips_edit_when_no_placeholders(self):
        created = [("10", "T", "u", 1)]
        original = [{"title": "T", "body": "No placeholders here."}]
        with patch("skills.core.brainstorm.brainstorm_runner.issue_edit") as mock_edit:
            _replace_sub_placeholders(created, original, "/fake")
            mock_edit.assert_not_called()

    def test_handles_edit_failure_gracefully(self):
        created = [("42", "T", "u", 1), ("43", "T2", "u2", 2)]
        original = [
            {"title": "T", "body": "See SUB-2"},
            {"title": "T2", "body": "See SUB-1"},
        ]
        with patch("skills.core.brainstorm.brainstorm_runner.issue_edit",
                    side_effect=RuntimeError("API error")):
            # Should not raise — errors are caught and logged
            _replace_sub_placeholders(created, original, "/fake")

    def test_gap_in_positions_uses_correct_original_body(self):
        """When issue 2 failed to create, issue 3's body should still be
        fetched from original_issues[2], not original_issues[1]."""
        created = [("42", "A", "u", 1), ("44", "C", "u", 3)]
        original = [
            {"title": "A", "body": "See SUB-3"},
            {"title": "B", "body": "See SUB-1"},  # this one failed
            {"title": "C", "body": "See SUB-1"},
        ]
        with patch("skills.core.brainstorm.brainstorm_runner.issue_edit") as mock_edit:
            _replace_sub_placeholders(created, original, "/fake")
            # Both issues reference existing ones, so both get edited
            calls = {c.args[0]: c.args[1] for c in mock_edit.call_args_list}
            assert calls["42"] == "See #44"  # SUB-3 → #44
            assert calls["44"] == "See #42"  # SUB-1 → #42

    def test_passes_repo_to_issue_edit(self):
        """When repo is provided, it must be forwarded to issue_edit."""
        created = [("42", "A", "u1", 1)]
        original = [{"title": "A", "body": "See SUB-1"}]
        with patch("skills.core.brainstorm.brainstorm_runner.issue_edit") as mock_edit:
            _replace_sub_placeholders(
                created, original, "/fake", repo="upstream/myapp",
            )
            mock_edit.assert_called_once_with(
                "42", "See #42", cwd="/fake", repo="upstream/myapp",
            )


class TestEnsureLabel:
    def test_passes_repo_flag_to_run_gh(self):
        with patch("skills.core.brainstorm.brainstorm_runner.run_gh") as mock_gh:
            _ensure_label("my-tag", "/fake", repo="upstream/app")
            args = mock_gh.call_args
            flat = list(args.args)
            assert "--repo" in flat
            assert flat[flat.index("--repo") + 1] == "upstream/app"

    def test_omits_repo_flag_when_none(self):
        with patch("skills.core.brainstorm.brainstorm_runner.run_gh") as mock_gh:
            _ensure_label("my-tag", "/fake")
            flat = list(mock_gh.call_args.args)
            assert "--repo" not in flat


class TestExtractMasterTitle:
    def test_short_topic(self):
        assert _extract_master_title("Fix caching") == "Fix caching"

    def test_long_topic_truncated(self):
        long = "A" * 200
        result = _extract_master_title(long)
        assert len(result) <= 100
        assert result.endswith("...")

    def test_first_sentence(self):
        result = _extract_master_title("Fix caching. Then do more stuff.")
        assert result == "Fix caching"

    def test_empty_topic(self):
        assert _extract_master_title("") == "Brainstorm"


class TestCoerceTopRanked:
    def test_drops_non_list(self):
        assert _coerce_top_ranked("oops", num_issues=3) is None
        assert _coerce_top_ranked(None, num_issues=3) is None

    def test_drops_out_of_range_positions(self):
        result = _coerce_top_ranked(
            [{"position": 1, "rationale": "ok"},
             {"position": 99, "rationale": "out of range"},
             {"position": 0, "rationale": "below"}],
            num_issues=2,
        )
        assert result == [{"position": 1, "rationale": "ok"}]

    def test_drops_non_int_positions(self):
        result = _coerce_top_ranked(
            [{"position": "1", "rationale": "string"}],
            num_issues=2,
        )
        assert result is None

    def test_empty_rationale_replaced_with_blank_string(self):
        result = _coerce_top_ranked(
            [{"position": 1}],
            num_issues=2,
        )
        assert result == [{"position": 1, "rationale": ""}]

    def test_returns_none_when_all_entries_invalid(self):
        result = _coerce_top_ranked(
            [{"position": 0}, "garbage"], num_issues=2,
        )
        assert result is None


class TestCoerceFastWins:
    def test_drops_non_dict(self):
        assert _coerce_fast_wins("oops") is None
        assert _coerce_fast_wins(["a", "b"]) is None
        assert _coerce_fast_wins(None) is None

    def test_keeps_only_recognized_buckets(self):
        result = _coerce_fast_wins({
            "under_1_day": ["SUB-1"],
            "under_1_year": ["SUB-2"],  # not a recognized bucket
            "random_key": ["junk"],
        })
        assert result == {"under_1_day": ["SUB-1"]}

    def test_filters_non_string_items(self):
        result = _coerce_fast_wins({
            "under_1_week": ["SUB-1", 42, None, "SUB-3", ""],
        })
        assert result == {"under_1_week": ["SUB-1", "SUB-3"]}

    def test_returns_none_when_all_buckets_empty(self):
        result = _coerce_fast_wins({
            "under_1_day": [],
            "under_1_week": [None, ""],
        })
        assert result is None


class TestCoerceOverallAssessment:
    def test_strips_and_returns_string(self):
        assert _coerce_overall_assessment("  worth doing  ") == "worth doing"

    def test_drops_non_string(self):
        assert _coerce_overall_assessment(42) is None
        assert _coerce_overall_assessment(None) is None
        assert _coerce_overall_assessment(["list"]) is None

    def test_drops_empty_or_whitespace(self):
        assert _coerce_overall_assessment("") is None
        assert _coerce_overall_assessment("   \n  ") is None


class TestResolveSubReference:
    def test_resolves_exact_sub_token_to_number_and_title(self):
        result = _resolve_sub_reference(
            "SUB-1", {1: "42"}, {1: "Alpha"},
        )
        assert result == "#42 — Alpha"

    def test_falls_back_to_number_only_when_title_missing(self):
        result = _resolve_sub_reference("SUB-1", {1: "42"}, {})
        assert result == "#42"

    def test_unknown_sub_token_left_unresolved(self):
        result = _resolve_sub_reference("SUB-9", {1: "42"}, {1: "Alpha"})
        # Unknown SUB-N is left as-is by _apply_sub_replacements.
        assert "SUB-9" in result

    def test_freeform_string_has_embedded_subs_rewritten(self):
        result = _resolve_sub_reference(
            "After SUB-1 lands", {1: "42"}, {1: "Alpha"},
        )
        # Not an exact SUB-N match → falls through to the rewrite path.
        assert result == "After #42 lands"

    def test_non_string_returns_empty(self):
        assert _resolve_sub_reference(None, {}, {}) == ""
        assert _resolve_sub_reference(42, {}, {}) == ""


# ---------------------------------------------------------------------------
# skill_dispatch integration
# ---------------------------------------------------------------------------

class TestSkillDispatch:
    def test_brainstorm_in_skill_runners(self):
        from app.skill_dispatch import _SKILL_RUNNERS
        assert "brainstorm" in _SKILL_RUNNERS
        assert _SKILL_RUNNERS["brainstorm"] == "skills.core.brainstorm.brainstorm_runner"

    def test_build_brainstorm_cmd_basic(self):
        from app.skill_dispatch import _build_brainstorm_cmd
        import sys
        base_cmd = [sys.executable, "-m", "skills.core.brainstorm.brainstorm_runner"]
        cmd = _build_brainstorm_cmd(base_cmd, "Improve caching", "/project/path")
        assert "--project-path" in cmd
        assert "/project/path" in cmd
        assert "--topic" in cmd
        assert "Improve caching" in cmd
        assert "--tag" not in cmd

    def test_build_brainstorm_cmd_with_tag(self):
        from app.skill_dispatch import _build_brainstorm_cmd
        import sys
        base_cmd = [sys.executable, "-m", "skills.core.brainstorm.brainstorm_runner"]
        cmd = _build_brainstorm_cmd(
            base_cmd, "Improve caching --tag prompt-caching", "/p",
        )
        assert "--tag" in cmd
        assert "prompt-caching" in cmd
        # Topic should not contain --tag
        topic_idx = cmd.index("--topic")
        topic_value = cmd[topic_idx + 1]
        assert "--tag" not in topic_value

    def test_is_skill_mission(self):
        from app.skill_dispatch import is_skill_mission
        assert is_skill_mission("/brainstorm Improve caching")
        assert is_skill_mission("[project:koan] /brainstorm Topic")

    def test_parse_skill_mission(self):
        from app.skill_dispatch import parse_skill_mission
        project, cmd, args = parse_skill_mission(
            "[project:koan] /brainstorm Improve caching --tag cache"
        )
        assert project == "koan"
        assert cmd == "brainstorm"
        assert "Improve caching --tag cache" in args


# ---------------------------------------------------------------------------
# Runner — max_turns config
# ---------------------------------------------------------------------------

RUNNER_PATH = Path(__file__).parent.parent / "skills" / "core" / "brainstorm" / "brainstorm_runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("brainstorm_runner", str(RUNNER_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def runner():
    return _load_runner()


class TestDecomposeMaxTurns:
    """Verify _decompose_topic uses configurable max_turns, not a hardcoded value."""

    def test_max_turns_from_config(self, runner):
        """max_turns should come from get_analysis_max_turns()."""
        mock_run = MagicMock(return_value="decomposition output")
        with patch.object(runner, "load_prompt_or_skill", return_value="prompt"), \
             patch("app.cli_provider.run_command_streaming", mock_run), \
             patch("app.config.get_analysis_max_turns", return_value=42), \
             patch("app.config.get_skill_timeout", return_value=600):
            result = runner._decompose_topic("/tmp/proj", "topic")

        assert mock_run.call_args[1]["max_turns"] == 42


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def _full_body():
    """Return a body string containing every required section header."""
    return "\n\n".join(f"{h}\nplaceholder" for h in REQUIRED_ISSUE_SECTIONS)


class TestValidateIssueBodies:
    def test_required_sections_constant_has_seven_headers(self):
        assert len(REQUIRED_ISSUE_SECTIONS) == 7
        # Spot-check the canonical names — these are also referenced in
        # the prompt's required-sections checklist, so they must match.
        assert "## Why This Matters" in REQUIRED_ISSUE_SECTIONS
        assert "## Risks & Caveats" in REQUIRED_ISSUE_SECTIONS
        assert "## Scores" in REQUIRED_ISSUE_SECTIONS
        assert "## Priority" in REQUIRED_ISSUE_SECTIONS

    def test_all_sections_present_returns_no_diagnostics(self):
        issues = [
            {"title": "Alpha", "body": _full_body()},
            {"title": "Beta",  "body": _full_body()},
        ]
        assert _validate_issue_bodies(issues) == []

    def test_one_missing_section_yields_one_diagnostic(self):
        body = _full_body().replace("## Risks & Caveats\n", "")
        diagnostics = _validate_issue_bodies(
            [{"title": "Alpha", "body": body}]
        )
        assert len(diagnostics) == 1
        assert "Issue 1" in diagnostics[0]
        assert "Alpha" in diagnostics[0]
        assert "## Risks & Caveats" in diagnostics[0]

    def test_old_template_is_fully_rejected(self):
        """The exact old-template body from the cryptoan run must
        trigger diagnostics for all four newly-required headers."""
        old_body = (
            "## Context\nFoundational thing.\n\n"
            "## Approach\nDo this.\n\n"
            "## Acceptance Criteria\n- [ ] Done\n\n"
            "## Dependencies\nNone."
        )
        diagnostics = _validate_issue_bodies(
            [{"title": "Implement signal ensemble", "body": old_body}]
        )
        missing_headers = {d.split("missing '")[1].rstrip("'") for d in diagnostics}
        assert "## Why This Matters" in missing_headers
        assert "## Risks & Caveats" in missing_headers
        assert "## Scores" in missing_headers
        assert "## Priority" in missing_headers
        # Old template did include these — should NOT be flagged
        assert "## Approach" not in missing_headers
        assert "## Acceptance Criteria" not in missing_headers
        assert "## Dependencies" not in missing_headers

    def test_empty_body_flags_all_seven_sections(self):
        diagnostics = _validate_issue_bodies(
            [{"title": "Empty", "body": ""}]
        )
        assert len(diagnostics) == len(REQUIRED_ISSUE_SECTIONS)

    def test_missing_body_key_treated_as_empty(self):
        diagnostics = _validate_issue_bodies([{"title": "No body"}])
        assert len(diagnostics) == len(REQUIRED_ISSUE_SECTIONS)

    def test_diagnostic_includes_issue_number_and_title_preview(self):
        issues = [
            {"title": "Alpha", "body": _full_body()},
            {"title": "B" * 80, "body": ""},
        ]
        diagnostics = _validate_issue_bodies(issues)
        assert all("Issue 2" in d for d in diagnostics)
        # Title preview is truncated to 40 chars
        assert all(("'" + "B" * 40 + "'") in d for d in diagnostics)


# ---------------------------------------------------------------------------
# Prompt provenance log
# ---------------------------------------------------------------------------


class TestPromptProvenance:
    def test_logs_version_new_when_marker_present(self, capsys):
        prompt = "Some prefix.\n\n## Why This Matters\n...rest of prompt."
        _log_prompt_provenance(Path("/some/path/decompose.md"), prompt)
        err = capsys.readouterr().err
        assert "prompt_provenance" in err
        assert "version=new" in err
        assert "path=/some/path/decompose.md" in err
        assert f"size={len(prompt)}" in err

    def test_logs_version_old_when_marker_absent(self, capsys):
        prompt = "You are a technical decomposition assistant.\n## Context\n..."
        _log_prompt_provenance(Path("/old/decompose.md"), prompt)
        err = capsys.readouterr().err
        assert "version=old" in err

    def test_includes_truncated_sha256(self, capsys):
        prompt = "## Why This Matters\nbody"
        _log_prompt_provenance(Path("/p.md"), prompt)
        err = capsys.readouterr().err
        match = re.search(r"head_sha256=([0-9a-f]+)", err)
        assert match is not None
        assert len(match.group(1)) == 12

    def test_handles_none_path_gracefully(self, capsys):
        _log_prompt_provenance(None, "## Why This Matters\nbody")
        err = capsys.readouterr().err
        assert "path=<system-prompt>" in err

    def test_handles_empty_prompt(self, capsys):
        _log_prompt_provenance(Path("/missing.md"), "")
        err = capsys.readouterr().err
        assert "size=0" in err
        assert "version=old" in err  # marker absent → old


# ---------------------------------------------------------------------------
# run_brainstorm — retry-once on validation failure
# ---------------------------------------------------------------------------


def _decomposition_json(issues_bodies, master_summary="Initiative summary."):
    """Build a JSON decomposition string from issue bodies."""
    return json.dumps({
        "master_summary": master_summary,
        "issues": [
            {"title": f"Issue {i+1}", "body": body}
            for i, body in enumerate(issues_bodies)
        ],
    })


_OLD_BODY = (
    "## Context\nx\n\n## Approach\nx\n\n"
    "## Acceptance Criteria\n- [ ] x\n\n## Dependencies\nNone"
)


class TestRunBrainstormRetry:
    def _run(self, claude_outputs, issue_create_returns=None):
        """Execute run_brainstorm with stubbed Claude + GitHub calls."""
        if issue_create_returns is None:
            issue_create_returns = [
                f"https://github.com/o/r/issues/{100 + i}"
                for i in range(len(claude_outputs[-1]) if claude_outputs else 3)
            ]
        notify = MagicMock()
        mock_create = _mock_create_issue(issue_create_returns)
        # _build_decompose_prompt avoids touching disk
        with patch.object(brainstorm_runner, "_build_decompose_prompt",
                          return_value="<prompt>"), \
             patch.object(brainstorm_runner, "_call_claude_with_prompt",
                          side_effect=claude_outputs) as mock_claude, \
             patch.object(brainstorm_runner, "tracker_is_configured",
                          return_value=True), \
             patch.object(brainstorm_runner, "tracker_supports_labels",
                          return_value=True), \
             patch.object(brainstorm_runner, "tracker_provider",
                          return_value="github"), \
             patch.object(brainstorm_runner, "create_issue", mock_create), \
             patch.object(brainstorm_runner, "_ensure_label"), \
             patch.object(brainstorm_runner, "_replace_sub_placeholders"):
            success, summary = brainstorm_runner.run_brainstorm(
                project_path="/proj",
                topic="A topic",
                tag="my-tag",
                notify_fn=notify,
            )
        return success, summary, mock_claude, mock_create, notify

    def test_no_retry_when_first_response_is_compliant(self):
        good = _decomposition_json([_full_body()] * 3)
        success, summary, mock_claude, mock_create, _notify = self._run(
            [good],
        )
        assert success is True
        assert mock_claude.call_count == 1
        assert mock_create.call_count >= 3

    def test_retries_once_when_first_response_is_old_shape(self):
        bad = _decomposition_json([_OLD_BODY] * 3)
        good = _decomposition_json([_full_body()] * 3)
        success, summary, mock_claude, mock_create, notify = self._run(
            [bad, good],
        )
        assert success is True
        assert mock_claude.call_count == 2
        # Second call must include the retry reminder appended to prompt
        second_prompt = mock_claude.call_args_list[1].args[0]
        assert "ATTENTION" in second_prompt
        assert "## Why This Matters" in second_prompt
        # User got a notification about the retry
        assert any(
            "retrying" in str(c).lower() or "template" in str(c).lower()
            for c in notify.call_args_list
        )
        # Issues created from the retry response, not the bad first one
        assert mock_create.call_count >= 3

    def test_aborts_when_both_attempts_fail_validation(self):
        bad = _decomposition_json([_OLD_BODY] * 3)
        success, summary, mock_claude, mock_create, _notify = self._run(
            [bad, bad],
        )
        assert success is False
        assert mock_claude.call_count == 2
        # No GitHub issues are created when validation fails twice
        assert mock_create.call_count == 0
        assert "Template enforcement failed" in summary

    def test_summary_truncates_long_diagnostic_list(self):
        # Three issues × 4 missing sections = 12 diagnostics
        bad = _decomposition_json([_OLD_BODY] * 3)
        success, summary, _claude, _create, _notify = self._run([bad, bad])
        assert success is False
        # Only the first three diagnostics in the summary, plus a count
        assert "+9 more" in summary

    def test_master_synthesis_warning_when_all_keys_absent(self, capsys):
        good = _decomposition_json([_full_body()] * 3)
        mock_create = _mock_create_issue([f"https://x/{i}" for i in range(10)])
        with patch.object(brainstorm_runner, "_build_decompose_prompt",
                          return_value="<prompt>"), \
             patch.object(brainstorm_runner, "_call_claude_with_prompt",
                          return_value=good), \
             patch.object(brainstorm_runner, "tracker_is_configured",
                          return_value=True), \
             patch.object(brainstorm_runner, "tracker_supports_labels",
                          return_value=True), \
             patch.object(brainstorm_runner, "tracker_provider",
                          return_value="github"), \
             patch.object(brainstorm_runner, "create_issue", mock_create), \
             patch.object(brainstorm_runner, "_ensure_label"), \
             patch.object(brainstorm_runner, "_replace_sub_placeholders"):
            brainstorm_runner.run_brainstorm(
                project_path="/proj",
                topic="t",
                tag="t",
                notify_fn=MagicMock(),
            )
        err = capsys.readouterr().err
        assert "master synthesis absent" in err
