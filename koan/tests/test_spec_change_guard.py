"""Tests for scripts/spec_change_guard.py — the durable-contract change gate.

The guard lives under scripts/ (not on pytest's pythonpath), so it is loaded by file
path via importlib. Tests exercise the pure functions and the CLI exit-code contract;
the impure ``changed_files`` (git) is not tested here.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARD_PATH = _REPO_ROOT / "scripts" / "spec_change_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("spec_change_guard", _GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass field resolution can find the module
    # (Python 3.12+ looks up cls.__module__ in sys.modules during @dataclass).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


# --- is_durable_contract ---------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "specs/components/core.md",
        "specs/components/agent-loop.md",
        "specs/skills/review.md",
        "specs/skills/nested/deep.md",
    ],
)
def test_durable_contracts_detected(path):
    assert guard.is_durable_contract(path) is True


@pytest.mark.parametrize(
    "path",
    [
        # ephemeral speckit planning folders — the intended spec-first artifact
        "specs/004-mission-store/spec.md",
        "specs/004-mission-store/plan.md",
        "specs/005-spec-change-governance/tasks.md",
        # bundle bookkeeping / template — never a live contract
        "specs/components/index.md",
        "specs/skills/index.md",
        "specs/skills/SKILL_SPEC_TEMPLATE.md",
        "specs/README.md",
        "specs/SCHEMA.md",
        "specs/index.md",
        # not a contract at all
        "docs/design/decisions.md",
        "koan/app/missions.py",
        "specs/components/core.txt",  # not markdown
        "specs/componentsX/core.md",  # not the components dir
    ],
)
def test_non_contracts_excluded(path):
    assert guard.is_durable_contract(path) is False


def test_windows_separators_normalised():
    assert guard.is_durable_contract("specs\\components\\core.md") is True


# --- has_architecture_declaration ------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "- [x] **Architectural change** — modifies a contract. Rationale: x",
        "-  [x]  architectural change here",
        "* [x] This is an Architectural Change with caps",
        "intro\n\n- [x] architectural change\n\nmore",
        "- [X] architectural change",  # GFM accepts uppercase X as checked
    ],
)
def test_declaration_present(body):
    assert guard.has_architecture_declaration(body) is True


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "- [ ] **Architectural change** — unchecked box",
        "- [x] Some unrelated checked task",
        "This PR changes the architecture but has no checkbox.",
    ],
)
def test_declaration_absent(body):
    assert guard.has_architecture_declaration(body) is False


# --- evaluate --------------------------------------------------------------

def test_evaluate_clean_when_no_contract_changed():
    v = guard.evaluate(["koan/app/missions.py", "docs/x.md"], pr_body=None)
    assert v.ok is True
    assert v.undeclared_contracts == []


def test_evaluate_declared_passes():
    v = guard.evaluate(
        ["specs/components/core.md", "koan/app/missions.py"],
        pr_body="- [x] architectural change: redefined the mission contract",
    )
    assert v.ok is True


def test_evaluate_undeclared_fails_and_lists_contracts():
    v = guard.evaluate(
        ["specs/skills/review.md", "specs/components/core.md"],
        pr_body="just a normal change, no declaration",
    )
    assert v.ok is False
    assert v.fail_closed is False
    assert v.undeclared_contracts == ["specs/components/core.md", "specs/skills/review.md"]


def test_evaluate_fail_closed_when_no_body():
    v = guard.evaluate(["specs/components/core.md"], pr_body=None)
    assert v.ok is False
    assert v.fail_closed is True
    assert v.undeclared_contracts == ["specs/components/core.md"]


def test_evaluate_ephemeral_only_is_clean_even_without_body():
    v = guard.evaluate(["specs/005-spec-change-governance/plan.md"], pr_body=None)
    assert v.ok is True


# --- CLI exit-code contract ------------------------------------------------

def _run_cli(args, stdin=None):
    return subprocess.run(
        [sys.executable, str(_GUARD_PATH), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )


def test_cli_exit_0_when_declared_via_stdin():
    r = _run_cli(
        ["--changed-file", "specs/components/core.md", "--pr-body", "-"],
        stdin="- [x] architectural change: yes",
    )
    assert r.returncode == 0, r.stderr


def test_cli_exit_1_when_undeclared():
    r = _run_cli(
        ["--changed-file", "specs/components/core.md", "--pr-body", "-"],
        stdin="no declaration here",
    )
    assert r.returncode == 1
    assert "specs/components/core.md" in r.stderr
    assert "Architectural change" in r.stderr


def test_cli_exit_1_fail_closed_without_body():
    r = _run_cli(["--changed-file", "specs/components/core.md"])
    assert r.returncode == 1
    assert "failing closed" in r.stderr.lower()


def test_cli_exit_0_for_non_contract_change():
    r = _run_cli(["--changed-file", "koan/app/missions.py"])
    assert r.returncode == 0, r.stderr


def test_cli_usage_error_on_bad_pr_body_flag():
    r = _run_cli(["--changed-file", "specs/components/core.md", "--pr-body", "notdash"])
    assert r.returncode == 2
