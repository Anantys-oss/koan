import subprocess
from pathlib import Path

import pytest

from app.repo_tooling_guard import (
    QUARANTINE_DIRNAME,
    sanitize_repo_tooling,
    restore_repo_tooling,
)


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "CLAUDE.md").write_text("repo contributor guidance\n")
    (root / "AGENTS.md").write_text("agents\n")
    skills = root / ".claude" / "skills" / "brain"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("brain\n")
    (root / "README.md").write_text("hi\n")
    # instance/ is gitignored in the real koan repo — the quarantine dir lives
    # under it, so it must stay invisible to `git status`.
    (root / ".gitignore").write_text("instance/\n")
    (root / "instance").mkdir()
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def test_relocates_tracked_artifacts_out_of_root(repo):
    moved = sanitize_repo_tooling(repo)
    assert set(moved) == {"CLAUDE.md", "AGENTS.md", ".claude"}
    assert not (repo / "CLAUDE.md").exists()
    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ".claude").exists()
    q = repo / "instance" / QUARANTINE_DIRNAME
    assert (q / "CLAUDE.md").read_text() == "repo contributor guidance\n"
    assert (q / ".claude" / "skills" / "brain" / "SKILL.md").exists()


def test_git_status_clean_after_sanitize(repo):
    sanitize_repo_tooling(repo)
    status = _git(repo, "status", "--porcelain").stdout.strip()
    assert status == "", f"expected clean tree, got:\n{status}"


def test_idempotent_second_call_is_noop(repo):
    sanitize_repo_tooling(repo)
    moved_again = sanitize_repo_tooling(repo)
    assert moved_again == []
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_leaves_untracked_operator_files_alone(tmp_path):
    root = tmp_path
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "instance").mkdir()
    (root / "CLAUDE.md").write_text("operator's own untracked file\n")  # never committed
    moved = sanitize_repo_tooling(root)
    assert moved == []
    assert (root / "CLAUDE.md").read_text() == "operator's own untracked file\n"


def test_non_repo_is_noop(tmp_path):
    (tmp_path / "instance").mkdir()
    (tmp_path / "CLAUDE.md").write_text("x\n")
    assert sanitize_repo_tooling(tmp_path) == []
    assert (tmp_path / "CLAUDE.md").exists()


def test_restore_round_trip(repo):
    sanitize_repo_tooling(repo)
    restored = restore_repo_tooling(repo)
    assert set(restored) == {"CLAUDE.md", "AGENTS.md", ".claude"}
    assert (repo / "CLAUDE.md").read_text() == "repo contributor guidance\n"
    assert (repo / ".claude" / "skills" / "brain" / "SKILL.md").exists()
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_regression_no_runtime_cwd_contains_claude_skills(repo):
    """The guard's contract: after sanitize, KOAN_ROOT (the runtime cwd) has
    no auto-loadable .claude/skills tree or root CLAUDE.md/AGENTS.md."""
    sanitize_repo_tooling(repo)
    assert not (repo / ".claude" / "skills").exists()
    assert not (repo / "CLAUDE.md").exists()
    assert not (repo / "AGENTS.md").exists()
