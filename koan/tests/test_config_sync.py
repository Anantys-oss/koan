import yaml
from pathlib import Path

import pytest

from app import config_sync


@pytest.fixture
def koan_root(tmp_path):
    (tmp_path / "instance").mkdir()
    return tmp_path


def _write_config(koan_root: Path, data: dict):
    (koan_root / "instance" / "config.yaml").write_text(yaml.safe_dump(data))


def test_synced_when_baseline_matches(koan_root):
    _write_config(koan_root, {"dashboard": {"nickname": "Koan"}})
    config_sync.write_baseline(koan_root)
    status = config_sync.compute_status(koan_root)
    assert status["synced"] is True
    assert status["restart_pending"] is False
    assert status["changed_safe_keys"] == []
    assert status["changed_unsafe_keys"] == []


def test_safe_change_is_hot_not_restart(koan_root):
    _write_config(koan_root, {"dashboard": {"nickname": "Koan"}})
    config_sync.write_baseline(koan_root)
    _write_config(koan_root, {"dashboard": {"nickname": "Newname"}})
    status = config_sync.compute_status(koan_root)
    assert status["restart_pending"] is False
    assert "dashboard.nickname" in status["changed_safe_keys"]
    assert status["changed_unsafe_keys"] == []


def test_unsafe_change_sets_restart_pending(koan_root):
    _write_config(koan_root, {"cli_provider": "claude"})
    config_sync.write_baseline(koan_root)
    _write_config(koan_root, {"cli_provider": "copilot"})
    status = config_sync.compute_status(koan_root)
    assert status["restart_pending"] is True
    assert "cli_provider" in status["changed_unsafe_keys"]


def test_projects_yaml_change_is_unsafe(koan_root):
    pj = koan_root / "instance" / "projects.yaml"
    pj.write_text(yaml.safe_dump({"projects": {"a": {"path": "/x"}}}))
    config_sync.write_baseline(koan_root)
    pj.write_text(yaml.safe_dump({"projects": {"a": {"path": "/y"}}}))
    status = config_sync.compute_status(koan_root)
    assert status["restart_pending"] is True
    assert any(k.startswith("projects.yaml") for k in status["changed_unsafe_keys"])


def test_unknown_key_defaults_to_unsafe(koan_root):
    _write_config(koan_root, {})
    config_sync.write_baseline(koan_root)
    _write_config(koan_root, {"some_new_section": {"x": 1}})
    status = config_sync.compute_status(koan_root)
    assert status["restart_pending"] is True


def test_missing_baseline_reports_synced(koan_root):
    # No baseline written yet -> never block; treat as synced.
    _write_config(koan_root, {"dashboard": {"nickname": "Koan"}})
    status = config_sync.compute_status(koan_root)
    assert status["synced"] is True
