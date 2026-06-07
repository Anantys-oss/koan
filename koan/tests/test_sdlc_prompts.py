"""Tests for the SDLC phase prompt corpus (koan/skills/core/sdlc/prompts/)."""

from pathlib import Path

import pytest

from app.prompts import load_skill_prompt

SKILL_DIR = Path(__file__).parent.parent / "skills" / "core" / "sdlc"

_REQUIRED_PROMPTS = [
    "orchestrator",
    "research",
    "architecture",
    "planning",
    "implementation",
    "security_review",
    "qa_review",
    "sre_review",
    "fix",
    "tech_writer",
]

_COMMON_KWARGS = {
    "ISSUE_NAME": "test-feature-123",
    "WORKSPACE_PATH": "/tmp/test-koan/sdlc/test-feature-123",
    "PROJECT_ROOT": "/tmp/test-project",
    "INSTANCE_DIR": "/tmp/test-koan",
    "ISSUE_URL": "https://github.com/test/repo/issues/123",
    "PROJECT_NAME": "test-project",
    "BASE_BRANCH": "main",
    "BRANCH_NAME": "koan/sdlc-test-feature-123",
    "BRANCH_PREFIX": "koan/",
    "FIX_ITERATION": "1",
    "MAX_FIX_ITERATIONS": "3",
    "ISSUE_DESCRIPTION": "Add a new feature that does X",
}


@pytest.fixture
def skill_dir() -> Path:
    return SKILL_DIR


class TestSdlcPromptsExist:
    def test_skill_dir_exists(self, skill_dir):
        assert skill_dir.exists(), f"SDLC skill directory not found: {skill_dir}"

    def test_skill_md_exists(self, skill_dir):
        assert (skill_dir / "SKILL.md").exists()

    def test_prompts_dir_exists(self, skill_dir):
        assert (skill_dir / "prompts").exists()

    @pytest.mark.parametrize("name", _REQUIRED_PROMPTS)
    def test_prompt_file_exists(self, skill_dir, name):
        prompt_file = skill_dir / "prompts" / f"{name}.md"
        assert prompt_file.exists(), f"Missing prompt file: {prompt_file}"


class TestSdlcSkillMd:
    def test_has_group_field(self, skill_dir):
        content = (skill_dir / "SKILL.md").read_text()
        assert "group:" in content, "SKILL.md must have a group: field"

    def test_group_is_code(self, skill_dir):
        content = (skill_dir / "SKILL.md").read_text()
        assert "group: code" in content

    def test_has_worker_true(self, skill_dir):
        content = (skill_dir / "SKILL.md").read_text()
        assert "worker: true" in content

    def test_has_github_enabled(self, skill_dir):
        content = (skill_dir / "SKILL.md").read_text()
        assert "github_enabled: true" in content


class TestSdlcPromptsLoad:
    @pytest.mark.parametrize("name", _REQUIRED_PROMPTS)
    def test_prompt_loads_without_error(self, skill_dir, name):
        text = load_skill_prompt(skill_dir, name, **_COMMON_KWARGS)
        assert len(text) > 100, f"{name}.md loaded but seems too short"

    @pytest.mark.parametrize("name", _REQUIRED_PROMPTS)
    def test_no_unresolved_includes(self, skill_dir, name):
        text = load_skill_prompt(skill_dir, name, **_COMMON_KWARGS)
        assert "{@include" not in text, f"{name}.md has unresolved @include directives"

    @pytest.mark.parametrize("name", _REQUIRED_PROMPTS)
    def test_substitution_applied(self, skill_dir, name):
        text = load_skill_prompt(skill_dir, name, **_COMMON_KWARGS)
        assert "test-feature-123" in text, f"{name}.md: ISSUE_NAME not substituted"
        assert "{ISSUE_NAME}" not in text, f"{name}.md: raw {{ISSUE_NAME}} placeholder remains"


class TestSdlcPromptContracts:
    """Verify each prompt names its output artifact — the contract the next phase depends on."""

    def test_research_names_output(self, skill_dir):
        text = load_skill_prompt(skill_dir, "research", **_COMMON_KWARGS)
        assert "RESEARCH.md" in text

    def test_architecture_reads_research(self, skill_dir):
        text = load_skill_prompt(skill_dir, "architecture", **_COMMON_KWARGS)
        assert "RESEARCH.md" in text
        assert "ADR.md" in text

    def test_planning_reads_both_artifacts(self, skill_dir):
        text = load_skill_prompt(skill_dir, "planning", **_COMMON_KWARGS)
        assert "RESEARCH.md" in text
        assert "ADR.md" in text
        assert "PLAN.md" in text

    def test_implementation_reads_plan(self, skill_dir):
        text = load_skill_prompt(skill_dir, "implementation", **_COMMON_KWARGS)
        assert "PLAN.md" in text
        assert "IMPLEMENTATION.md" in text

    def test_review_prompts_produce_verdict_block(self, skill_dir):
        for review_prompt in ("security_review", "qa_review", "sre_review"):
            text = load_skill_prompt(skill_dir, review_prompt, **_COMMON_KWARGS)
            assert "VERDICT: APPROVED" in text, f"{review_prompt}: missing VERDICT: APPROVED example"
            assert "VERDICT: NEEDS_FIX" in text, f"{review_prompt}: missing VERDICT: NEEDS_FIX example"

    def test_fix_reads_verdict_files(self, skill_dir):
        text = load_skill_prompt(skill_dir, "fix", **_COMMON_KWARGS)
        assert "SECURITY.md" in text
        assert "QA.md" in text
        assert "SRE.md" in text
        assert "NEEDS_FIX" in text

    def test_tech_writer_reads_implementation(self, skill_dir):
        text = load_skill_prompt(skill_dir, "tech_writer", **_COMMON_KWARGS)
        assert "IMPLEMENTATION.md" in text
        assert "DOCS.md" in text

    def test_review_prompts_cite_diff_only(self, skill_dir):
        """Review agents must constrain findings to the diff — not pre-existing code."""
        for review_prompt in ("security_review", "qa_review", "sre_review"):
            text = load_skill_prompt(skill_dir, review_prompt, **_COMMON_KWARGS)
            assert "diff" in text.lower(), f"{review_prompt}: no mention of diff-only scope"
