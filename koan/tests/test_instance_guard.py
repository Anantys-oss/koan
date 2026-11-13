"""Tests for the instance leak rules and the standalone guard CLI.

The pure rules live in the app package (``app.instance_leak_rules`` — on pytest's
pythonpath via pyproject ``pythonpath = ["koan"]``); ``scripts/instance_guard.py``
is the CLI wrapper around them, loaded by file path since scripts/ is not on the
pythonpath. Tests exercise the pure functions and the CLI exit-code contract; the
impure ``tracked_files`` (git) is not tested here.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from app.instance_leak_rules import classify, find_violations

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARD_PATH = _REPO_ROOT / "scripts" / "instance_guard.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("instance_guard", _GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# --- classify --------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "instance",
        "instance/missions.md",
        "instance/memory/global/genesis.md",
        "instance/config.yaml",
        "instance\\config.yaml",  # windows separator normalised
    ],
)
def test_classify_instance_violation(path):
    assert classify(path) == "instance"


@pytest.mark.parametrize(
    "path",
    [
        "instance.example/skills",
        "instance.example/skills/my_team/my_fix/SKILL.md",
        "instance.example/skills/README.md",
    ],
)
def test_classify_example_skills_violation(path):
    assert classify(path) == "instance-example-skills"


@pytest.mark.parametrize(
    "path",
    [
        "projects.yaml",
        "projects.yaml.bak-20261030-222007",  # the file that actually leaked
        "projects.yaml.bak",
        "projects.yaml.old",
    ],
)
def test_classify_projects_config_violation(path):
    """Live projects.yaml and its backups hold operator paths + private repos.

    A timestamped ``projects.yaml.bak-*`` was committed to the public repo by
    ``commit_instance``'s ``git add -A``; the guard must reject it, not pass.
    ``projects.example.yaml`` (the template) must stay allowed — see
    ``test_classify_allowed``.
    """
    assert classify(path) == "projects-config"


@pytest.mark.parametrize(
    "path",
    [
        "instance.example/config.yaml",
        "instance.example/soul.md",
        "instance.example/memory/summary.md",
        "koan/skills/core/review/SKILL.md",
        "instances/foo.txt",  # unrelated dir that merely shares a prefix
        "instance_helper.py",
        "README.md",
        "projects.example.yaml",  # the tracked template config stays committable
        "projects.docker.yaml",
    ],
)
def test_classify_allowed(path):
    assert classify(path) is None


def test_instance_example_itself_is_allowed():
    """The template tree is tracked on purpose — only its skills/ subtree is banned."""
    assert classify("instance.example/README.md") is None


# --- find_violations -------------------------------------------------------

def test_find_violations_returns_sorted_pairs():
    paths = [
        "instance/z.md",
        "instance.example/skills/a/SKILL.md",
        "instance/a.md",
        "projects.yaml.bak-20261030-222007",
        "koan/app/main.py",
    ]
    result = find_violations(paths)
    assert result == [
        ("instance.example/skills/a/SKILL.md", "instance-example-skills"),
        ("instance/a.md", "instance"),
        ("instance/z.md", "instance"),
        ("projects.yaml.bak-20261030-222007", "projects-config"),
    ]


def test_find_violations_empty_when_clean():
    paths = ["instance.example/config.yaml", "koan/skills/core/fix/SKILL.md", "README.md"]
    assert find_violations(paths) == []


# --- CLI contract ----------------------------------------------------------

def test_main_passes_on_clean_paths(capsys):
    code = cli.main(["--path", "instance.example/config.yaml", "--path", "README.md"])
    assert code == 0
    assert "PASSED" in capsys.readouterr().out


def test_main_fails_on_instance_leak(capsys):
    code = cli.main(["--path", "instance/missions.md"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "instance/missions.md" in err


def test_main_fails_on_example_skill(capsys):
    code = cli.main(["--path", "instance.example/skills/my_team/my_fix/SKILL.md"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "instance.example/skills/my_team/my_fix/SKILL.md" in err


def test_main_reports_both_reasons(capsys):
    code = cli.main(
        ["--path", "instance/missions.md", "--path", "instance.example/skills/x/SKILL.md"]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "instance/missions.md" in err
    assert "instance.example/skills/x/SKILL.md" in err


def test_main_fails_on_projects_yaml_leak(capsys):
    code = cli.main(["--path", "projects.yaml.bak-20261030-222007"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "projects.yaml.bak-20261030-222007" in err


def test_main_allows_projects_example_yaml(capsys):
    code = cli.main(["--path", "projects.example.yaml"])
    assert code == 0
    assert "PASSED" in capsys.readouterr().out
