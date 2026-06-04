"""Tests for the caveman per-skill opt-in helpers.

Covers:
- ``app.config.is_caveman_mode`` and ``get_caveman_include_list`` reading
  the nested ``optimizations.caveman.{enabled, include}`` mapping.
- ``app.caveman.is_skill_included`` for SKILL.md frontmatter and config
  inclusion (including alias resolution).
- ``app.caveman.get_caveman_section`` / ``append_caveman`` end-to-end.
- Config validator deep-validation, including rejection of the deprecated
  scalar bool form.
- Default core skills shipping with the right opt-in / opt-out flags.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Config layer
# ---------------------------------------------------------------------------


class TestIsCavemanModeNested:
    """``is_caveman_mode`` reads the nested ``optimizations.caveman.enabled``."""

    def test_default_when_no_config(self):
        from app.config import is_caveman_mode
        with patch("app.config._load_config", return_value={}):
            assert is_caveman_mode() is True

    def test_scalar_bool_form_falls_back_to_default(self):
        """The pre-release bool shorthand is no longer honored at runtime —
        ``is_caveman_mode`` falls back to the default (True) when the value
        is not a mapping.  The validator surfaces the misshapen config
        separately (see :class:`TestValidatorNestedCaveman`)."""
        from app.config import is_caveman_mode
        with patch("app.config._load_config",
                   return_value={"optimizations": {"caveman": False}}):
            assert is_caveman_mode() is True

    def test_nested_enabled_true(self):
        from app.config import is_caveman_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"enabled": True, "include": []}}
        }):
            assert is_caveman_mode() is True

    def test_nested_enabled_false(self):
        from app.config import is_caveman_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"enabled": False}}
        }):
            assert is_caveman_mode() is False

    def test_nested_missing_enabled_defaults_true(self):
        from app.config import is_caveman_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase"]}}
        }):
            assert is_caveman_mode() is True

    def test_nested_garbage_enabled_defaults_true(self):
        """Non-bool ``enabled`` should not silently disable caveman."""
        from app.config import is_caveman_mode
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"enabled": "yes"}}
        }):
            assert is_caveman_mode() is True

    def test_optimizations_not_dict(self):
        from app.config import is_caveman_mode
        with patch("app.config._load_config",
                   return_value={"optimizations": "garbage"}):
            assert is_caveman_mode() is True


class TestGetCavemanIncludeList:
    """``get_caveman_include_list`` returns canonical names with aliases resolved."""

    def test_empty_when_no_config(self):
        from app.config import get_caveman_include_list
        with patch("app.config._load_config", return_value={}):
            assert get_caveman_include_list() == set()

    def test_empty_when_scalar_caveman(self):
        """Scalar bool at ``optimizations.caveman`` is misshapen — the
        include list resolves to an empty set so callers degrade safely."""
        from app.config import get_caveman_include_list
        with patch("app.config._load_config",
                   return_value={"optimizations": {"caveman": True}}):
            assert get_caveman_include_list() == set()

    def test_returns_canonical_names(self):
        from app.config import get_caveman_include_list
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase", "fix"]}}
        }):
            assert get_caveman_include_list() == {"rebase", "fix"}

    def test_aliases_resolved_to_canonical(self):
        """``rb`` is an alias for ``rebase``-style commands; ``secu`` resolves to ``security_audit``."""
        from app.config import get_caveman_include_list
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["deeplan", "secu"]}}
        }):
            result = get_caveman_include_list()
            assert "deepplan" in result
            assert "security_audit" in result
            assert "deeplan" not in result
            assert "secu" not in result

    def test_strips_leading_slash(self):
        from app.config import get_caveman_include_list
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["/rebase", " fix "]}}
        }):
            assert get_caveman_include_list() == {"rebase", "fix"}

    def test_non_string_entries_skipped(self):
        from app.config import get_caveman_include_list
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase", 42, None, ""]}}
        }):
            assert get_caveman_include_list() == {"rebase"}

    def test_include_not_a_list(self):
        from app.config import get_caveman_include_list
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": "rebase"}}
        }):
            assert get_caveman_include_list() == set()


# ---------------------------------------------------------------------------
# caveman.is_skill_included — SKILL.md + config interaction
# ---------------------------------------------------------------------------


def _write_skill_md(path: Path, body: str) -> Path:
    """Write a minimal SKILL.md and return the skill directory."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(textwrap.dedent(body).strip() + "\n")
    return path


class TestIsSkillIncluded:
    """Skill inclusion respects SKILL.md frontmatter and the config include list."""

    def test_default_not_included(self):
        """No SKILL.md, no config — skill is opt-in by default."""
        from app.caveman import is_skill_included
        with patch("app.config._load_config", return_value={}):
            assert is_skill_included("rebase") is False

    def test_included_via_config_canonical(self):
        from app.caveman import is_skill_included
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase"]}}
        }):
            assert is_skill_included("rebase") is True
            assert is_skill_included("plan") is False

    def test_included_via_config_alias(self):
        """``deeplan`` matches the canonical ``deepplan`` include entry."""
        from app.caveman import is_skill_included
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["deepplan"]}}
        }):
            assert is_skill_included("deeplan") is True
            assert is_skill_included("deepplan") is True

    def test_included_via_skill_md(self, tmp_path):
        from app.caveman import is_skill_included
        skill_dir = _write_skill_md(tmp_path / "ops" / "myskill", """
            ---
            name: myskill
            scope: ops
            caveman: true
            ---
        """)
        with patch("app.config._load_config", return_value={}):
            assert is_skill_included("myskill", skill_dir=skill_dir) is True

    def test_skill_md_default_caveman_false(self, tmp_path):
        """Absent ``caveman:`` in frontmatter means opt-in default — caveman does not apply."""
        from app.caveman import is_skill_included
        skill_dir = _write_skill_md(tmp_path / "ops" / "myskill", """
            ---
            name: myskill
            scope: ops
            ---
        """)
        with patch("app.config._load_config", return_value={}):
            assert is_skill_included("myskill", skill_dir=skill_dir) is False

    def test_skill_md_explicit_caveman_false(self, tmp_path):
        from app.caveman import is_skill_included
        skill_dir = _write_skill_md(tmp_path / "ops" / "myskill", """
            ---
            name: myskill
            scope: ops
            caveman: false
            ---
        """)
        with patch("app.config._load_config", return_value={}):
            assert is_skill_included("myskill", skill_dir=skill_dir) is False

    def test_config_include_overrides_skill_md_false(self, tmp_path):
        """Operator's ``include:`` config wins over a SKILL.md ``caveman: false``."""
        from app.caveman import is_skill_included
        skill_dir = _write_skill_md(tmp_path / "ops" / "thing", """
            ---
            name: thing
            scope: ops
            caveman: false
            ---
        """)
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["thing"]}}
        }):
            assert is_skill_included("thing", skill_dir=skill_dir) is True

    def test_skill_md_true_alone_includes(self, tmp_path):
        from app.caveman import is_skill_included
        skill_dir = _write_skill_md(tmp_path / "ops" / "ponder", """
            ---
            name: ponder
            scope: ops
            caveman: true
            ---
        """)
        with patch("app.config._load_config", return_value={}):
            assert is_skill_included("ponder", skill_dir=skill_dir) is True


# ---------------------------------------------------------------------------
# caveman.get_caveman_section / append_caveman
# ---------------------------------------------------------------------------


class TestGetCavemanSection:
    """Returns directive when applicable, empty string otherwise."""

    def test_agent_loop_returns_directive_by_default(self):
        """No skill_name + no skill_dir = agent loop, gated only by global flag."""
        from app.caveman import get_caveman_section
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-MARKER"):
                assert get_caveman_section() == "CAVEMAN-MARKER"

    def test_agent_loop_empty_when_globally_disabled(self):
        from app.caveman import get_caveman_section
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"enabled": False}}
        }):
            assert get_caveman_section() == ""

    def test_skill_context_default_empty(self):
        """Skill context with no opt-in returns empty (opt-in default)."""
        from app.caveman import get_caveman_section
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-MARKER"):
                assert get_caveman_section(skill_name="rebase") == ""

    def test_skill_context_returns_directive_when_opted_in(self):
        from app.caveman import get_caveman_section
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase"]}}
        }):
            with patch("app.prompts.load_prompt", return_value="CAVEMAN-MARKER"):
                assert get_caveman_section(skill_name="rebase") == "CAVEMAN-MARKER"

    def test_swallows_load_prompt_failure(self):
        from app.caveman import get_caveman_section
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt",
                       side_effect=FileNotFoundError("missing")):
                assert get_caveman_section() == ""


class TestAppendCaveman:
    """``append_caveman`` is a no-op when the section is empty, otherwise concatenates."""

    def test_no_change_when_disabled(self):
        from app.caveman import append_caveman
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"enabled": False}}
        }):
            assert append_caveman("base prompt", skill_name="rebase") == "base prompt"

    def test_no_change_when_skill_not_opted_in(self):
        from app.caveman import append_caveman
        with patch("app.config._load_config", return_value={}):
            with patch("app.prompts.load_prompt", return_value="X"):
                # No SKILL.md, no config include — skill stays opt-out.
                assert append_caveman("base prompt", skill_name="rebase") == "base prompt"

    def test_concatenates_with_blank_line_when_opted_in(self):
        from app.caveman import append_caveman
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase"]}}
        }):
            with patch("app.prompts.load_prompt", return_value="X"):
                result = append_caveman("base prompt", skill_name="rebase")
                assert result == "base prompt\n\nX"

    def test_no_double_newline_when_prompt_already_ends_with_newline(self):
        from app.caveman import append_caveman
        with patch("app.config._load_config", return_value={
            "optimizations": {"caveman": {"include": ["rebase"]}}
        }):
            with patch("app.prompts.load_prompt", return_value="X"):
                result = append_caveman("base prompt\n", skill_name="rebase")
                assert result == "base prompt\nX"


# ---------------------------------------------------------------------------
# Skill registry exposes caveman_enabled
# ---------------------------------------------------------------------------


class TestSkillCavemanFrontmatter:
    """``Skill.caveman_enabled`` reflects the SKILL.md frontmatter flag."""

    def test_default_false_when_absent(self, tmp_path):
        from app.skills import parse_skill_md
        path = tmp_path / "SKILL.md"
        path.write_text(textwrap.dedent("""
            ---
            name: foo
            scope: bar
            ---
        """).strip() + "\n")
        skill = parse_skill_md(path)
        assert skill is not None
        assert skill.caveman_enabled is False

    def test_false_when_explicit(self, tmp_path):
        from app.skills import parse_skill_md
        path = tmp_path / "SKILL.md"
        path.write_text(textwrap.dedent("""
            ---
            name: foo
            scope: bar
            caveman: false
            ---
        """).strip() + "\n")
        skill = parse_skill_md(path)
        assert skill is not None
        assert skill.caveman_enabled is False

    def test_true_when_explicit(self, tmp_path):
        from app.skills import parse_skill_md
        path = tmp_path / "SKILL.md"
        path.write_text(textwrap.dedent("""
            ---
            name: foo
            scope: bar
            caveman: true
            ---
        """).strip() + "\n")
        skill = parse_skill_md(path)
        assert skill is not None
        assert skill.caveman_enabled is True


class TestCoreSkillsShipDefaults:
    """Core skills ship with the expected caveman flag for opt-in semantics."""

    @pytest.mark.parametrize("skill_name", [
        "plan", "deepplan", "security_audit", "audit",
        "brainstorm", "sparring", "incident", "claudemd", "chat",
    ])
    def test_context_rich_skills_ship_caveman_false(self, skill_name):
        """Context-rich skills keep the explicit ``caveman: false`` marker."""
        from app.skills import parse_skill_md
        skill_md = (
            Path(__file__).resolve().parent.parent
            / "skills" / "core" / skill_name / "SKILL.md"
        )
        assert skill_md.exists(), f"{skill_md} missing"
        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.caveman_enabled is False, (
            f"core skill {skill_name} should ship with caveman: false"
        )

    @pytest.mark.parametrize("skill_name", [
        "rebase", "recreate", "squash", "fix", "ci_check", "check", "implement",
        "review",
    ])
    def test_terse_skills_ship_caveman_true(self, skill_name):
        """Terse-output skills opt in with ``caveman: true``."""
        from app.skills import parse_skill_md
        skill_md = (
            Path(__file__).resolve().parent.parent
            / "skills" / "core" / skill_name / "SKILL.md"
        )
        assert skill_md.exists(), f"{skill_md} missing"
        skill = parse_skill_md(skill_md)
        assert skill is not None
        assert skill.caveman_enabled is True, (
            f"core skill {skill_name} should ship with caveman: true"
        )


# ---------------------------------------------------------------------------
# Config validator — nested form
# ---------------------------------------------------------------------------


class TestValidatorNestedCaveman:
    """The validator requires the nested mapping and flags bad shapes."""

    def test_scalar_bool_form_warns(self):
        """The pre-release scalar ``caveman: true`` shorthand is rejected —
        only the nested ``caveman: {enabled, include}`` mapping is accepted."""
        from app.config_validator import validate_config
        warnings = validate_config({"optimizations": {"caveman": True}})
        bad = [w for w in warnings if w[0] == "optimizations.caveman"]
        assert bad, f"expected warning for scalar bool, got {warnings}"

    def test_nested_form_passes(self):
        from app.config_validator import validate_config
        warnings = validate_config({
            "optimizations": {"caveman": {"enabled": True, "include": ["rebase"]}}
        })
        assert not [w for w in warnings if "caveman" in w[0]]

    def test_unrecognized_nested_key_warns(self):
        from app.config_validator import validate_config
        warnings = validate_config({
            "optimizations": {"caveman": {"enabledddd": False}}
        })
        bad = [w for w in warnings if w[0] == "optimizations.caveman.enabledddd"]
        assert bad, f"expected warning for typo, got {warnings}"

    def test_legacy_exclude_key_warns(self):
        """The pre-release ``exclude`` key was renamed to ``include``."""
        from app.config_validator import validate_config
        warnings = validate_config({
            "optimizations": {"caveman": {"exclude": ["rebase"]}}
        })
        bad = [w for w in warnings if w[0] == "optimizations.caveman.exclude"]
        assert bad

    def test_wrong_type_for_enabled_warns(self):
        from app.config_validator import validate_config
        warnings = validate_config({
            "optimizations": {"caveman": {"enabled": "yes"}}
        })
        bad = [w for w in warnings if w[0] == "optimizations.caveman.enabled"]
        assert bad

    def test_wrong_type_for_include_warns(self):
        from app.config_validator import validate_config
        warnings = validate_config({
            "optimizations": {"caveman": {"include": "rebase"}}
        })
        bad = [w for w in warnings if w[0] == "optimizations.caveman.include"]
        assert bad

    def test_non_string_entry_in_include_warns(self):
        from app.config_validator import validate_config
        warnings = validate_config({
            "optimizations": {"caveman": {"include": ["rebase", 42]}}
        })
        bad = [w for w in warnings if w[0].startswith("optimizations.caveman.include[")]
        assert bad
