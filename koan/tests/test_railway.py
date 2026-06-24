"""Tests for hosted-deploy (Railway) helpers and env-var-aware onboarding."""

from app import railway
from app.onboarding_helpers import create_env_file, has_instance
from app.projects_config import (
    invalidate_projects_config_cache,
    load_projects_config,
)
from app.workspace_discovery import discover_workspace_projects


def _set_full_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("GH_TOKEN", "gh")
    monkeypatch.setenv("KOAN_TELEGRAM_TOKEN", "tg")
    monkeypatch.setenv("KOAN_TELEGRAM_CHAT_ID", "42")


# --- env presence -----------------------------------------------------------

def test_required_env_present(monkeypatch):
    _set_full_env(monkeypatch)
    assert railway.required_env_present() is True


def test_required_env_accepts_api_key(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("KOAN_TELEGRAM_TOKEN", "x")
    monkeypatch.setenv("KOAN_TELEGRAM_CHAT_ID", "x")
    assert railway.required_env_present() is True


def test_required_env_missing(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert railway.required_env_present() is False


def test_is_railway(monkeypatch):
    monkeypatch.setenv("KOAN_DEPLOY", "railway")
    assert railway.is_railway() is True
    monkeypatch.setenv("KOAN_DEPLOY", "local")
    assert railway.is_railway() is False


# --- has_instance / env path ------------------------------------------------

def test_has_instance_true_via_env(tmp_path, monkeypatch):
    (tmp_path / "instance").mkdir()
    monkeypatch.setenv("KOAN_DEPLOY", "railway")
    _set_full_env(monkeypatch)
    assert has_instance(tmp_path) is True  # no .env on disk


def test_has_instance_unchanged_local(tmp_path, monkeypatch):
    monkeypatch.delenv("KOAN_DEPLOY", raising=False)
    (tmp_path / "instance").mkdir()
    assert has_instance(tmp_path) is False


# --- .env synthesis (#2076) -------------------------------------------------

def test_create_env_file_synthesizes_without_example(tmp_path, monkeypatch):
    _set_full_env(monkeypatch)
    monkeypatch.setenv("KOAN_DEPLOY", "railway")
    assert create_env_file(tmp_path) is True
    body = (tmp_path / ".env").read_text()
    assert "GH_TOKEN=gh" in body
    assert "CLAUDE_CODE_OAUTH_TOKEN=tok" in body


def test_create_env_file_false_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert create_env_file(tmp_path) is False


def test_write_env_preserves_existing_keys(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CUSTOM_KEY=keepme\n")
    monkeypatch.setenv("GH_TOKEN", "gh")
    railway.write_env_from_environment(env_file)
    txt = env_file.read_text()
    assert "CUSTOM_KEY=keepme" in txt
    assert "GH_TOKEN=gh" in txt


# --- daemon detection -------------------------------------------------------

def test_daemon_running_false(tmp_path):
    assert railway.daemon_running(tmp_path) is False


# --- volume writability -----------------------------------------------------

def test_ensure_volume_writable_ok(tmp_path):
    ok, msg = railway.ensure_volume_writable(tmp_path / "instance")
    assert ok is True
    assert msg == "writable"


# --- instance-priority resolution (#2074) -----------------------------------

def test_projects_yaml_instance_priority(tmp_path):
    invalidate_projects_config_cache()
    (tmp_path / "projects.yaml").write_text(
        "projects:\n  root_proj:\n    path: /tmp/r\n")
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "projects.yaml").write_text(
        "projects:\n  vol_proj:\n    path: /tmp/v\n")
    cfg = load_projects_config(str(tmp_path))
    assert "vol_proj" in cfg["projects"]
    assert "root_proj" not in cfg["projects"]


def test_projects_yaml_root_fallback(tmp_path):
    invalidate_projects_config_cache()
    (tmp_path / "projects.yaml").write_text(
        "projects:\n  root_proj:\n    path: /tmp/r\n")
    cfg = load_projects_config(str(tmp_path))
    assert "root_proj" in cfg["projects"]


def test_workspace_discovery_prefers_instance(tmp_path):
    (tmp_path / "instance" / "workspace" / "alpha").mkdir(parents=True)
    (tmp_path / "workspace" / "beta").mkdir(parents=True)
    names = [n for n, _ in discover_workspace_projects(str(tmp_path))]
    assert names == ["alpha"]


def test_workspace_discovery_root_fallback(tmp_path):
    (tmp_path / "workspace" / "beta").mkdir(parents=True)
    names = [n for n, _ in discover_workspace_projects(str(tmp_path))]
    assert names == ["beta"]
