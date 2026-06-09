"""Tests for onboarding helper functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_onboarding_needed_when_instance_missing(tmp_path):
    from app.onboarding_helpers import onboarding_needed

    assert onboarding_needed(tmp_path) is True


def test_onboarding_needed_when_checkpoint_exists(tmp_path):
    from app.onboarding_helpers import onboarding_needed

    (tmp_path / "instance").mkdir()
    (tmp_path / ".env").write_text("KOAN_ROOT=/tmp\n")
    (tmp_path / ".koan-onboarding.json").write_text("{}")

    assert onboarding_needed(tmp_path) is True


def test_onboarding_not_needed_when_instance_and_env_exist(tmp_path):
    from app.onboarding_helpers import onboarding_needed

    (tmp_path / "instance").mkdir()
    (tmp_path / ".env").write_text("KOAN_ROOT=/tmp\n")

    assert onboarding_needed(tmp_path) is False


def test_setup_workspace_koan_clones_when_missing(tmp_path):
    from app.onboarding_helpers import KOAN_REPO_URL, setup_workspace_koan

    proc = MagicMock(returncode=0, stdout="", stderr="")
    with patch("app.onboarding_helpers.subprocess.run", return_value=proc) as run:
        ok, message = setup_workspace_koan(tmp_path)

    assert ok is True
    assert "cloned" in message
    assert (tmp_path / "workspace").is_dir()
    run.assert_called_once_with(
        ["git", "clone", KOAN_REPO_URL, str(tmp_path / "workspace" / "koan")],
        cwd=str(tmp_path / "workspace"),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_setup_workspace_koan_accepts_existing_valid_clone(tmp_path):
    from app.onboarding_helpers import setup_workspace_koan

    path = tmp_path / "workspace" / "koan"
    path.mkdir(parents=True)

    with patch("app.onboarding_helpers._has_koan_remote", return_value=True):
        ok, message = setup_workspace_koan(tmp_path)

    assert ok is True
    assert "already configured" in message


def test_setup_workspace_koan_blocks_conflicting_directory(tmp_path):
    from app.onboarding_helpers import setup_workspace_koan

    path = tmp_path / "workspace" / "koan"
    path.mkdir(parents=True)

    with patch("app.onboarding_helpers._has_koan_remote", return_value=False):
        ok, message = setup_workspace_koan(tmp_path)

    assert ok is False
    assert "does not point" in message


def test_create_instance_and_env_with_explicit_root(tmp_path):
    from app.onboarding_helpers import create_env_file, create_instance_dir

    (tmp_path / "instance.example").mkdir()
    (tmp_path / "instance.example" / "config.yaml").write_text("x: 1\n")
    (tmp_path / "env.example").write_text("# env\n")

    assert create_instance_dir(tmp_path) is True
    assert create_env_file(tmp_path) is True
    assert (tmp_path / "instance" / "config.yaml").exists()
    assert (tmp_path / ".env").read_text() == "# env\n"
