"""Pre-push leak gate in commit_instance — real git repos, real guard script.

commit_instance() (app.mission_runner) runs ``git add -A`` + commit + push on the
instance dir. In deployments where the instance dir IS the public repo, a stray
private file at the root would be published before anything can object. The gate
loads scripts/instance_guard.py by file path and refuses to commit+push any staged
path its pure ``find_violations()`` classifies as a leak; the post-push CI guard
(.github/workflows/instance-guard.yml) remains the backstop.

These tests run real git + the real guard script (copied from this repo) in
throwaway repos with a bare remote, and assert on the committed/pushed state —
not on how the internals were called.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARD_SRC = _REPO_ROOT / "scripts" / "instance_guard.py"


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def _init_bare_repo(tmp_path: Path, with_guard: bool = True):
    """Repo (optionally with the guard script) + bare remote, one initial commit."""
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

    guard = repo / "scripts" / "instance_guard.py"
    if with_guard:
        guard.parent.mkdir(parents=True)
        shutil.copy(_GUARD_SRC, guard)
    (repo / "README.md").write_text("public\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "push", "-q", "origin", branch)
    return repo, remote, branch


@pytest.fixture()
def leak_repo(tmp_path: Path):
    repo, remote, branch = _init_bare_repo(tmp_path, with_guard=True)
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


def test_guard_absent_leaves_behavior_unchanged(tmp_path: Path):
    """Deployment layouts where the instance dir is not the public repo have no
    guard script at instance_dir/scripts/ — the commit proceeds as before
    (post-push CI is the only gate there)."""
    from app.mission_runner import commit_instance

    repo, _remote, branch = _init_bare_repo(tmp_path, with_guard=False)
    (repo / "stray.txt").write_text("whatever\n")

    assert commit_instance(str(repo)) is True
    assert "stray.txt" in _git(repo, "ls-tree", "-r", branch, "--name-only")
