"""Tests for system prompt templates."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "system-prompts"


def test_submit_pr_prompt_has_tracker_issue_instructions():
    """Submit-pull-request template should use provider-neutral tracker issues."""
    prompt = (PROMPTS_DIR / "submit-pull-request.md").read_text()

    # Must have the audit section header
    assert "# Audit Missions" in prompt

    # Must use the issue tracker helper through Koan's current interpreter,
    # not bare python or direct gh issue commands.
    assert "{KOAN_PYTHON} -m app.issue_cli create" in prompt
    assert "python -m app.issue_cli create" not in prompt
    assert "gh issue create" not in prompt

    # Must have skip conditions (don't create issues for trivial findings)
    assert "Skip issue creation when" in prompt

    # Must mention configured tracker availability
    assert "configured issue tracker" in prompt


def test_agent_prompt_has_all_required_placeholders():
    """Agent prompt should have all required placeholders for prompt_builder substitution."""
    agent_prompt = (PROMPTS_DIR / "agent.md").read_text()

    required_placeholders = [
        "{INSTANCE}",
        "{PROJECT_NAME}",
        "{PROJECT_PATH}",
        "{RUN_NUM}",
        "{MAX_RUNS}",
        "{AUTONOMOUS_MODE}",
        "{FOCUS_AREA}",
        "{AVAILABLE_PCT}",
        "{MISSION_INSTRUCTION}",
        "{KOAN_PYTHON}",
    ]

    for placeholder in required_placeholders:
        assert placeholder in agent_prompt, f"Missing placeholder: {placeholder}"


def test_agent_prompt_has_branch_pr_notification_instructions():
    """Conclusion message should instruct agent to report branch name and PR link."""
    agent_prompt = (PROMPTS_DIR / "agent.md").read_text()

    # Must mention branch notification in conclusion section
    assert "branch name" in agent_prompt
    # Must mention PR link
    assert "PR link" in agent_prompt


def test_agent_prompt_conclusion_has_project_prefix():
    """Conclusion message instruction must include project name prefix after emoji."""
    agent_prompt = (PROMPTS_DIR / "agent.md").read_text()

    # The 🏁 instruction should include {PROJECT_NAME} prefix
    assert "🏁 [{PROJECT_NAME}]" in agent_prompt


def test_format_message_preserves_project_prefix():
    """Format-message prompt must instruct to preserve project prefixes."""
    prompt = (PROMPTS_DIR / "format-message.md").read_text()

    assert "project prefix" in prompt.lower()


def test_all_prompts_exist():
    """All referenced prompt files should exist."""
    expected_prompts = [
        "agent.md",
        "chat.md",
        "contemplative.md",
        "format-message.md",
        "pick-mission.md",
        "dashboard-chat.md",
        "morning-brief.md",
        "evening-debrief.md",
        "post-mission-reflection.md",
    ]

    for prompt_name in expected_prompts:
        prompt_path = PROMPTS_DIR / prompt_name
        assert prompt_path.exists(), f"Missing prompt: {prompt_name}"


def test_contemplative_prompt_has_required_placeholders():
    """Contemplative prompt should have all required placeholders."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    required_placeholders = [
        "{INSTANCE}",
        "{PROJECT_NAME}",
        "{SESSION_INFO}",
    ]

    for placeholder in required_placeholders:
        assert placeholder in prompt, f"Missing placeholder: {placeholder}"


def test_contemplative_prompt_has_reflection_topics():
    """Contemplative prompt should have structured reflection topics."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    # Must have the four topic categories
    assert "**Retrospective**" in prompt, "Missing Retrospective section"
    assert "**Relational**" in prompt, "Missing Relational section"
    assert "**Strategic**" in prompt, "Missing Strategic section"
    assert "**Philosophical**" in prompt, "Missing Philosophical section"


def test_contemplative_prompt_requires_output():
    """Contemplative prompt should require at least one output type."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    # Must have Required Output section
    assert "# Required Output" in prompt, "Missing Required Output section"

    # Must describe output options
    assert "Option 1: Learning" in prompt
    assert "Option 2: Mission Proposal" in prompt
    assert "Option 3: Question for the human" in prompt
    assert "Option 4: Kōan" in prompt

    # Must reference outbox for output delivery
    assert "outbox.md" in prompt


def test_contemplative_prompt_anti_noise_rules():
    """Contemplative prompt should have anti-noise guidance."""
    prompt = (PROMPTS_DIR / "contemplative.md").read_text()

    # Must discourage empty/generic output
    assert "silence" in prompt.lower() or "silent" in prompt.lower()
    assert "noise" in prompt.lower() or "generic" in prompt.lower()


def test_agent_prompt_uses_mktemp_not_fixed_tmp_names():
    """Agent prompt must not instruct fixed /tmp filenames.

    Fixed names (e.g. /tmp/issue.md) collide across users running Kōan on the
    same host. The prompt must use mktemp patterns so each run gets a unique
    file. See koan_tmp_dir() / the multi-instance temp-collision fix.
    """
    prompt = (PROMPTS_DIR / "agent.md").read_text()

    for fixed in ("/tmp/test-output.txt", "/tmp/issue.md", "/tmp/comment.md"):
        assert fixed not in prompt, f"Fixed /tmp path still present: {fixed}"

    assert "mktemp /tmp/koan-test-output-XXXXXX" in prompt
    assert "mktemp /tmp/koan-issue-XXXXXX" in prompt
    assert "mktemp /tmp/koan-comment-XXXXXX" in prompt
    _assert_mktemp_templates_are_bsd_portable(prompt)


def test_submit_pr_prompt_uses_mktemp_not_fixed_tmp_names():
    """Submit-PR prompt must use a mktemp pattern for the audit issue body."""
    prompt = (PROMPTS_DIR / "submit-pull-request.md").read_text()

    assert "/tmp/koan-audit-issue.md" not in prompt
    assert "mktemp /tmp/koan-audit-issue-XXXXXX" in prompt
    _assert_mktemp_templates_are_bsd_portable(prompt)


def _assert_mktemp_templates_are_bsd_portable(prompt: str):
    """Every `mktemp` template must end its X-run at the end of the path.

    BSD/macOS `mktemp` only substitutes a *trailing* run of X's; a suffix after
    the X's (e.g. `mktemp /tmp/foo-XXXXXX.md`) is taken literally, yielding a
    fixed filename and reintroducing the cross-user collision this fix removes.
    Guard against any `XXXXXX<suffix>` form.
    """
    import re

    for m in re.finditer(r"mktemp\s+(\S+)", prompt):
        # Strip trailing shell punctuation (the template often sits inside a
        # $(...) command substitution, so the token can end with `)`, `;`, etc.).
        template = m.group(1).rstrip(');"\'`')
        assert re.search(r"X+$", template), (
            f"mktemp template {template!r} does not end in X's — not BSD/macOS "
            "portable (suffix after X's is taken literally). Put the X-run last."
        )
