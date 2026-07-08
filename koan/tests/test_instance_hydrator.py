"""Tests for cold-boot instance/ hydration from a git repo."""
import subprocess
from pathlib import Path

from app.instance_hydrator import hydrate_instance_from_repo, pull_instance_repo


def _git_env() -> dict:
    return {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }


def _make_source_repo(tmp_path: Path) -> str:
    """Create a bare repo with a seeded instance-like tree; return a file:// URL."""
    work = tmp_path / "src"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "missions.md").write_text("# Missions\n")
    (work / "soul.md").write_text("I am a test instance.\n")
    (work / "memory").mkdir()
    (work / "memory" / "summary.md").write_text("seed memory\n")
    env = {**_git_env(), "GIT_DIR": str(work / ".git"), "GIT_WORK_TREE": str(work)}
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=work, env=env, check=True)
    bare = tmp_path / "src.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return f"file://{bare}"


def test_hydrates_empty_instance(tmp_path):
    url = _make_source_repo(tmp_path)
    instance = tmp_path / "instance"
    instance.mkdir()  # bind-mount point exists but empty
    ok = hydrate_instance_from_repo(str(instance), repo=url)
    assert ok is True
    assert (instance / "missions.md").exists()
    assert (instance / "soul.md").read_text() == "I am a test instance.\n"
    assert (instance / "memory" / "summary.md").exists()
    # .git MUST be preserved so commit_instance() can keep pushing
    assert (instance / ".git").is_dir()


def test_skips_when_already_hydrated(tmp_path):
    url = _make_source_repo(tmp_path)
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "missions.md").write_text("existing\n")  # already provisioned
    ok = hydrate_instance_from_repo(str(instance), repo=url)
    assert ok is False
    assert (instance / "missions.md").read_text() == "existing\n"  # untouched
    assert not (instance / "soul.md").exists()


def test_skips_when_dot_git_present(tmp_path):
    url = _make_source_repo(tmp_path)
    instance = tmp_path / "instance"
    (instance / ".git").mkdir(parents=True)  # already a repo
    ok = hydrate_instance_from_repo(str(instance), repo=url)
    assert ok is False


def test_no_repo_configured_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("KOAN_INSTANCE_REPO", raising=False)
    instance = tmp_path / "instance"
    instance.mkdir()
    assert hydrate_instance_from_repo(str(instance), repo=None) is False


def test_branch_selection(tmp_path):
    url = _make_source_repo(tmp_path)
    # add a second branch with a marker file
    bare = url.removeprefix("file://")
    work2 = tmp_path / "work2"
    subprocess.run(["git", "clone", "-q", str(bare), str(work2)], check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "ops"], cwd=work2, check=True)
    (work2 / "BRANCH_MARKER").write_text("ops\n")
    subprocess.run(["git", "add", "-A"], cwd=work2, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "ops"], cwd=work2, env=_git_env(), check=True)
    subprocess.run(["git", "push", "-q", "origin", "ops"], cwd=work2, check=True)
    instance = tmp_path / "instance"
    instance.mkdir()
    ok = hydrate_instance_from_repo(str(instance), repo=url, branch="ops")
    assert ok is True
    assert (instance / "BRANCH_MARKER").exists()


def test_fail_open_on_bad_repo(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    ok = hydrate_instance_from_repo(str(instance), repo="file:///nonexistent/repo.git")
    assert ok is False
    assert list(instance.iterdir()) == []  # left clean for template fallback


def test_pull_is_noop_without_git(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    assert pull_instance_repo(str(instance)) is False  # not a repo → graceful


def test_pull_applies_remote_commit(tmp_path):
    url = _make_source_repo(tmp_path)
    instance = tmp_path / "instance"
    subprocess.run(["git", "clone", "-q", url, str(instance)], check=True)
    # operator edits the remote directly
    bare = url.removeprefix("file://")
    op = tmp_path / "op"
    subprocess.run(["git", "clone", "-q", str(bare), str(op)], check=True)
    (op / "soul.md").write_text("updated by operator\n")
    subprocess.run(["git", "add", "-A"], cwd=op, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "op edit"], cwd=op, env=_git_env(), check=True)
    subprocess.run(["git", "push", "-q"], cwd=op, check=True)
    assert pull_instance_repo(str(instance)) is True
    assert (instance / "soul.md").read_text() == "updated by operator\n"
