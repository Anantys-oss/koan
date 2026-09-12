"""Tests for API-safe skill catalog shaping and publication."""

import os
import textwrap
from unittest.mock import patch

import pytest
from app.api import create_app
from app.api.skill_catalog import (
    API_COMMAND_NAMES,
    build_skill_catalog,
    canonical_command_name,
    exposed_core_skills,
)
from app.skills import SkillRegistry

_TOKEN = "test-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_EXPOSED = {
    "audit",
    "brief",
    "ci_check",
    "doc",
    "explain",
    "fix",
    "gh_request",
    "implement",
    "plan",
    "rebase",
    "review",
}


@pytest.fixture
def api_client(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    with patch.dict(
        os.environ,
        {"KOAN_API_TOKEN": _TOKEN, "KOAN_ROOT": str(tmp_path)},
    ):
        app = create_app(koan_root=tmp_path, instance_dir=instance)
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client


def _write_skill(root, scope, name, *, exposed):
    skill_dir = root / scope / name
    skill_dir.mkdir(parents=True)
    flag = "        api_exposed: true\n" if exposed else ""
    (skill_dir / "SKILL.md").write_text(textwrap.dedent(f"""\
        ---
        name: {name}
        scope: {scope}
        group: code
        description: {name.title()} skill
{flag}        commands:
          - name: {name}
            description: Run {name}
            usage: /{name} https://github.com/owner/repo/issues/42
            aliases: [{name}_alias]
        ---
    """))


def test_catalog_contains_only_exposed_core_skills(tmp_path):
    _write_skill(tmp_path, "core", "visible", exposed=True)
    _write_skill(tmp_path, "core", "hidden", exposed=False)
    _write_skill(tmp_path, "my_team", "private_skill", exposed=True)
    registry = SkillRegistry(tmp_path)

    catalog = build_skill_catalog(exposed_core_skills(registry))

    assert catalog == [
        {
            "name": "visible",
            "description": "Visible skill",
            "group": "code",
            "emoji": "",
            "commands": [
                {
                    "name": "/visible",
                    "description": "Run visible",
                    "usage": (
                        "/visible "
                        "https://github.com/owner/repo/issues/42"
                    ),
                    "aliases": ["/visible_alias"],
                }
            ],
        }
    ]


def test_accepted_command_names_include_advertised_aliases():
    # Canonical names and every advertised alias appear in the surface
    # koan_missions_create advertises, and aliases normalize to the canonical
    # verb.
    names = set(API_COMMAND_NAMES)
    assert "/review" in names
    assert "/rv" in names
    assert "/rb" in names
    assert canonical_command_name("rv") == "review"
    assert canonical_command_name("rb") == "rebase"
    # A verb outside the advertised set comes back unchanged rather than
    # raising — the catalogue advertises, it does not gate.
    assert canonical_command_name("shutdown") == "shutdown"


def test_list_skills_requires_authentication(api_client):
    response = api_client.get("/v1/skills")

    assert response.status_code == 401


def test_list_skills_returns_only_exposed_core_catalog(api_client):
    response = api_client.get("/v1/skills", headers=_AUTH)

    assert response.status_code == 200
    catalog = response.get_json()
    assert {skill["name"] for skill in catalog} == _EXPOSED
    assert "shutdown" not in {skill["name"] for skill in catalog}
    assert "delete_project" not in {skill["name"] for skill in catalog}


def test_review_catalog_entry_includes_usage_and_flags(api_client):
    response = api_client.get("/v1/skills", headers=_AUTH)

    review = next(
        skill
        for skill in response.get_json()
        if skill["name"] == "review"
    )
    command = review["commands"][0]
    assert review["group"] == "code"
    assert review["emoji"] == "🔍"
    assert command["name"] == "/review"
    assert "--architecture" in command["description"]
    assert "--force" in command["description"]
    assert command["usage"].startswith("/review ")
    assert "/rv" in command["aliases"]
