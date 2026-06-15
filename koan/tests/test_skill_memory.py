"""Tests for app.skill_memory.build_memory_block (and the env wrapper).

The helper is shared between the agent loop (via prompt_builder) and the
five mission-driving skills (/fix, /plan, /implement, /refactor, /review),
so its observable contract — what shows up in the rendered block under
which circumstances — is load-bearing across both call paths.
"""

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.skill_memory import build_memory_block, build_memory_block_for_skill


@pytest.fixture
def memory_dirs(tmp_path):
    """Build instance/memory/projects/<name>/ ready for file writes."""
    instance = tmp_path / "instance"
    project_name = "demo"
    project_dir = instance / "memory" / "projects" / project_name
    project_dir.mkdir(parents=True)
    return {
        "instance": str(instance),
        "project_name": project_name,
        "project_dir": project_dir,
    }


def _write(memory_dirs, filename, content):
    (memory_dirs["project_dir"] / filename).write_text(content, encoding="utf-8")


class TestBuildMemoryBock:
    def test_returns_empty_when_no_files(self, memory_dirs):
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"], "any task",
        )
        assert result == ""

    def test_returns_empty_when_instance_missing(self, memory_dirs):
        assert build_memory_block("", "proj", "task") == ""
        assert build_memory_block("/anywhere", "", "task") == ""

    def test_rejects_unsafe_project_names(self, memory_dirs):
        """Path-traversal-style project names must not be read or created.

        The current callers all derive ``project_name`` from operator
        config or a git basename, so this guard is purely defensive
        against a future caller passing untrusted input.
        """
        for unsafe in (
            "..",
            "../etc",
            "../../secrets",
            "foo/bar",
            "foo\\bar",
            ".hidden",
            "   ",
        ):
            result = build_memory_block(
                memory_dirs["instance"], unsafe, "task",
            )
            assert result == "", f"unsafe name {unsafe!r} produced {result!r}"

    def test_includes_only_learnings_when_only_learnings_present(self, memory_dirs):
        _write(memory_dirs, "learnings.md", "- always test PR changes\n- use atomic writes\n")
        with patch("app.skill_memory._load_recall_defaults", return_value=(40, 5)):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"], "test",
            )
        assert "<memory-context>" in result
        assert "</memory-context>" in result
        assert "Learnings" in result
        assert "Context (human-curated)" not in result
        assert "Priorities (human-curated)" not in result
        assert "always test PR changes" in result

    def test_includes_all_three_sources_when_present(self, memory_dirs):
        _write(memory_dirs, "learnings.md", "- never push to main\n")
        _write(memory_dirs, "context.md", "Service is a monolith with two workers.\n")
        _write(memory_dirs, "priorities.md", "- ship the rebill flow this sprint\n")
        with patch("app.skill_memory._load_recall_defaults", return_value=(40, 5)):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"], "rebill",
            )
        assert "Context (human-curated)" in result
        assert "Service is a monolith" in result
        assert "Priorities (human-curated)" in result
        assert "ship the rebill flow" in result
        assert "Learnings" in result
        assert "never push to main" in result

    def test_recall_full_tag_loads_all_learnings(self, memory_dirs):
        content = "\n".join(f"- learning {i}" for i in range(50))
        _write(memory_dirs, "learnings.md", content)
        with patch("app.skill_memory._load_recall_defaults", return_value=(2, 0)):
            result = build_memory_block(
                memory_dirs["instance"],
                memory_dirs["project_name"],
                "do something [recall:full]",
            )
        assert "[recall:full] override" in result
        # Every learning must appear when the override is in play.
        for i in range(50):
            assert f"learning {i}" in result

    def test_context_is_capped(self, memory_dirs):
        # 200 lines is well past the 80-line cap.
        big = "\n".join(f"context line {i}" for i in range(200))
        _write(memory_dirs, "context.md", big)
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"], "task",
        )
        assert "context line 0" in result      # kept (top)
        assert "context line 79" in result     # kept (last before cap)
        assert "context line 199" not in result  # dropped
        assert "truncated" in result

    def test_priorities_is_capped(self, memory_dirs):
        big = "\n".join(f"priority line {i}" for i in range(60))
        _write(memory_dirs, "priorities.md", big)
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"], "task",
        )
        assert "priority line 0" in result
        assert "priority line 39" in result
        assert "priority line 59" not in result
        assert "truncated" in result

    def test_override_titles(self, memory_dirs):
        _write(memory_dirs, "learnings.md", "- always log auth attempts\n")
        with patch("app.skill_memory._load_recall_defaults", return_value=(40, 5)):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"], "x",
                title="Project Learnings",
            )
        assert "# Project Learnings" in result
        assert "# Project Memory" not in result

    def test_blank_files_produce_no_section(self, memory_dirs):
        _write(memory_dirs, "context.md", "   \n\n  \n")
        _write(memory_dirs, "priorities.md", "")
        # No learnings file at all; the two blank human files should yield "".
        assert build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"], "task",
        ) == ""

    def test_filtered_learnings_uses_score_when_task_text_overlaps(self, memory_dirs):
        content = (
            "- database migration backfill plan needed\n"
            "- CSS grid wraps better than flexbox\n"
            "- database migration tooling failed once\n"
            "- React hook ordering matters\n"
            + "\n".join(f"- recent padding {i}" for i in range(10))
        )
        _write(memory_dirs, "learnings.md", content)
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"],
            "fix database migration error",
            max_learnings=3, recent_hedge=1,
        )
        # At least one database-related line must appear (scoring picks relevant)
        assert "database migration" in result
        # recency hedge keeps the very last line.
        assert "recent padding 9" in result
        # Unrelated lines should not survive.
        assert "CSS grid wraps" not in result
        assert "React hook ordering" not in result


class TestLoadRecallConfigShared:
    """The parametric helper shared by the agent loop and skill-side defaults."""

    def test_returns_defaults_when_no_memory_block(self):
        from app.skill_memory import load_recall_config

        with patch("app.utils.load_config", return_value={}):
            assert load_recall_config(40, 5) == (40, 5)
            assert load_recall_config(25, 3) == (25, 3)

    def test_reads_configured_values(self):
        from app.skill_memory import load_recall_config

        cfg = {"memory": {"max_relevant_learnings": 7, "recall_recent_hedge": 2}}
        with patch("app.utils.load_config", return_value=cfg):
            assert load_recall_config(40, 5) == (7, 2)

    def test_invalid_values_fall_back_to_supplied_defaults(self):
        from app.skill_memory import load_recall_config

        cfg = {"memory": {"max_relevant_learnings": "nope", "recall_recent_hedge": None}}
        with patch("app.utils.load_config", return_value=cfg):
            assert load_recall_config(40, 5) == (40, 5)
            # Same config, different defaults — fallback respects the caller.
            assert load_recall_config(25, 3) == (25, 3)

    def test_negative_values_clamped_to_zero(self):
        from app.skill_memory import load_recall_config

        cfg = {"memory": {"max_relevant_learnings": -5, "recall_recent_hedge": -1}}
        with patch("app.utils.load_config", return_value=cfg):
            assert load_recall_config(40, 5) == (0, 0)

    def test_load_config_failure_returns_defaults(self):
        from app.skill_memory import load_recall_config

        with patch("app.utils.load_config", side_effect=OSError("boom")):
            assert load_recall_config(40, 5) == (40, 5)


class TestSkillWrapper:
    def test_empty_when_koan_root_unset(self, memory_dirs, monkeypatch):
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        assert build_memory_block_for_skill("/some/path", "task") == ""

    def test_resolves_instance_and_project_from_env(self, memory_dirs, monkeypatch):
        # KOAN_ROOT points at tmp_path; project_name comes from basename.
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))

        _write(memory_dirs, "learnings.md", "- guard the auth boundary\n")
        # project_path's basename must match the project_name so the helper
        # finds memory/projects/demo/learnings.md.
        fake_project_path = str(koan_root / memory_dirs["project_name"])
        result = build_memory_block_for_skill(fake_project_path, "task")
        assert "guard the auth boundary" in result
        assert "<memory-context>" in result

    def test_resolves_via_projects_yaml_when_basename_differs(
        self, memory_dirs, monkeypatch, tmp_path,
    ):
        """When the repo directory name diverges from the configured project
        slug, build_memory_block_for_skill should still pick the right
        memory/projects/<slug>/ tree by matching projects.yaml paths.
        """
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))

        # On-disk repo lives at koan_root/code/some-fork/, but projects.yaml
        # registers it as project name "demo" — matching the memory dir.
        repo_dir = koan_root / "code" / "some-fork"
        repo_dir.mkdir(parents=True)
        projects_yaml = koan_root / "projects.yaml"
        projects_yaml.write_text(
            "projects:\n"
            f"  demo:\n"
            f"    path: {repo_dir}\n",
            encoding="utf-8",
        )

        _write(memory_dirs, "learnings.md", "- guard the auth boundary\n")

        result = build_memory_block_for_skill(str(repo_dir), "task")
        # Basename ("some-fork") would miss; projects.yaml resolution catches it.
        assert "guard the auth boundary" in result
        assert "<memory-context>" in result

    def test_resolves_workspace_project_without_projects_yaml_warning(
        self, memory_dirs, monkeypatch, caplog,
    ):
        """Workspace-discovered projects are first-class known projects."""
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))

        repo_dir = koan_root / "workspace" / memory_dirs["project_name"]
        repo_dir.mkdir(parents=True)
        _write(memory_dirs, "learnings.md", "- load workspace memory\n")

        with caplog.at_level(logging.WARNING, logger="app.skill_memory"):
            result = build_memory_block_for_skill(str(repo_dir), "task")

        assert "load workspace memory" in result
        assert not [
            r for r in caplog.records
            if "not found in known projects" in r.getMessage()
        ]

    def test_resolves_direct_workspace_path_when_registry_is_empty(
        self, memory_dirs, monkeypatch, caplog,
    ):
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))

        repo_dir = koan_root / "workspace" / memory_dirs["project_name"]
        repo_dir.mkdir(parents=True)
        _write(memory_dirs, "learnings.md", "- direct workspace fallback\n")

        with (
            caplog.at_level(logging.WARNING, logger="app.skill_memory"),
            patch("app.utils._get_known_projects_for_root", return_value=[]),
        ):
            result = build_memory_block_for_skill(str(repo_dir), "task")

        assert "direct workspace fallback" in result
        assert not [
            r for r in caplog.records
            if "not found in known projects" in r.getMessage()
        ]

    def test_explicit_project_name_skips_path_reverse_lookup(
        self, memory_dirs, monkeypatch, caplog,
    ):
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))
        unregistered_dir = koan_root / "code" / "unregistered"
        unregistered_dir.mkdir(parents=True)
        _write(memory_dirs, "learnings.md", "- trust explicit project\n")

        with caplog.at_level(logging.WARNING, logger="app.skill_memory"):
            result = build_memory_block_for_skill(
                str(unregistered_dir), "task", project_name="demo",
            )

        assert "trust explicit project" in result
        assert not [
            r for r in caplog.records
            if "not found in known projects" in r.getMessage()
        ]

    def test_falls_back_to_basename_when_projects_yaml_missing(
        self, memory_dirs, monkeypatch,
    ):
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))
        # No projects.yaml at koan_root — the resolver must not crash.
        _write(memory_dirs, "learnings.md", "- baseline rule\n")
        fake_project_path = str(koan_root / memory_dirs["project_name"])
        result = build_memory_block_for_skill(fake_project_path, "task")
        assert "baseline rule" in result


class TestMaxBlockLinesClamp:
    """The global ``memory.max_block_lines`` ceiling — operator-misconfig
    protection that truncates parts in reverse curation order (learnings
    first, then priorities, then context).
    """

    @staticmethod
    def _populate_all_three(memory_dirs):
        """Write context.md (~5 lines), priorities.md (~5 lines), and
        learnings.md (50 entries) for clamp tests below.
        """
        _write(
            memory_dirs, "context.md",
            "Service is a monolith.\nTwo background workers.\n"
            "Postgres primary + replica.\nRedis for sessions.\n"
            "S3 for static uploads.\n",
        )
        _write(
            memory_dirs, "priorities.md",
            "- ship the rebill flow\n- cut p99 latency\n"
            "- migrate the auth middleware\n- close out the OSS backlog\n"
            "- prep for Q3 audit\n",
        )
        _write(
            memory_dirs, "learnings.md",
            "\n".join(f"- learning {i}" for i in range(50)),
        )

    def test_max_block_lines_default_no_clamp(self, memory_dirs):
        """A block whose total line count fits under the default cap (200)
        must pass through unchanged — no marker, no truncation.
        """
        self._populate_all_three(memory_dirs)
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"],
            "any task",
            max_learnings=10, recent_hedge=2,
        )
        # Far under the 200-line default — nothing should be clamped.
        assert "memory block clamped from" not in result
        assert "raise memory.max_block_lines" not in result

    def test_max_block_lines_clamps_learnings_first(self, memory_dirs):
        """When the cap fires, learnings get trimmed before priorities or
        context — the curated sources are preserved.
        """
        self._populate_all_three(memory_dirs)
        # 50 learnings = 52-line learnings part (header + blank + 50 bullets).
        # Plus ~9 lines each for ctx + prio (fenced). Total ~70 lines.
        # Cap at 40 → ~30 lines of excess, all of which should come out of
        # learnings (which alone has 49 droppable lines).
        with patch("app.skill_memory._load_max_block_lines", return_value=40):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"],
                "do something [recall:full]",
            )

        # Curated content must survive unchanged.
        assert "Service is a monolith." in result
        assert "Postgres primary + replica." in result
        assert "S3 for static uploads." in result
        assert "ship the rebill flow" in result
        assert "prep for Q3 audit" in result
        # Some early learnings must still be there (we didn't kill the whole part)…
        assert "- learning 0" in result
        # …but the late ones are dropped from the bottom.
        assert "- learning 49" not in result

    def test_max_block_lines_logs_warning_on_clamp(self, memory_dirs, caplog):
        """When clamping fires, a WARNING line is emitted with the project
        name and before/after counts (so operators can grep for it).
        """
        self._populate_all_three(memory_dirs)
        with caplog.at_level(logging.WARNING, logger="app.skill_memory"), \
                patch("app.skill_memory._load_max_block_lines", return_value=40):
            build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"],
                "do something [recall:full]",
            )

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "memory block clamped" in r.getMessage()
        ]
        assert warnings, f"expected a clamp WARNING, got {[r.getMessage() for r in caplog.records]!r}"
        msg = warnings[0].getMessage()
        assert "project=demo" in msg
        # Format is "N → M lines"; we don't pin exact N/M (depends on fencing
        # output length), but both numbers must be present.
        assert "→" in msg
        assert "lines" in msg

    def test_max_block_lines_truncation_marker_present(self, memory_dirs):
        """The model needs to see *why* content is missing — the marker tells
        it (and the operator reading the prompt) that the clamp fired.
        """
        self._populate_all_three(memory_dirs)
        with patch("app.skill_memory._load_max_block_lines", return_value=40):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"],
                "do something [recall:full]",
            )

        assert "memory block clamped from" in result
        assert "raise memory.max_block_lines in config.yaml to see more" in result

    def test_max_block_lines_config_override(self, memory_dirs):
        """An operator-supplied ``memory.max_block_lines`` must be honored
        end-to-end (no hard-coded constant short-circuits the config path).
        """
        self._populate_all_three(memory_dirs)
        cfg = {"memory": {"max_block_lines": 40}}
        with patch("app.utils.load_config", return_value=cfg):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"],
                "do something [recall:full]",
            )
        assert "memory block clamped from" in result, (
            "config override should have triggered clamp at 40 lines"
        )


class TestExternalFencing:
    """The two human-curated files (``context.md``, ``priorities.md``) are
    injected verbatim into every skill prompt, so they're a prompt-injection
    surface. ``prompt_guard.fence_external_data`` neutralises that.
    """

    def test_context_md_fenced(self, memory_dirs):
        _write(memory_dirs, "context.md", "Service is a monolith.\n")
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"], "task",
        )
        assert "--- BEGIN EXTERNAL DATA (context.md)" in result
        assert "--- END EXTERNAL DATA (context.md)" in result
        # Original content survives intact inside the fence.
        assert "Service is a monolith." in result

    def test_priorities_md_fenced(self, memory_dirs):
        _write(memory_dirs, "priorities.md", "- ship the rebill flow\n")
        result = build_memory_block(
            memory_dirs["instance"], memory_dirs["project_name"], "task",
        )
        assert "--- BEGIN EXTERNAL DATA (priorities.md)" in result
        assert "--- END EXTERNAL DATA (priorities.md)" in result
        assert "ship the rebill flow" in result

    def test_learnings_not_fenced(self, memory_dirs):
        """Agent-generated learnings are not external data — fencing them
        would just add noise and burn tokens.
        """
        _write(memory_dirs, "learnings.md", "- always test PR changes\n")
        with patch("app.skill_memory._load_recall_defaults", return_value=(40, 5)):
            result = build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"], "test",
            )
        # No EXTERNAL DATA marker mentioning learnings.md anywhere.
        assert "EXTERNAL DATA (learnings" not in result
        # And the learnings content is present.
        assert "always test PR changes" in result


class TestObservability:
    """Three log lines added so operators can spot silent memory loss and
    size their cap from data instead of guessing.
    """

    def test_logs_info_on_missing_koan_root(self, monkeypatch, caplog):
        """Standalone invocation (no KOAN_ROOT) is legitimate — but it
        should be visible in logs so an operator whose env is broken can
        tell ``no memory`` from ``standalone``.
        """
        monkeypatch.delenv("KOAN_ROOT", raising=False)
        with caplog.at_level(logging.INFO, logger="app.skill_memory"):
            result = build_memory_block_for_skill("/some/repo", "task")
        assert result == ""

        infos = [
            r for r in caplog.records
            if r.levelno == logging.INFO and "KOAN_ROOT unset" in r.getMessage()
        ]
        assert infos, "expected an INFO log when KOAN_ROOT is unset"
        assert "/some/repo" in infos[0].getMessage()

    def test_resolve_project_name_logs_warning_on_basename_fallback(
        self, memory_dirs, monkeypatch, caplog,
    ):
        """When projects.yaml loads but the project_path doesn't match any
        registered entry, the resolver falls back to basename. That's a
        silent-drift case — memory may point at a slug that doesn't exist.
        """
        koan_root = Path(memory_dirs["instance"]).parent
        monkeypatch.setenv("KOAN_ROOT", str(koan_root))

        # projects.yaml registers a project, but at a different path.
        registered_dir = koan_root / "code" / "registered-project"
        registered_dir.mkdir(parents=True)
        (koan_root / "projects.yaml").write_text(
            "projects:\n"
            f"  registered:\n"
            f"    path: {registered_dir}\n",
            encoding="utf-8",
        )

        # Call with a path that is NOT in projects.yaml.
        unregistered_dir = koan_root / "code" / "unregistered-project"
        unregistered_dir.mkdir(parents=True)

        with caplog.at_level(logging.WARNING, logger="app.skill_memory"):
            build_memory_block_for_skill(str(unregistered_dir), "task")

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "not found in known projects" in r.getMessage()
        ]
        assert warnings, (
            "expected a WARNING when known-project lookup found no match; "
            f"got {[r.getMessage() for r in caplog.records]!r}"
        )
        msg = warnings[0].getMessage()
        assert "unregistered-project" in msg

    def test_build_memory_block_logs_size_telemetry(self, memory_dirs, caplog):
        """Every successful build emits a one-line size summary so operators
        can grep ``[skill_memory] block`` to see what they're paying for.
        """
        _write(memory_dirs, "context.md", "Architecture line.\n")
        _write(memory_dirs, "priorities.md", "- ship the migration\n")
        _write(memory_dirs, "learnings.md", "- always test PR changes\n")
        with caplog.at_level(logging.INFO, logger="app.skill_memory"), \
                patch("app.skill_memory._load_recall_defaults", return_value=(40, 5)):
            build_memory_block(
                memory_dirs["instance"], memory_dirs["project_name"],
                "test",
            )

        infos = [
            r for r in caplog.records
            if r.levelno == logging.INFO and "block built" in r.getMessage()
        ]
        assert infos, (
            "expected an INFO telemetry log on every successful build; "
            f"got {[r.getMessage() for r in caplog.records]!r}"
        )
        msg = infos[0].getMessage()
        assert "project=demo" in msg
        # All three counts must be non-zero in this fixture.
        assert "ctx=" in msg
        assert "prio=" in msg
        assert "learn=" in msg
        # Total line count must be present and > 0.
        assert "lines=" in msg
