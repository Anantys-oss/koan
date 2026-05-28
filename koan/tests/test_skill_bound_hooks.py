"""Tests for skill-bound hook discovery in app.hooks.

Skill-bound hooks live at ``instance/skills/<scope>/<name>/<event>.py`` and
export a ``run(ctx)`` function. These tests verify the registry finds them,
fires them with the documented context, and isolates errors.
"""

from pathlib import Path

import pytest

from app.hooks import HookRegistry, fire_hook, init_hooks, reset_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def instance_dir(tmp_path):
    """Create an instance directory layout with empty hooks/ and skills/."""
    inst = tmp_path / "instance"
    (inst / "hooks").mkdir(parents=True)
    (inst / "skills").mkdir()
    return inst


def _write_skill_hook(
    instance_dir: Path,
    scope: str,
    name: str,
    event: str,
    code: str,
) -> Path:
    skill_dir = instance_dir / "skills" / scope / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / f"{event}.py"
    path.write_text(code)
    return path


def _write_instance_hook(instance_dir: Path, name: str, code: str) -> Path:
    path = instance_dir / "hooks" / f"{name}.py"
    path.write_text(code)
    return path


class TestSkillBoundDiscovery:
    def test_discovers_skill_hook(self, instance_dir):
        _write_skill_hook(
            instance_dir, "my", "fix", "post_mission",
            "def run(ctx): pass\n",
        )
        registry = HookRegistry(instance_dir / "hooks", instance_dir=str(instance_dir))
        assert registry.has_hooks("post_mission")

    def test_ignores_unknown_event_filename(self, instance_dir):
        _write_skill_hook(
            instance_dir, "my", "fix", "random_event",
            "def run(ctx): pass\n",
        )
        registry = HookRegistry(instance_dir / "hooks", instance_dir=str(instance_dir))
        assert not registry.has_hooks("random_event")
        assert not registry.has_hooks("post_mission")

    def test_ignores_module_without_run(self, instance_dir, capsys):
        _write_skill_hook(
            instance_dir, "my", "fix", "post_mission",
            "x = 42\n",
        )
        registry = HookRegistry(instance_dir / "hooks", instance_dir=str(instance_dir))
        assert not registry.has_hooks("post_mission")
        captured = capsys.readouterr()
        assert "no callable run()" in captured.err

    def test_isolates_load_errors(self, instance_dir, capsys):
        _write_skill_hook(
            instance_dir, "broken", "skill", "post_mission",
            "def run(\n",  # syntax error
        )
        _write_skill_hook(
            instance_dir, "my", "fix", "post_mission",
            "def run(ctx): ctx.setdefault('hits', []).append('my_fix')\n",
        )
        registry = HookRegistry(instance_dir / "hooks", instance_dir=str(instance_dir))
        assert registry.has_hooks("post_mission")
        captured = capsys.readouterr()
        assert "Failed to load skill hook" in captured.err

    def test_skips_underscore_dirs(self, instance_dir):
        _write_skill_hook(
            instance_dir, "_private", "x", "post_mission",
            "def run(ctx): pass\n",
        )
        _write_skill_hook(
            instance_dir, "my", "_pycache_", "post_mission",
            "def run(ctx): pass\n",
        )
        registry = HookRegistry(instance_dir / "hooks", instance_dir=str(instance_dir))
        assert not registry.has_hooks("post_mission")

    def test_instance_hook_runs_before_skill_hook(self, instance_dir, tmp_path):
        order_file = tmp_path / "order.txt"
        _write_instance_hook(
            instance_dir, "global",
            f"def h(ctx):\n"
            f"    with open({str(order_file)!r}, 'a') as f:\n"
            f"        f.write('global\\n')\n"
            f"HOOKS = {{'post_mission': h}}\n",
        )
        _write_skill_hook(
            instance_dir, "my", "fix", "post_mission",
            f"def run(ctx):\n"
            f"    with open({str(order_file)!r}, 'a') as f:\n"
            f"        f.write('skill\\n')\n",
        )

        init_hooks(str(instance_dir))
        fire_hook("post_mission", instance_dir=str(instance_dir))

        order = order_file.read_text().splitlines()
        assert order == ["global", "skill"]

    def test_fire_runs_skill_hook_with_ctx(self, instance_dir, tmp_path):
        capture = tmp_path / "ctx.txt"
        _write_skill_hook(
            instance_dir, "my", "fix", "post_mission",
            f"def run(ctx):\n"
            f"    with open({str(capture)!r}, 'w') as f:\n"
            f"        f.write(repr(sorted(ctx.keys())))\n",
        )
        init_hooks(str(instance_dir))
        fire_hook(
            "post_mission",
            instance_dir=str(instance_dir),
            mission_title="/myfix ACME-1",
            exit_code=0,
            result_text="RESULT: SUCCESS",
        )
        keys_repr = capture.read_text()
        assert "instance_dir" in keys_repr
        assert "mission_title" in keys_repr
        assert "result_text" in keys_repr

    def test_skill_hook_error_isolated(self, instance_dir, tmp_path, capsys):
        marker = tmp_path / "ok_ran.txt"
        _write_skill_hook(
            instance_dir, "my", "broken", "post_mission",
            "def run(ctx): raise RuntimeError('boom')\n",
        )
        _write_skill_hook(
            instance_dir, "my", "ok", "post_mission",
            f"def run(ctx):\n"
            f"    with open({str(marker)!r}, 'w') as f:\n"
            f"        f.write('ran')\n",
        )
        init_hooks(str(instance_dir))
        failures = fire_hook("post_mission")
        # Broken hook's error is captured.
        assert any("boom" in msg for msg in failures.values())
        # Sibling hook still executed despite the broken one.
        assert marker.read_text() == "ran"
        # Only the broken hook is recorded as failed; the sibling is not.
        assert len(failures) == 1
        assert any("broken" in name for name in failures)
        captured = capsys.readouterr()
        assert "post_mission" in captured.err

    def test_no_skill_dir_does_not_crash(self, tmp_path):
        inst = tmp_path / "instance"
        (inst / "hooks").mkdir(parents=True)
        # Note: no skills/ directory.
        registry = HookRegistry(inst / "hooks", instance_dir=str(inst))
        assert not registry.has_hooks("post_mission")
