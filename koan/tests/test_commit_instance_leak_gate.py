"""Pre-push leak gate in commit_instance — real git repos, real leak rules.

commit_instance() (app.mission_runner) runs ``git add -A`` + commit + push on the
instance dir. The gate runs the instance leak rules (app.instance_leak_rules —
the same pure core the post-push CI guard uses) over the staged changes before
committing, and unstages any hit so it is neither committed nor pushed. The
rules are imported from the installed package, never exec'd out of the
agent-writable instance tree, so the gate is active in every deployment
layout; the post-push CI guard (.github/workflows/instance-guard.yml) remains
the backstop.

Deletions of leaking paths are allowed through: unstaging a deletion would
resurrect the tracked entry and strand the leak on the remote forever.

These tests run real git in throwaway repos with a bare remote and assert on
the committed/pushed state — not on how the internals were called.
"""

import subprocess
from pathlib import Path

import pytest


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _init_bare_repo(tmp_path: Path):
    """Repo + bare remote, one initial commit."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "gate@test.invalid")
    _git(repo, "config", "user.name", "Gate Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "remote", "add", "origin", str(remote))

    (repo / "README.md").write_text("public\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "push", "-q", "origin", branch)
    return repo, remote, branch


@pytest.fixture()
def leak_repo(tmp_path: Path):
    repo, remote, branch = _init_bare_repo(tmp_path)
    return {
        "repo": repo,
        "remote": remote,
        "branch": branch,
        "head_sha": lambda: _git(repo, "rev-parse", "HEAD"),
        "tracked": lambda where=repo: _git(where, "ls-tree", "-r", branch, "--name-only"),
        "untracked": lambda: _git(
            repo, "status", "--porcelain", "--untracked-files=all"
        ),
    }


def test_instance_leak_never_committed_or_pushed(leak_repo, capsys):
    """A staged instance/ file must stay out of both local and remote HEAD."""
    from app.mission_runner import commit_instance

    (leak_repo["repo"] / "instance").mkdir()
    (leak_repo["repo"] / "instance" / "missions.md").write_text("private\n")
    (leak_repo["repo"] / "notes.md").write_text("public note\n")

    assert commit_instance(str(leak_repo["repo"])) is True

    local = leak_repo["tracked"]()
    assert "notes.md" in local
    assert "instance/missions.md" not in local
    remote = leak_repo["tracked"](where=leak_repo["remote"])
    assert "instance/missions.md" not in remote

    # The file survives on disk, untracked — and the operator sees the block.
    assert (leak_repo["repo"] / "instance" / "missions.md").is_file()
    assert "instance/missions.md" in leak_repo["untracked"]()
    assert "instance/missions.md" in capsys.readouterr().err


def test_non_ascii_leak_blocked(leak_repo):
    """Non-ASCII names must be blocked too.

    Staged changes are read with ``--name-status -z`` so git delivers the real
    (unquoted) path; line-oriented output would C-quote it (core.quotePath)
    and the prefix rules would miss it.
    """
    from app.mission_runner import commit_instance

    (leak_repo["repo"] / "instance").mkdir()
    (leak_repo["repo"] / "instance" / "café.md").write_text("privé\n")
    (leak_repo["repo"] / "notes.md").write_text("public note\n")

    assert commit_instance(str(leak_repo["repo"])) is True

    local = leak_repo["tracked"]()
    assert "notes.md" in local
    assert "instance/café.md" not in local
    remote = leak_repo["tracked"](where=leak_repo["remote"])
    assert "instance/café.md" not in remote


def test_example_skills_leak_blocked(leak_repo):
    """The second guard rule (instance.example/skills/) is enforced too."""
    from app.mission_runner import commit_instance

    skill = leak_repo["repo"] / "instance.example" / "skills" / "my_team" / "my_fix"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("name: my_fix\n")
    (leak_repo["repo"] / "ok.md").write_text("fine\n")

    assert commit_instance(str(leak_repo["repo"])) is True

    local = leak_repo["tracked"]()
    assert "ok.md" in local
    assert "instance.example/skills/my_team/my_fix/SKILL.md" not in local


def test_only_leaks_staged_blocks_whole_commit(leak_repo):
    """Nothing clean to commit → no commit at all, remote untouched."""
    from app.mission_runner import commit_instance

    before = leak_repo["head_sha"]()
    (leak_repo["repo"] / "instance").mkdir()
    (leak_repo["repo"] / "instance" / "secret.md").write_text("x\n")

    assert commit_instance(str(leak_repo["repo"])) is False
    assert leak_repo["head_sha"]() == before
    assert "instance/secret.md" not in leak_repo["tracked"](
        where=leak_repo["remote"]
    )


def test_tracked_leak_modification_not_committed(leak_repo):
    """A private file already tracked (pre-existing force-add): its new content
    must not be committed either — unstaging restores the index entry to HEAD."""
    from app.mission_runner import commit_instance

    evil = leak_repo["repo"] / "instance" / "evil.md"
    evil.parent.mkdir(parents=True)
    evil.write_text("v1\n")
    _git(leak_repo["repo"], "add", "-A")
    _git(leak_repo["repo"], "commit", "-q", "-m", "pre-existing leak")

    evil.write_text("v2\n")
    (leak_repo["repo"] / "clean.md").write_text("ok\n")

    assert commit_instance(str(leak_repo["repo"])) is True
    assert _git(leak_repo["repo"], "show", "HEAD:instance/evil.md") == "v1"
    assert "clean.md" in leak_repo["tracked"]()


def test_tracked_leak_deletion_is_committed(leak_repo):
    """Removing a pre-existing tracked leak must go through.

    Unstaging a staged deletion would resurrect the index entry and strand the
    leak on the remote forever, so the gate lets deletions of leaking paths
    through.
    """
    from app.mission_runner import commit_instance

    evil = leak_repo["repo"] / "instance" / "evil.md"
    evil.parent.mkdir(parents=True)
    evil.write_text("v1\n")
    _git(leak_repo["repo"], "add", "-A")
    _git(leak_repo["repo"], "commit", "-q", "-m", "pre-existing leak")

    evil.unlink()
    (leak_repo["repo"] / "clean.md").write_text("ok\n")

    assert commit_instance(str(leak_repo["repo"])) is True
    local = leak_repo["tracked"]()
    assert "instance/evil.md" not in local
    assert "clean.md" in local
    remote = leak_repo["tracked"](where=leak_repo["remote"])
    assert "instance/evil.md" not in remote


def test_gate_active_without_guard_script_in_tree(tmp_path: Path):
    """The rules live in the installed package, not in the tree.

    Real deployments check out a private instance repo with no
    scripts/instance_guard.py inside — the gate must still block there, and
    must not execute any file from the agent-writable tree.
    """
    from app.mission_runner import commit_instance

    repo, remote, branch = _init_bare_repo(tmp_path)
    (repo / "instance").mkdir()
    (repo / "instance" / "missions.md").write_text("private\n")
    (repo / "notes.md").write_text("public note\n")

    assert commit_instance(str(repo)) is True

    assert "notes.md" in _git(repo, "ls-tree", "-r", branch, "--name-only")
    assert "instance/missions.md" not in _git(
        remote, "ls-tree", "-r", branch, "--name-only"
    )
