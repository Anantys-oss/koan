"""Tests for the /models skill handler.

Covers the per-role custom-CLI annotation added on top of the model listing:
when a mission role is routed to a custom CLI (`cli:` config section), the
resolved binary/flavor is shown next to that role's model.
"""

from unittest.mock import patch

from app.skills import SkillContext

from skills.core.models.handler import handle


def _make_ctx(tmp_path, project_name=""):
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(exist_ok=True)
    return SkillContext(
        koan_root=tmp_path,
        instance_dir=instance_dir,
        command_name="models",
        project_name=project_name,
    )


def _run(tmp_path, models, cli_roles, cli_fallback=("", ""), provider="claude"):
    with (
        patch("app.provider.get_provider_name", return_value=provider),
        patch("app.config.get_model_config", return_value=models),
        patch("app.config.get_cli_config", return_value=cli_roles),
        patch("app.config.get_cli_fallback", return_value=cli_fallback),
    ):
        return handle(_make_ctx(tmp_path))


def test_no_custom_cli_shows_plain_model_lines(tmp_path):
    """Default setup (every role on the global provider) stays annotation-free."""
    models = {
        "mission": "opus",
        "chat": "sonnet",
        "lightweight": "haiku",
        "fallback": "sonnet",
        "review_mode": "",
        "reflect": "",
    }
    cli_roles = {r: ("claude", "") for r in
                 ("mission", "chat", "lightweight", "review_mode", "reflect")}
    out = _run(tmp_path, models, cli_roles)

    assert "Models for provider: claude" in out
    assert "mission: opus" in out
    assert "review_mode: (provider default)" in out
    assert "[cli:" not in out


def test_custom_binary_path_shows_basename(tmp_path):
    """A role pinned to a binary path surfaces that binary's basename."""
    models = {"mission": "opus", "review_mode": "opus"}
    cli_roles = {
        "mission": ("claude", ""),
        "chat": ("claude", ""),
        "lightweight": ("claude", ""),
        "review_mode": ("claude", "/root/.local/bin/claude-deep"),
        "reflect": ("claude", ""),
    }
    out = _run(tmp_path, models, cli_roles)

    assert "review_mode: opus  [cli: claude-deep]" in out
    # Roles without an override are not annotated.
    assert "mission: opus" in out
    assert "mission: opus  [cli:" not in out


def test_flavor_override_without_path_shows_flavor(tmp_path):
    """A different provider flavor (no pinned path) shows the flavor name."""
    models = {"mission": "opus"}
    cli_roles = {
        "mission": ("codex", ""),
        "chat": ("claude", ""),
        "lightweight": ("claude", ""),
        "review_mode": ("claude", ""),
        "reflect": ("claude", ""),
    }
    out = _run(tmp_path, models, cli_roles)

    assert "mission: opus  [cli: codex]" in out


def test_fallback_role_uses_cli_fallback(tmp_path):
    """The fallback slot is annotated from get_cli_fallback, not get_cli_config."""
    models = {"fallback": "sonnet"}
    cli_roles = {r: ("claude", "") for r in
                 ("mission", "chat", "lightweight", "review_mode", "reflect")}
    out = _run(tmp_path, models, cli_roles,
               cli_fallback=("claude", "/opt/bin/claude-fast"))

    assert "fallback: sonnet  [cli: claude-fast]" in out


def test_cli_config_failure_degrades_gracefully(tmp_path):
    """A broken cli: config must not break the model listing."""
    models = {"mission": "opus"}
    with (
        patch("app.provider.get_provider_name", return_value="claude"),
        patch("app.config.get_model_config", return_value=models),
        patch("app.config.get_cli_config", side_effect=RuntimeError("boom")),
        patch("app.config.get_cli_fallback", side_effect=RuntimeError("boom")),
    ):
        out = handle(_make_ctx(tmp_path))

    assert "mission: opus" in out
    assert "[cli:" not in out
