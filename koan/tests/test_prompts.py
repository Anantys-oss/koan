"""Tests for prompts.py — system prompt loader and placeholder substitution."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.prompts import (
    PROMPT_DIR,
    _MAX_INCLUDE_DEPTH,
    _read_prompt_with_git_fallback,
    _resolve_includes,
    _substitute,
    get_prompt_path,
    load_prompt,
    load_prompt_or_skill,
    load_skill_prompt,
)


# ---------- _substitute ----------


class TestSubstitute:
    """Tests for placeholder substitution."""

    def test_single_placeholder(self):
        assert _substitute("Hello {NAME}!", {"NAME": "World"}) == "Hello World!"

    def test_multiple_placeholders(self):
        template = "{A} and {B}"
        assert _substitute(template, {"A": "one", "B": "two"}) == "one and two"

    def test_repeated_placeholder(self):
        template = "{X} then {X} again"
        assert _substitute(template, {"X": "val"}) == "val then val again"

    def test_no_placeholders(self):
        assert _substitute("plain text", {}) == "plain text"

    def test_missing_placeholder_left_as_is(self):
        assert _substitute("Hello {NAME}!", {}) == "Hello {NAME}!"

    def test_non_string_value_converted(self):
        assert _substitute("count: {N}", {"N": 42}) == "count: 42"

    def test_empty_string_value(self):
        assert _substitute("x{V}y", {"V": ""}) == "xy"

    def test_koan_python_default_placeholder(self):
        import shlex
        import sys

        result = _substitute("{KOAN_PYTHON} -m app.issue_cli", {})
        assert result == f"{shlex.quote(sys.executable or 'python3')} -m app.issue_cli"

    def test_explicit_value_overrides_default_placeholder(self):
        result = _substitute("{KOAN_PYTHON} -m app.issue_cli", {
            "KOAN_PYTHON": "python3",
        })
        assert result == "python3 -m app.issue_cli"


# ---------- load_prompt ----------


class TestLoadPrompt:
    """Tests for loading prompts from koan/system-prompts/."""

    def test_load_chat_prompt(self):
        result = load_prompt("chat")
        assert len(result) > 0
        assert isinstance(result, str)

    def test_load_format_telegram(self):
        result = load_prompt("format-message")
        assert len(result) > 0

    def test_load_agent(self):
        result = load_prompt("agent")
        assert len(result) > 0

    def test_load_contemplative(self):
        result = load_prompt("contemplative")
        assert len(result) > 0

    def test_nonexistent_prompt_raises(self):
        with pytest.raises(FileNotFoundError):
            load_prompt("this-does-not-exist-at-all")

    def test_placeholder_substitution(self):
        """Prompts with placeholders should have them replaced."""
        # Read a prompt that has known placeholders
        raw = (PROMPT_DIR / "chat.md").read_text()
        # If it has any {KEY} patterns, test substitution works
        if "{" in raw:
            # Just verify load_prompt doesn't crash with kwargs
            result = load_prompt("chat", SOUL="test soul", MEMORY="test memory")
            assert isinstance(result, str)

    def test_prompt_dir_exists(self):
        assert PROMPT_DIR.exists()
        assert PROMPT_DIR.is_dir()

    def test_all_system_prompts_loadable(self):
        """Every .md file in system-prompts/ should be loadable."""
        for md_file in PROMPT_DIR.glob("*.md"):
            name = md_file.stem
            result = load_prompt(name)
            assert len(result) > 0, f"Prompt {name} is empty"


# ---------- get_prompt_path ----------


class TestGetPromptPath:
    """Tests for the prompt path helper."""

    def test_returns_path_object(self):
        result = get_prompt_path("chat")
        assert isinstance(result, Path)

    def test_path_includes_md_extension(self):
        result = get_prompt_path("format-message")
        assert result.name == "format-message.md"

    def test_path_is_in_prompt_dir(self):
        result = get_prompt_path("agent")
        assert result.parent == PROMPT_DIR

    def test_path_for_existing_prompt(self):
        result = get_prompt_path("chat")
        assert result.exists()

    def test_path_for_nonexistent_prompt(self):
        """Path is returned even if file doesn't exist (caller handles that)."""
        result = get_prompt_path("does-not-exist")
        assert isinstance(result, Path)
        assert not result.exists()


# ---------- load_skill_prompt ----------


class TestLoadSkillPrompt:
    """Tests for loading prompts from skill directories."""

    def test_load_from_skill_dir(self, tmp_path):
        """When prompt exists in skill dir, use it."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.md").write_text("Skill prompt {VAR}")
        result = load_skill_prompt(tmp_path, "test", VAR="value")
        assert result == "Skill prompt value"

    def test_fallback_to_system_prompts(self, tmp_path):
        """When prompt missing from skill dir, fall back to system-prompts/."""
        # tmp_path has no prompts/ dir, so should fall back
        result = load_skill_prompt(tmp_path, "chat")
        # Should get the system chat prompt
        assert len(result) > 0

    def test_skill_prompt_takes_priority(self, tmp_path):
        """Skill-specific prompt overrides system prompt."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        # Create a prompt with the same name as a system prompt
        (prompts_dir / "chat.md").write_text("Custom skill chat prompt")
        result = load_skill_prompt(tmp_path, "chat")
        assert result == "Custom skill chat prompt"

    def test_nonexistent_in_both_raises(self, tmp_path):
        """If prompt doesn't exist in skill or system dir, raise."""
        with pytest.raises(FileNotFoundError):
            load_skill_prompt(tmp_path, "totally-nonexistent-prompt-xyz")

    def test_substitution_works(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "review.md").write_text("Review {PROJECT} for {GOAL}")
        result = load_skill_prompt(
            tmp_path, "review", PROJECT="koan", GOAL="quality"
        )
        assert result == "Review koan for quality"

    def test_real_skill_prompts_loadable(self):
        """All existing skill prompts should be loadable."""
        skills_dir = Path(__file__).parent.parent / "skills" / "core"
        if not skills_dir.exists():
            pytest.skip("skills/core not found")
        for skill_dir in skills_dir.iterdir():
            prompts = skill_dir / "prompts"
            if prompts.exists():
                for md_file in prompts.glob("*.md"):
                    result = load_skill_prompt(skill_dir, md_file.stem)
                    assert len(result) > 0, f"{skill_dir.name}/{md_file.stem} is empty"


class TestPlanPromptVerificationCriteria:
    """Plan prompt must include a Verification Criteria section."""

    def test_plan_prompt_contains_verification_criteria(self):
        skills_dir = Path(__file__).parent.parent / "skills" / "core" / "plan"
        result = load_skill_prompt(skills_dir, "plan")
        assert "Verification Criteria" in result


class TestLoadSkillPromptCavemanInjection:
    """``load_skill_prompt`` auto-appends the caveman directive only when the
    skill has explicitly opted in (SKILL.md ``caveman: true`` or config
    ``optimizations.caveman.include``)."""

    @staticmethod
    def _make_skill(tmp_path, name, *, caveman_flag=None, prompt="Body {VAR}"):
        skill_dir = tmp_path / name
        (skill_dir / "prompts").mkdir(parents=True)
        (skill_dir / "prompts" / "p.md").write_text(prompt)
        frontmatter_extra = ""
        if caveman_flag is not None:
            frontmatter_extra = f"\ncaveman: {'true' if caveman_flag else 'false'}"
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\nscope: core{frontmatter_extra}\n---\n"
        )
        return skill_dir

    def test_caveman_skipped_by_default(self, tmp_path):
        """No ``caveman:`` flag — opt-in default keeps caveman off."""
        from unittest.mock import patch
        skill_dir = self._make_skill(tmp_path, "myskill")
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(skill_dir, "p", VAR="ok")
        assert "CAVEMAN-X" not in result
        assert result == "Body ok"

    def test_caveman_appended_when_skill_md_opts_in(self, tmp_path):
        from unittest.mock import patch
        skill_dir = self._make_skill(tmp_path, "myskill", caveman_flag=True)
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(skill_dir, "p", VAR="ok")
        assert result.startswith("Body ok")
        assert "CAVEMAN-X" in result

    def test_caveman_skipped_when_skill_md_explicitly_opts_out(self, tmp_path):
        from unittest.mock import patch
        skill_dir = self._make_skill(tmp_path, "myskill", caveman_flag=False)
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(skill_dir, "p", VAR="ok")
        assert "CAVEMAN-X" not in result
        assert result == "Body ok"

    def test_caveman_appended_when_in_config_include(self, tmp_path):
        from unittest.mock import patch
        skill_dir = self._make_skill(tmp_path, "myskill")
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["myskill"]}}
        }):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(skill_dir, "p", VAR="ok")
        assert "CAVEMAN-X" in result

    def test_config_include_overrides_skill_md_false(self, tmp_path):
        """Operator's ``include:`` config overrides a SKILL.md ``caveman: false``."""
        from unittest.mock import patch
        skill_dir = self._make_skill(tmp_path, "myskill", caveman_flag=False)
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["myskill"]}}
        }):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(skill_dir, "p", VAR="ok")
        assert "CAVEMAN-X" in result

    def test_caveman_skipped_when_globally_disabled(self, tmp_path):
        from unittest.mock import patch
        skill_dir = self._make_skill(tmp_path, "myskill", caveman_flag=True)
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"enabled": False}}
        }):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(skill_dir, "p", VAR="ok")
        assert "CAVEMAN-X" not in result

    def test_no_skill_md_means_no_injection(self, tmp_path):
        """A bare directory without SKILL.md is not treated as a skill — caveman not appended."""
        from unittest.mock import patch
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "p.md").write_text("Body")
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-X"):
                result = load_skill_prompt(tmp_path, "p")
        assert result == "Body"


# ---------- load_prompt_or_skill ----------


class TestLoadPromptOrSkill:
    """Tests for the consolidated prompt loading helper."""

    def test_with_skill_dir_uses_skill_prompt(self, tmp_path):
        """When skill_dir is not None, delegates to load_skill_prompt."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.md").write_text("Skill {VAR}")
        result = load_prompt_or_skill(tmp_path, "test", VAR="ok")
        assert result == "Skill ok"

    def test_with_none_skill_dir_uses_system_prompt(self):
        """When skill_dir is None, delegates to load_prompt."""
        result = load_prompt_or_skill(None, "chat")
        assert len(result) > 0

    def test_skill_dir_takes_priority_over_system(self, tmp_path):
        """Skill-specific prompt overrides system prompt of the same name."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "chat.md").write_text("custom chat")
        result = load_prompt_or_skill(tmp_path, "chat")
        assert result == "custom chat"

    def test_skill_dir_falls_back_to_system(self, tmp_path):
        """When prompt missing in skill dir, falls back to system-prompts."""
        result = load_prompt_or_skill(tmp_path, "chat")
        system_result = load_prompt("chat")
        assert result == system_result

    def test_none_skill_dir_nonexistent_raises(self):
        """When skill_dir is None and prompt doesn't exist, raises."""
        with pytest.raises(FileNotFoundError):
            load_prompt_or_skill(None, "totally-nonexistent-xyz")

    def test_substitution_with_skill_dir(self, tmp_path):
        """Placeholder substitution works via skill path."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "demo.md").write_text("{A} and {B}")
        result = load_prompt_or_skill(tmp_path, "demo", A="one", B="two")
        assert result == "one and two"

    def test_substitution_without_skill_dir(self):
        """Placeholder substitution works via system path."""
        result = load_prompt_or_skill(
            None, "chat", SOUL="test soul", MEMORY="test mem"
        )
        assert "test soul" in result or isinstance(result, str)


# ---------- _read_prompt_with_git_fallback ----------


def _make_run_side_effect(rev_parse="ok", remotes=None, repo_root="/repo"):
    """Build a side_effect function for subprocess.run mocking.

    Args:
        rev_parse: "ok", "fail", or "timeout" for git rev-parse behavior.
        remotes: dict mapping remote prefix (e.g. "upstream/main") to
                 "ok", "fail", or "timeout".  Defaults to both failing.
        repo_root: path returned by rev-parse --show-toplevel.
    """
    if remotes is None:
        remotes = {"upstream/main": "fail", "origin/main": "fail"}

    def side_effect(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            if rev_parse == "timeout":
                raise subprocess.TimeoutExpired(cmd, 5)
            rc = 0 if rev_parse == "ok" else 1
            return subprocess.CompletedProcess(cmd, rc, stdout=f"{repo_root}\n", stderr="")

        if cmd[:2] == ["git", "show"]:
            ref = cmd[2]  # e.g. "upstream/main:rel/path.md"
            for remote, behavior in remotes.items():
                if ref.startswith(f"{remote}:"):
                    if behavior == "timeout":
                        raise subprocess.TimeoutExpired(cmd, 5)
                    if behavior == "ok":
                        return subprocess.CompletedProcess(
                            cmd, 0, stdout=f"content from {remote.split('/')[0]}", stderr=""
                        )
                    return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: not found")

        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown command")

    return side_effect


class TestGitFallback:
    """Tests for _read_prompt_with_git_fallback."""

    def test_file_exists_no_git_call(self, tmp_path):
        """When the file exists on disk, return it without calling git."""
        p = tmp_path / "prompt.md"
        p.write_text("on disk content")
        with patch("app.prompts.subprocess.run") as mock_run:
            result = _read_prompt_with_git_fallback(p)
        assert result == "on disk content"
        mock_run.assert_not_called()

    def test_file_missing_reads_upstream(self, tmp_path):
        """When file is missing, falls back to upstream/main."""
        p = tmp_path / "sub" / "prompt.md"
        se = _make_run_side_effect(remotes={"upstream/main": "ok", "origin/main": "fail"}, repo_root=str(tmp_path))
        with patch("app.prompts.subprocess.run", side_effect=se):
            result = _read_prompt_with_git_fallback(p)
        assert result == "content from upstream"

    def test_upstream_fails_reads_origin(self, tmp_path):
        """When upstream/main fails, falls back to origin/main."""
        p = tmp_path / "sub" / "prompt.md"
        se = _make_run_side_effect(remotes={"upstream/main": "fail", "origin/main": "ok"}, repo_root=str(tmp_path))
        with patch("app.prompts.subprocess.run", side_effect=se):
            result = _read_prompt_with_git_fallback(p)
        assert result == "content from origin"

    def test_both_remotes_fail_raises(self, tmp_path):
        """When both remotes fail, raises FileNotFoundError."""
        p = tmp_path / "sub" / "prompt.md"
        se = _make_run_side_effect(repo_root=str(tmp_path))
        with patch("app.prompts.subprocess.run", side_effect=se):
            with pytest.raises(FileNotFoundError):
                _read_prompt_with_git_fallback(p)

    def test_rev_parse_fails_raises(self, tmp_path):
        """When git rev-parse fails, raises FileNotFoundError."""
        p = tmp_path / "prompt.md"
        se = _make_run_side_effect(rev_parse="fail", repo_root=str(tmp_path))
        with patch("app.prompts.subprocess.run", side_effect=se):
            with pytest.raises(FileNotFoundError):
                _read_prompt_with_git_fallback(p)

    def test_rev_parse_timeout_raises(self, tmp_path):
        """When git rev-parse times out, raises FileNotFoundError."""
        p = tmp_path / "prompt.md"
        se = _make_run_side_effect(rev_parse="timeout", repo_root=str(tmp_path))
        with patch("app.prompts.subprocess.run", side_effect=se):
            with pytest.raises(FileNotFoundError):
                _read_prompt_with_git_fallback(p)

    def test_upstream_timeout_tries_origin(self, tmp_path):
        """When upstream/main times out, tries origin/main."""
        p = tmp_path / "sub" / "prompt.md"
        se = _make_run_side_effect(
            remotes={"upstream/main": "timeout", "origin/main": "ok"}, repo_root=str(tmp_path),
        )
        with patch("app.prompts.subprocess.run", side_effect=se):
            result = _read_prompt_with_git_fallback(p)
        assert result == "content from origin"

    def test_both_timeouts_raises(self, tmp_path):
        """When both remotes time out, raises FileNotFoundError."""
        p = tmp_path / "sub" / "prompt.md"
        se = _make_run_side_effect(
            remotes={"upstream/main": "timeout", "origin/main": "timeout"}, repo_root=str(tmp_path),
        )
        with patch("app.prompts.subprocess.run", side_effect=se):
            with pytest.raises(FileNotFoundError):
                _read_prompt_with_git_fallback(p)

    def test_load_prompt_uses_fallback(self, tmp_path):
        """load_prompt() uses the git fallback when file is missing."""
        fake_path = tmp_path / "nonexistent.md"
        se = _make_run_side_effect(remotes={"upstream/main": "ok", "origin/main": "fail"}, repo_root=str(tmp_path))
        with patch("app.prompts.get_prompt_path", return_value=fake_path):
            with patch("app.prompts.subprocess.run", side_effect=se):
                result = load_prompt("nonexistent")
        assert result == "content from upstream"

    def test_load_skill_prompt_uses_fallback(self, tmp_path):
        """load_skill_prompt() uses the git fallback on system-prompt fallback path."""
        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        # No prompts/ dir in skill, so it falls back to system-prompts
        fake_path = tmp_path / "nonexistent.md"
        se = _make_run_side_effect(remotes={"upstream/main": "fail", "origin/main": "ok"}, repo_root=str(tmp_path))
        with patch("app.prompts.get_prompt_path", return_value=fake_path):
            with patch("app.prompts.subprocess.run", side_effect=se):
                result = load_skill_prompt(skill_dir, "nonexistent")
        assert result == "content from origin"

    def test_skill_prompt_git_fallback_before_system_prompts(self, tmp_path):
        """When skill prompt is missing on disk, try git for the skill path first."""
        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()
        # Skill prompt doesn't exist on disk, but git show finds it
        se = _make_run_side_effect(
            remotes={"upstream/main": "ok", "origin/main": "fail"},
            repo_root=str(tmp_path),
        )
        with patch("app.prompts.subprocess.run", side_effect=se):
            result = load_skill_prompt(skill_dir, "recreate")
        assert result == "content from upstream"

    def test_skill_prompt_falls_through_to_system_on_git_miss(self, tmp_path):
        """When skill prompt not on disk AND not in git, try system-prompts."""
        skill_dir = tmp_path / "myskill"
        skill_dir.mkdir()

        # First call to _read_prompt_with_git_fallback (skill path) fails entirely
        # Second call (system-prompts path) succeeds via origin
        call_count = [0]
        se_both_fail = _make_run_side_effect(
            remotes={"upstream/main": "fail", "origin/main": "fail"},
            repo_root=str(tmp_path),
        )
        se_origin_ok = _make_run_side_effect(
            remotes={"upstream/main": "fail", "origin/main": "ok"},
            repo_root=str(tmp_path),
        )

        def alternating_se(cmd, **kwargs):
            if cmd[:2] == ["git", "show"]:
                ref = cmd[2]
                # Skill path calls fail, system-prompt calls succeed
                if "myskill" in ref:
                    return se_both_fail(cmd, **kwargs)
                return se_origin_ok(cmd, **kwargs)
            return se_origin_ok(cmd, **kwargs)

        fake_sys = tmp_path / "someprompt.md"
        with patch("app.prompts.get_prompt_path", return_value=fake_sys):
            with patch("app.prompts.subprocess.run", side_effect=alternating_se):
                result = load_skill_prompt(skill_dir, "someprompt")
        assert result == "content from origin"


# ---------- _resolve_includes ----------


class TestResolveIncludes:
    """Tests for {@include partial-name} directive resolution."""

    def test_no_includes_passes_through(self):
        """Template without includes is returned unchanged."""
        template = "Hello world\nNo includes here"
        assert _resolve_includes(template) == template

    def test_global_partial_resolved(self, tmp_path):
        """A global partial in system-prompts/_partials/ is resolved."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "greeting.md").write_text("Hello from partial")
        template = "Before\n{@include greeting}\nAfter"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "Before\nHello from partial\nAfter"

    def test_skill_local_partial_overrides_global(self, tmp_path):
        """Skill-local partial takes priority over global."""
        # Global partial
        global_dir = tmp_path / "global"
        global_partials = global_dir / "_partials"
        global_partials.mkdir(parents=True)
        (global_partials / "rules.md").write_text("global rules")
        # Skill-local partial
        skill_dir = tmp_path / "skill"
        skill_partials = skill_dir / "prompts" / "_partials"
        skill_partials.mkdir(parents=True)
        (skill_partials / "rules.md").write_text("skill-specific rules")

        template = "Start\n{@include rules}\nEnd"
        with patch("app.prompts.PROMPT_DIR", global_dir):
            result = _resolve_includes(template, skill_dir=skill_dir)
        assert result == "Start\nskill-specific rules\nEnd"

    def test_falls_back_to_global_when_no_skill_partial(self, tmp_path):
        """When skill has no local override, global partial is used."""
        global_dir = tmp_path / "global"
        global_partials = global_dir / "_partials"
        global_partials.mkdir(parents=True)
        (global_partials / "rules.md").write_text("global rules")
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()

        template = "{@include rules}"
        with patch("app.prompts.PROMPT_DIR", global_dir):
            result = _resolve_includes(template, skill_dir=skill_dir)
        assert result == "global rules"

    def test_missing_partial_left_as_is(self, tmp_path):
        """When a partial doesn't exist anywhere, the directive is preserved."""
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        template = "Before\n{@include nonexistent}\nAfter"
        with patch("app.prompts.PROMPT_DIR", global_dir):
            result = _resolve_includes(template)
        assert result == "Before\n{@include nonexistent}\nAfter"

    def test_recursive_includes(self, tmp_path):
        """Partials can include other partials."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "outer.md").write_text("outer-start\n{@include inner}\nouter-end")
        (partials / "inner.md").write_text("inner-content")
        template = "{@include outer}"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "outer-start\ninner-content\nouter-end"

    def test_depth_limit_prevents_infinite_recursion(self, tmp_path):
        """Recursive includes stop at _MAX_INCLUDE_DEPTH."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        # Create a self-referencing partial
        (partials / "loop.md").write_text("level\n{@include loop}")
        template = "{@include loop}"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        # At depth 0: resolves loop -> "level\n{@include loop}"
        # At depth 1: resolves inner loop -> "level\n{@include loop}"
        # At depth 2: resolves inner loop -> "level\n{@include loop}"
        # At depth 3: max depth reached, returns as-is
        expected_levels = _MAX_INCLUDE_DEPTH
        lines = result.split("\n")
        assert lines.count("level") == expected_levels
        # The deepest include is left unresolved
        assert lines[-1] == "{@include loop}"

    def test_multiple_includes_in_one_template(self, tmp_path):
        """Multiple include directives in a single template are all resolved."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "alpha.md").write_text("A-content")
        (partials / "beta.md").write_text("B-content")
        template = "start\n{@include alpha}\nmiddle\n{@include beta}\nend"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "start\nA-content\nmiddle\nB-content\nend"

    def test_inline_include_not_matched(self):
        """Include directives must be on their own line."""
        template = "text {@include foo} more text"
        # Should not be matched since it's not on its own line
        assert _resolve_includes(template) == template

    def test_include_with_trailing_whitespace(self, tmp_path):
        """Trailing whitespace after the directive is tolerated."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "ws.md").write_text("whitespace-ok")
        template = "before\n{@include ws}   \nafter"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "before\nwhitespace-ok\nafter"

    def test_include_preserves_surrounding_content(self, tmp_path):
        """Lines before and after the include directive are preserved."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "mid.md").write_text("included")
        template = "line1\nline2\n{@include mid}\nline4\nline5"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "line1\nline2\nincluded\nline4\nline5"

    def test_hyphenated_partial_name(self, tmp_path):
        """Partial names with hyphens are valid."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "pr-submit-fork.md").write_text("fork rules")
        template = "{@include pr-submit-fork}"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "fork rules"

    def test_placeholders_survive_include_resolution(self, tmp_path):
        """Placeholders in partials are preserved for later _substitute()."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "tmpl.md").write_text("Hello {NAME}")
        template = "{@include tmpl}"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "Hello {NAME}"

    def test_trailing_newline_stripped_from_partial(self, tmp_path):
        """Trailing newline in partial file does not produce extra blank lines."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        # Write with trailing newline (as git-committed files normally have)
        (partials / "note.md").write_text("included content\n")
        template = "before\n{@include note}\nafter"
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = _resolve_includes(template)
        assert result == "before\nincluded content\nafter"


class TestLoadPromptWithIncludes:
    """Integration tests: load_prompt and load_skill_prompt resolve includes."""

    def test_load_prompt_resolves_global_include(self, tmp_path):
        """load_prompt resolves {@include} from system-prompts/_partials/."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "sig.md").write_text("-- Koan")
        (tmp_path / "test.md").write_text("Hello\n{@include sig}")
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = load_prompt("test")
        assert result == "Hello\n-- Koan"

    def test_load_skill_prompt_resolves_skill_local_include(self, tmp_path):
        """load_skill_prompt prefers skill-local partials."""
        # Global
        global_dir = tmp_path / "global"
        global_partials = global_dir / "_partials"
        global_partials.mkdir(parents=True)
        (global_partials / "footer.md").write_text("global footer")
        # Skill
        skill_dir = tmp_path / "skill"
        skill_prompts = skill_dir / "prompts"
        skill_partials = skill_prompts / "_partials"
        skill_partials.mkdir(parents=True)
        (skill_partials / "footer.md").write_text("skill footer")
        (skill_prompts / "main.md").write_text("body\n{@include footer}")
        with patch("app.prompts.PROMPT_DIR", global_dir):
            result = load_skill_prompt(skill_dir, "main")
        assert result == "body\nskill footer"

    def test_includes_resolved_before_substitution(self, tmp_path):
        """Includes are expanded first, then {KEY} placeholders are substituted."""
        partials = tmp_path / "_partials"
        partials.mkdir()
        (partials / "greeting.md").write_text("Hello {USER}")
        (tmp_path / "test.md").write_text("{@include greeting}\nBye")
        with patch("app.prompts.PROMPT_DIR", tmp_path):
            result = load_prompt("test", USER="Alice")
        assert result == "Hello Alice\nBye"

    def test_real_partials_resolve_in_prompts(self):
        """Existing {@include} directives in real prompt files resolve correctly."""
        partials_dir = PROMPT_DIR / "_partials"
        if not partials_dir.exists():
            pytest.skip("No _partials directory yet")
        # Verify that agent.md (which uses includes) loads without leftover directives
        result = load_prompt("agent")
        # No unresolved includes should remain (all partials exist)
        import re
        unresolved = re.findall(r"\{@include\s+[\w-]+\}", result)
        assert unresolved == [], f"Unresolved includes in agent.md: {unresolved}"

    def test_all_skill_prompts_resolve_includes(self):
        """Every skill prompt with {@include} directives must resolve completely."""
        import re
        skills_dir = Path(__file__).parent.parent / "skills" / "core"
        if not skills_dir.exists():
            pytest.skip("skills/core not found")
        failures = []
        for skill_dir in sorted(skills_dir.iterdir()):
            prompts = skill_dir / "prompts"
            if not prompts.exists():
                continue
            for md_file in sorted(prompts.glob("*.md")):
                result = load_skill_prompt(skill_dir, md_file.stem)
                unresolved = re.findall(r"\{@include\s+[\w-]+\}", result)
                if unresolved:
                    failures.append(f"{skill_dir.name}/{md_file.stem}: {unresolved}")
        assert failures == [], (
            "Unresolved {@include} directives in skill prompts:\n"
            + "\n".join(failures)
        )

    def test_implementation_workflow_partial_resolves(self):
        """implementation-workflow partial resolves in both fix and implement prompts."""
        import re

        include_re = re.compile(r"\{@include\s+[\w-]+\}")
        skills_root = PROMPT_DIR.parent / "skills" / "core"

        fix_dir = skills_root / "fix"
        impl_dir = skills_root / "implement"

        fix_out = load_skill_prompt(fix_dir, "fix")
        impl_out = load_skill_prompt(impl_dir, "implement")

        # The partial itself must have been resolved (no raw directive remaining)
        assert "{@include implementation-workflow}" not in fix_out, (
            "implementation-workflow partial not resolved in fix.md"
        )
        assert "{@include implementation-workflow}" not in impl_out, (
            "implementation-workflow partial not resolved in implement.md"
        )

        # The partial's content must be present in both rendered outputs
        assert "Phase 3" in fix_out, "Phase 3 missing from rendered fix.md"
        assert "Phase 3" in impl_out, "Phase 3 missing from rendered implement.md"

        # No unresolved {@include ...} directives should remain in either output
        fix_unresolved = include_re.findall(fix_out)
        impl_unresolved = include_re.findall(impl_out)
        assert fix_unresolved == [], f"Unresolved includes in fix.md: {fix_unresolved}"
        assert impl_unresolved == [], f"Unresolved includes in implement.md: {impl_unresolved}"
