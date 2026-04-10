"""Tests for the /config_check core skill — config drift detection."""

from unittest.mock import patch

import yaml

from app.skills import SkillContext


def _make_ctx(tmp_path):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="config_check",
        args="",
    )


def _write_template(tmp_path, config):
    template_dir = tmp_path / "instance.example"
    template_dir.mkdir(exist_ok=True)
    (template_dir / "config.yaml").write_text(yaml.dump(config))


def _write_user_config(tmp_path, config):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    (instance_dir / "config.yaml").write_text(yaml.dump(config))


class TestConfigCheckSkill:
    def test_reports_in_sync_when_identical(self, tmp_path):
        from skills.core.config_check.handler import handle

        config = {"max_runs_per_day": 20, "debug": False}
        _write_template(tmp_path, config)
        _write_user_config(tmp_path, config)

        ctx = _make_ctx(tmp_path)
        with patch("skills.core.config_check.handler.load_config", return_value=config):
            result = handle(ctx)
        assert "in sync" in result

    def test_reports_missing_keys(self, tmp_path):
        from skills.core.config_check.handler import handle

        template = {"max_runs_per_day": 20, "new_feature": True}
        user = {"max_runs_per_day": 20}
        _write_template(tmp_path, template)
        _write_user_config(tmp_path, user)

        ctx = _make_ctx(tmp_path)
        with patch("skills.core.config_check.handler.load_config", return_value=user):
            result = handle(ctx)
        assert "Missing" in result
        assert "new_feature" in result

    def test_reports_extra_keys(self, tmp_path):
        from skills.core.config_check.handler import handle

        template = {"max_runs_per_day": 20}
        user = {"max_runs_per_day": 20, "old_removed_setting": "x"}
        _write_template(tmp_path, template)
        _write_user_config(tmp_path, user)

        ctx = _make_ctx(tmp_path)
        with patch("skills.core.config_check.handler.load_config", return_value=user):
            result = handle(ctx)
        assert "Extra" in result
        assert "old_removed_setting" in result

    def test_reports_both_directions(self, tmp_path):
        from skills.core.config_check.handler import handle

        template = {"shared": 1, "in_template_only": 2}
        user = {"shared": 1, "in_user_only": 3}
        _write_template(tmp_path, template)
        _write_user_config(tmp_path, user)

        ctx = _make_ctx(tmp_path)
        with patch("skills.core.config_check.handler.load_config", return_value=user):
            result = handle(ctx)
        assert "in_template_only" in result
        assert "in_user_only" in result
        assert "Missing" in result
        assert "Extra" in result

    def test_missing_template_returns_error(self, tmp_path):
        from skills.core.config_check.handler import handle

        _write_user_config(tmp_path, {"a": 1})
        ctx = _make_ctx(tmp_path)
        result = handle(ctx)
        assert "Template not found" in result

    def test_missing_user_config_returns_error(self, tmp_path):
        from skills.core.config_check.handler import handle

        _write_template(tmp_path, {"a": 1})
        ctx = _make_ctx(tmp_path)
        result = handle(ctx)
        assert "config.yaml not found" in result
