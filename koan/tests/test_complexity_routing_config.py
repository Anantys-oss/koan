"""Tests for app.config.get_complexity_routing_config."""

import pytest
from unittest.mock import patch


class TestGetComplexityRoutingConfig:
    def _config(self, routing_section):
        """Build a minimal config dict with the given complexity_routing section."""
        return {"complexity_routing": routing_section} if routing_section is not None else {}

    def test_disabled_when_not_in_config(self):
        from app.config import get_complexity_routing_config
        with patch("app.config._load_config", return_value={}):
            with patch("app.config._load_project_overrides", return_value={}):
                result = get_complexity_routing_config()
        assert result is None

    def test_disabled_when_enabled_false(self):
        from app.config import get_complexity_routing_config
        cfg = self._config({"enabled": False})
        with patch("app.config._load_config", return_value=cfg):
            with patch("app.config._load_project_overrides", return_value={}):
                result = get_complexity_routing_config()
        assert result is None

    def test_enabled_returns_default_tiers(self):
        from app.config import get_complexity_routing_config
        cfg = self._config({"enabled": True})
        with patch("app.config._load_config", return_value=cfg):
            with patch("app.config._load_project_overrides", return_value={}):
                result = get_complexity_routing_config()
        assert result is not None
        assert result["enabled"] is True
        tiers = result["tiers"]
        assert set(tiers.keys()) == {"trivial", "simple", "medium", "complex"}
        assert tiers["trivial"]["model"] == "haiku"
        assert tiers["trivial"]["max_turns"] == 10
        assert tiers["complex"]["max_turns"] == 80
        assert tiers["medium"]["model"] == ""  # no override

    def test_partial_tier_override(self):
        from app.config import get_complexity_routing_config
        cfg = self._config({
            "enabled": True,
            "tiers": {
                "trivial": {"model": "custom-haiku", "max_turns": 5},
            },
        })
        with patch("app.config._load_config", return_value=cfg):
            with patch("app.config._load_project_overrides", return_value={}):
                result = get_complexity_routing_config()
        assert result["tiers"]["trivial"]["model"] == "custom-haiku"
        assert result["tiers"]["trivial"]["max_turns"] == 5
        # Other tiers should still have defaults
        assert result["tiers"]["complex"]["max_turns"] == 80

    def test_project_false_overrides_global_enabled(self):
        from app.config import get_complexity_routing_config
        cfg = self._config({"enabled": True})
        with patch("app.config._load_config", return_value=cfg):
            with patch("app.config._load_project_overrides", return_value={"complexity_routing": False}):
                result = get_complexity_routing_config("myproject")
        assert result is None

    def test_project_enabled_false_dict(self):
        from app.config import get_complexity_routing_config
        cfg = self._config({"enabled": True})
        with patch("app.config._load_config", return_value=cfg):
            with patch("app.config._load_project_overrides", return_value={"complexity_routing": {"enabled": False}}):
                result = get_complexity_routing_config("myproject")
        assert result is None

    def test_project_tier_overrides_global_tiers(self):
        from app.config import get_complexity_routing_config
        cfg = self._config({"enabled": True, "tiers": {"trivial": {"model": "haiku"}}})
        project_override = {
            "complexity_routing": {
                "tiers": {"trivial": {"model": "project-haiku", "max_turns": 3}},
            }
        }
        with patch("app.config._load_config", return_value=cfg):
            with patch("app.config._load_project_overrides", return_value=project_override):
                result = get_complexity_routing_config("myproject")
        assert result["tiers"]["trivial"]["model"] == "project-haiku"
        assert result["tiers"]["trivial"]["max_turns"] == 3
