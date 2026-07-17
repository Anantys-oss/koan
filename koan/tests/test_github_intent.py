"""Tests for github_intent.py — NLP intent classifier for GitHub @mentions."""

import json
from unittest.mock import patch

import pytest

from app.github_intent import _parse_classification, classify_intent

pytestmark = pytest.mark.slow


SAMPLE_COMMANDS = [
    ("rebase", "Rebase a PR onto the latest base branch"),
    ("implement", "Implement a feature from an issue"),
    ("fix", "Fix a bug"),
    ("review", "Review a pull request"),
]


class TestParseClassification:
    def test_valid_json(self):
        result = _parse_classification('{"command": "fix", "context": "the login bug"}')
        assert result == {"command": "fix", "context": "the login bug", "confidence": 0.0}

    def test_null_command(self):
        result = _parse_classification('{"command": null, "context": ""}')
        assert result == {"command": None, "context": "", "confidence": 0.0}

    def test_json_in_code_block(self):
        text = '```json\n{"command": "rebase", "context": ""}\n```'
        result = _parse_classification(text)
        assert result == {"command": "rebase", "context": "", "confidence": 0.0}

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"command": "review", "context": "PR #42"}\nDone.'
        result = _parse_classification(text)
        assert result == {"command": "review", "context": "PR #42", "confidence": 0.0}

    def test_empty_output(self):
        assert _parse_classification("") is None
        assert _parse_classification(None) is None

    def test_invalid_json(self):
        assert _parse_classification("not json at all") is None

    def test_strips_slash_from_command(self):
        result = _parse_classification('{"command": "/fix", "context": ""}')
        assert result == {"command": "fix", "context": "", "confidence": 0.0}

    def test_empty_command_becomes_none(self):
        result = _parse_classification('{"command": "", "context": ""}')
        assert result == {"command": None, "context": "", "confidence": 0.0}

    def test_non_dict_json(self):
        assert _parse_classification("[1, 2, 3]") is None

    def test_missing_context_defaults_empty(self):
        result = _parse_classification('{"command": "fix"}')
        assert result == {"command": "fix", "context": "", "confidence": 0.0}

    def test_confidence_parsed_and_clamped(self):
        assert _parse_classification(
            '{"command": "review", "context": "x", "confidence": 0.91}'
        )["confidence"] == 0.91
        assert _parse_classification(
            '{"command": "review", "context": "x", "confidence": 1.7}'
        )["confidence"] == 1.0
        assert _parse_classification(
            '{"command": "review", "context": "x", "confidence": -3}'
        )["confidence"] == 0.0

    def test_confidence_invalid_fails_closed(self):
        assert _parse_classification(
            '{"command": "review", "context": "x", "confidence": "bad"}'
        )["confidence"] == 0.0
        assert _parse_classification(
            '{"command": "review", "context": "x"}'
        )["confidence"] == 0.0


def _patch_cli_and_prompt(mock_output):
    """Patch both run_command and load_prompt for classify_intent tests."""
    prompt_template = "Commands:\n{COMMANDS}\n\nMessage:\n{MESSAGE}"
    return (
        patch("app.cli_provider.run_command", return_value=mock_output),
        patch("app.prompts.load_prompt", return_value=prompt_template),
    )


class TestClassifyIntent:
    def test_successful_classification(self):
        mock_output = '{"command": "fix", "context": "the login bug"}'
        p1, p2 = _patch_cli_and_prompt(mock_output)
        with p1, p2:
            result = classify_intent(
                "this is a bug, please fix it",
                SAMPLE_COMMANDS,
                "/tmp/project",
            )
        assert result == {"command": "fix", "context": "the login bug", "confidence": 0.0}

    def test_empty_message(self):
        assert classify_intent("", SAMPLE_COMMANDS, "/tmp/project") is None
        assert classify_intent("  ", SAMPLE_COMMANDS, "/tmp/project") is None

    def test_no_commands(self):
        assert classify_intent("fix this", [], "/tmp/project") is None

    def test_cli_failure_returns_none(self):
        prompt_template = "Commands:\n{COMMANDS}\n\nMessage:\n{MESSAGE}"
        with patch("app.cli_provider.run_command", side_effect=RuntimeError("timeout")), \
             patch("app.prompts.load_prompt", return_value=prompt_template):
            result = classify_intent(
                "please review this PR",
                SAMPLE_COMMANDS,
                "/tmp/project",
            )
        assert result is None

    def test_os_error_returns_none(self):
        prompt_template = "Commands:\n{COMMANDS}\n\nMessage:\n{MESSAGE}"
        with patch("app.cli_provider.run_command", side_effect=OSError("no such file")), \
             patch("app.prompts.load_prompt", return_value=prompt_template):
            result = classify_intent(
                "please review this PR",
                SAMPLE_COMMANDS,
                "/tmp/project",
            )
        assert result is None

    def test_prompt_loaded_and_filled(self):
        mock_output = '{"command": "rebase", "context": ""}'
        prompt_template = "Commands:\n{COMMANDS}\n\nMessage:\n{MESSAGE}"
        with patch("app.cli_provider.run_command", return_value=mock_output) as mock_run, \
             patch("app.prompts.load_prompt", return_value=prompt_template):
            classify_intent("rebase this please", SAMPLE_COMMANDS, "/tmp/project")
            call_args = mock_run.call_args
            prompt = call_args.kwargs.get("prompt") or call_args[0][0]
            assert "rebase" in prompt
            assert "implement" in prompt
            assert "rebase this please" in prompt

    def test_uses_lightweight_model(self):
        mock_output = '{"command": null, "context": ""}'
        p1, p2 = _patch_cli_and_prompt(mock_output)
        with p1 as mock_run, p2:
            classify_intent("hello", SAMPLE_COMMANDS, "/tmp/project")
            call_args = mock_run.call_args
            assert call_args.kwargs.get("model_key") == "lightweight"

    def test_ambiguous_returns_null_command(self):
        mock_output = '{"command": null, "context": ""}'
        p1, p2 = _patch_cli_and_prompt(mock_output)
        with p1, p2:
            result = classify_intent("hello there", SAMPLE_COMMANDS, "/tmp/project")
        assert result == {"command": None, "context": "", "confidence": 0.0}

    def test_malformed_output_returns_none(self):
        p1, p2 = _patch_cli_and_prompt("I don't understand")
        # Override p1 with the actual bad output
        with patch("app.cli_provider.run_command", return_value="I don't understand"), p2:
            result = classify_intent("do something", SAMPLE_COMMANDS, "/tmp/project")
        assert result is None

    def test_missing_prompt_returns_none(self):
        with patch("app.prompts.load_prompt", return_value=None):
            result = classify_intent("fix this", SAMPLE_COMMANDS, "/tmp/project")
        assert result is None


# ---------------------------------------------------------------------------
# Intent ladder: keyword layer, URL guard, unified resolver
# ---------------------------------------------------------------------------

from app.skills import Skill, SkillCommand, SkillRegistry  # noqa: E402


def _reg():
    """A registry with github-enabled review/rebase/fix + excluded gh_request/ask."""
    reg = SkillRegistry()
    reg._register(Skill(
        name="review", scope="core", github_enabled=True,
        commands=[SkillCommand(name="review", aliases=["rv"])],
    ))
    reg._register(Skill(
        name="rebase", scope="core", github_enabled=True,
        commands=[SkillCommand(name="rebase")],
    ))
    reg._register(Skill(
        name="fix", scope="core", github_enabled=True,
        commands=[SkillCommand(name="fix")],
    ))
    reg._register(Skill(
        name="gh_request", scope="core", github_enabled=True,
        commands=[SkillCommand(name="gh_request")],
    ))
    reg._register(Skill(
        name="ask", scope="core", github_enabled=True,
        commands=[SkillCommand(name="ask")],
    ))
    return reg


class TestMatchSkillKeyword:
    def test_single_hit_with_filler(self):
        from app.github_intent import match_skill_keyword
        m = match_skill_keyword("eh do a review", _reg())
        assert m is not None
        assert m.command == "review"
        assert m.source == "keyword"
        assert m.confidence == 1.0

    def test_alias(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("can you rv quickly", _reg()).command == "review"

    def test_multi_hit_returns_none(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("fix the review nits", _reg()) is None

    def test_no_hit_returns_none(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("what do you think about this", _reg()) is None

    def test_incidental_noun_not_promoted(self):
        # Keyword not at token 0 and not preceded by an imperative lead-in is an
        # incidental noun, not an actionable ask — must not auto-dispatch.
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("the review looks good", _reg()) is None
        assert match_skill_keyword("this rebase broke prod", _reg()) is None

    def test_leading_keyword_promoted(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("review this please", _reg()).command == "review"

    def test_outside_window_returns_none(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("a b c d e review", _reg(), window=5) is None

    def test_excludes_meta_and_ask(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("please gh_request this", _reg()) is None
        assert match_skill_keyword("ask what the plan is", _reg()) is None

    def test_empty_returns_none(self):
        from app.github_intent import match_skill_keyword
        assert match_skill_keyword("", _reg()) is None
        assert match_skill_keyword("   ", _reg()) is None

    def test_context_strips_command_token(self):
        from app.github_intent import match_skill_keyword
        m = match_skill_keyword("please review this", _reg())
        assert m.command == "review"
        assert "review" not in m.context.split()


class TestUrlTypeGuard:
    def test_matrix(self):
        from app.github_intent import _url_type_ok
        assert _url_type_ok("review", "pr") is True
        assert _url_type_ok("review", "issue") is False
        assert _url_type_ok("fix", "issue") is True
        assert _url_type_ok("fix", "pr") is False
        # Unknown subject: never block on missing info.
        assert _url_type_ok("review", "") is True
        assert _url_type_ok("fix", "") is True


class TestResolveGithubIntent:
    def test_keyword_first_no_model(self, monkeypatch):
        import app.github_intent as gi
        called = {"n": 0}
        monkeypatch.setattr(
            gi, "classify_intent",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        m = gi.resolve_github_intent(
            "eh do a review", _reg(), subject_kind="pr", project_path="/tmp",
        )
        assert m.command == "review"
        assert m.source == "keyword"
        assert called["n"] == 0  # keyword hit ⇒ model never called

    def test_model_high_confidence(self, monkeypatch):
        import app.github_intent as gi
        monkeypatch.setattr(
            gi, "classify_intent",
            lambda *a, **k: {"command": "rebase", "context": "onto main", "confidence": 0.91},
        )
        m = gi.resolve_github_intent(
            "could you tidy the branch history", _reg(),
            subject_kind="pr", project_path="/tmp",
        )
        assert m.command == "rebase"
        assert m.source == "model"
        assert m.confidence == 0.91

    def test_model_low_confidence_returns_none(self, monkeypatch):
        import app.github_intent as gi
        monkeypatch.setattr(
            gi, "classify_intent",
            lambda *a, **k: {"command": "review", "context": "", "confidence": 0.4},
        )
        assert gi.resolve_github_intent(
            "hmm not sure what", _reg(), subject_kind="pr", project_path="/tmp",
        ) is None

    def test_model_url_guard_blocks(self, monkeypatch):
        import app.github_intent as gi
        monkeypatch.setattr(
            gi, "classify_intent",
            lambda *a, **k: {"command": "review", "context": "", "confidence": 0.95},
        )
        # review needs a PR; subject is an issue ⇒ blocked.
        assert gi.resolve_github_intent(
            "take a good look", _reg(), subject_kind="issue", project_path="/tmp",
        ) is None

    def test_model_meta_command_rejected(self, monkeypatch):
        import app.github_intent as gi
        monkeypatch.setattr(
            gi, "classify_intent",
            lambda *a, **k: {"command": "gh_request", "context": "", "confidence": 0.99},
        )
        assert gi.resolve_github_intent(
            "just handle it somehow", _reg(), subject_kind="pr", project_path="/tmp",
        ) is None

    def test_no_project_path_no_model(self, monkeypatch):
        import app.github_intent as gi
        called = {"n": 0}
        monkeypatch.setattr(
            gi, "classify_intent",
            lambda *a, **k: called.__setitem__("n", called["n"] + 1),
        )
        # No keyword, no project_path ⇒ can't run model ⇒ None.
        assert gi.resolve_github_intent(
            "please handle this thing", _reg(), subject_kind="pr", project_path=None,
        ) is None
        assert called["n"] == 0

    def test_empty_text_returns_none(self):
        import app.github_intent as gi
        assert gi.resolve_github_intent("", _reg(), project_path="/tmp") is None
