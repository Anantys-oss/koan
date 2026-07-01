"""Behavioral tests for the native /speckit skill orchestration helpers.

Covers the code-enforced primitives in app.speckit_orchestration and the
config accessor in app.config. These assert observable behavior (gates fire,
tokens parsed, mission queued/deduped, progress written) — never source text.

Run with:  KOAN_ROOT=/tmp/test-koan .venv/bin/pytest koan/tests/test_speckit_skill.py -v
"""

from app import config
from app.speckit_orchestration import (
    build_mission_entry,
    constitution_path_for,
    emit_progress,
    extract_overrides,
    has_constitution,
    queue_mission,
)
from app.skills import SkillContext


# --- config accessor (T003) ---------------------------------------------------

def test_get_speckit_config_defaults(monkeypatch):
    monkeypatch.setattr(config, "_load_config", lambda: {})
    cfg = config.get_speckit_config()
    assert cfg == {
        "quota_threshold": 15,
        "review_max_iterations": 3,
        "review_severity": "important",
    }


def test_get_speckit_config_honors_values(monkeypatch):
    monkeypatch.setattr(
        config,
        "_load_config",
        lambda: {"speckit": {"quota_threshold": 25, "review_max_iterations": 5, "review_severity": "high"}},
    )
    cfg = config.get_speckit_config()
    assert cfg == {"quota_threshold": 25, "review_max_iterations": 5, "review_severity": "high"}


def test_get_speckit_config_coerces_and_clamps(monkeypatch):
    monkeypatch.setattr(
        config,
        "_load_config",
        lambda: {"speckit": {"quota_threshold": "200", "review_max_iterations": "bad", "review_severity": "  "}},
    )
    cfg = config.get_speckit_config()
    assert cfg["quota_threshold"] == 100          # clamped to 0-100
    assert cfg["review_max_iterations"] == 3      # non-int -> default
    assert cfg["review_severity"] == "important"  # blank -> default


def test_get_speckit_config_non_dict_section(monkeypatch):
    monkeypatch.setattr(config, "_load_config", lambda: {"speckit": "not a dict"})
    assert config.get_speckit_config()["quota_threshold"] == 15


# --- constitution gate (T004) -------------------------------------------------

def test_has_constitution_false_then_true(tmp_path):
    assert has_constitution(tmp_path) is False
    cpath = constitution_path_for(tmp_path)
    cpath.parent.mkdir(parents=True)
    cpath.write_text("# constitution\n", encoding="utf-8")
    assert has_constitution(tmp_path) is True


def test_constitution_path_is_under_specify_memory(tmp_path):
    assert constitution_path_for(tmp_path) == tmp_path / ".specify" / "memory" / "constitution.md"


# --- override token parsing (T005) -------------------------------------------

def test_extract_overrides_parses_and_strips():
    repo, branch, cleaned = extract_overrides("add CSV export repo:myrepo branch:mybranch")
    assert repo == "myrepo"
    assert branch == "mybranch"
    assert cleaned == "add CSV export"


def test_extract_overrides_absent_tokens():
    repo, branch, cleaned = extract_overrides("just a plain goal")
    assert repo is None
    assert branch is None
    assert cleaned == "just a plain goal"


def test_extract_overrides_collapses_whitespace():
    _, _, cleaned = extract_overrides("a    b     c")
    assert cleaned == "a b c"


# --- mission entry + queuing (T006) ------------------------------------------

def test_build_mission_entry_with_project_tag():
    assert (
        build_mission_entry("speckit", "myproject", "add CSV export")
        == "- [project:myproject] /speckit add CSV export"
    )


def test_build_mission_entry_without_project_tag():
    assert build_mission_entry("speckit", "", "add CSV export") == "- /speckit add CSV export"


def test_build_mission_entry_empty_goal():
    assert build_mission_entry("speckit", "p", "   ") == "- [project:p] /speckit"


def test_build_mission_entry_collapses_multiline_goal():
    # A multiline goal must not break the single-line missions.md entry format.
    entry = build_mission_entry("speckit", "p", "add CSV export\nwith headers\tand quoting")
    assert "\n" not in entry
    assert entry == "- [project:p] /speckit add CSV export with headers and quoting"


def test_queue_mission_inserts_entry(tmp_path):
    inserted = queue_mission(tmp_path, "speckit", "myproject", "add CSV export")
    assert inserted is True
    content = (tmp_path / "missions.md").read_text(encoding="utf-8")
    assert "/speckit add CSV export" in content
    assert "[project:myproject]" in content
    # Dedup is URL/issue-scoped (matches /implement): a free-text chat goal has
    # no URL, so a second identical send is NOT deduped — it queues again. This
    # mirrors the spec's queueing model (dedup applies only to an issue that
    # already has an open PR). Verified here so the behavior is pinned.
    assert queue_mission(tmp_path, "speckit", "myproject", "add CSV export") is True


# --- progress notes (T006 / FR-018) ------------------------------------------

def test_emit_progress_writes_line(tmp_path):
    emit_progress(tmp_path, "specify step complete")
    text = (tmp_path / "outbox.md").read_text(encoding="utf-8")
    assert "specify step complete" in text


# --- handler gate logic (US1) -------------------------------------------------

def _ctx(args, tmp_path):
    return SkillContext(
        koan_root=tmp_path, instance_dir=tmp_path, command_name="speckit", args=args,
    )


def test_handler_usage_on_empty_args(tmp_path):
    from skills.core.speckit.handler import handle

    assert "Usage" in handle(_ctx("", tmp_path))


def test_handler_unknown_project(tmp_path, monkeypatch):
    import app.speckit_orchestration as orch

    monkeypatch.setattr(orch, "resolve_target", lambda arg: (None, None))
    from skills.core.speckit.handler import handle

    assert "Unknown project" in handle(_ctx("ghostproject add X", tmp_path))


def test_handler_aborts_without_constitution(tmp_path, monkeypatch):
    import app.speckit_orchestration as orch

    monkeypatch.setattr(orch, "resolve_target", lambda arg: (str(tmp_path), "myproject"))
    monkeypatch.setattr(orch, "has_constitution", lambda path: False)
    queued = []
    monkeypatch.setattr(orch, "queue_mission", lambda *a, **k: queued.append(a) or True)
    from skills.core.speckit.handler import handle

    reply = handle(_ctx("myproject add CSV export", tmp_path))
    assert "constitution" in reply
    assert queued == []  # gated: nothing queued


def test_handler_queues_when_constitution_present(tmp_path, monkeypatch):
    import app.speckit_orchestration as orch

    monkeypatch.setattr(orch, "resolve_target", lambda arg: (str(tmp_path), "myproject"))
    monkeypatch.setattr(orch, "has_constitution", lambda path: True)
    seen = {}

    def fake_queue(instance_dir, command, project_name, goal, **k):
        seen.update(command=command, project_name=project_name, goal=goal)
        return True

    monkeypatch.setattr(orch, "queue_mission", fake_queue)
    from skills.core.speckit.handler import handle

    reply = handle(_ctx("myproject add CSV export", tmp_path))
    assert "Queued" in reply
    assert seen == {"command": "speckit", "project_name": "myproject", "goal": "add CSV export"}


# --- prompt substitution hardening (T014b / ant-review finding #2) -----------
# Untrusted goal/issue text flows into the speckit prompt; {KEY} substitution
# must be single-pass so a value containing literal {OTHER_KEY} text cannot
# contaminate or probe other substitutions.

def test_substitute_is_single_pass_no_cross_contamination(monkeypatch):
    from app import prompts

    monkeypatch.setattr(prompts, "_default_placeholders", lambda: {})
    # GOAL value contains a literal {BASE_BRANCH}; it must NOT be replaced.
    out = prompts._substitute("Goal: {GOAL} on {BASE_BRANCH}", {
        "GOAL": "do {BASE_BRANCH} thing",
        "BASE_BRANCH": "main",
    })
    assert out == "Goal: do {BASE_BRANCH} thing on main"


def test_substitute_leaves_unknown_placeholders(monkeypatch):
    from app import prompts

    monkeypatch.setattr(prompts, "_default_placeholders", lambda: {})
    out = prompts._substitute("hi {NAME} {UNKNOWN}", {"NAME": "koan"})
    assert out == "hi koan {UNKNOWN}"


# --- US3: @mention routing ---------------------------------------------------

def test_speckit_skills_are_github_enabled_for_mention():
    """US3: the @mention path (github_command_handler.validate_command) accepts
    /speckit and /speckit_from_branch because their SKILL.md set
    github_enabled. Routing itself is config-driven; this pins it."""
    from app.github_command_handler import validate_command
    from app.skills import build_registry

    registry = build_registry()
    for cmd in ("speckit", "speckit_from_branch"):
        skill = validate_command(cmd, registry)
        assert skill is not None, f"/{cmd} is not github_enabled"
        assert skill.github_enabled is True


# --- US2: issue-URL trigger + repo:/branch: override forwarding --------------

def test_handler_forwards_issue_url_and_override_tokens(tmp_path, monkeypatch):
    import app.speckit_orchestration as orch

    monkeypatch.setattr(orch, "resolve_target", lambda arg: (str(tmp_path), "myrepo"))
    monkeypatch.setattr(orch, "has_constitution", lambda path: True)
    seen = {}

    def fake_queue(instance_dir, command, project_name, goal, **k):
        seen["goal"] = goal
        return True

    monkeypatch.setattr(orch, "queue_mission", fake_queue)
    from skills.core.speckit.handler import handle

    reply = handle(_ctx("https://github.com/o/r/issues/42 repo:r branch:feat", tmp_path))
    assert "Queued" in reply
    assert "https://github.com/o/r/issues/42" in seen["goal"]
    assert "repo:r" in seen["goal"]      # forwarded verbatim
    assert "branch:feat" in seen["goal"]


def test_runner_applies_branch_override_and_strips_tokens(monkeypatch, tmp_path):
    import skills.core.speckit.speckit_runner as runner

    monkeypatch.setattr(runner, "has_constitution", lambda path: True)
    captured = {}
    monkeypatch.setattr(
        runner, "load_prompt_or_skill",
        lambda skill_dir, name, **kw: captured.update(kw) or "PROMPT",
    )
    monkeypatch.setattr(runner, "_invoke_claude", lambda path, prompt: "done")

    runner.run_speckit(
        project_path=str(tmp_path),
        project_name="myrepo",
        goal="add CSV export branch:feat-x",
        notify_fn=lambda _msg: None,
    )
    assert captured["GOAL"] == "add CSV export"  # branch: stripped from the prompt goal
    assert captured["BASE_BRANCH"] == "feat-x"   # branch: applied as base override


# --- speckit_from_branch handler (US5 trigger; runner pending) ---------------

def test_from_branch_handler_usage_on_empty_args(tmp_path):
    from skills.core.speckit_from_branch.handler import handle

    assert "Usage" in handle(_ctx("", tmp_path))


def test_from_branch_handler_aborts_without_constitution(tmp_path, monkeypatch):
    import app.speckit_orchestration as orch

    monkeypatch.setattr(orch, "resolve_target", lambda arg: (str(tmp_path), "myrepo"))
    monkeypatch.setattr(orch, "has_constitution", lambda path: False)
    queued = []
    monkeypatch.setattr(orch, "queue_mission", lambda *a, **k: queued.append(a) or True)
    from skills.core.speckit_from_branch.handler import handle

    reply = handle(_ctx("myrepo my-spec-branch", tmp_path))
    assert "constitution" in reply
    assert queued == []


def test_from_branch_handler_queues_with_repo_and_branch(tmp_path, monkeypatch):
    import app.speckit_orchestration as orch

    monkeypatch.setattr(orch, "resolve_target", lambda arg: (str(tmp_path), "myrepo"))
    monkeypatch.setattr(orch, "has_constitution", lambda path: True)
    seen = {}

    def fake_queue(instance_dir, command, project_name, goal, **k):
        seen.update(command=command, project_name=project_name, goal=goal)
        return True

    monkeypatch.setattr(orch, "queue_mission", fake_queue)
    from skills.core.speckit_from_branch.handler import handle

    reply = handle(_ctx("myrepo my-spec-branch", tmp_path))
    assert "Queued" in reply
    assert seen["command"] == "speckit_from_branch"
    assert seen["project_name"] == "myrepo"
    assert seen["goal"] == "myrepo my-spec-branch"
