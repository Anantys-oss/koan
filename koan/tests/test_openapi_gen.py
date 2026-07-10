"""Behavior tests for the OpenAPI generator (app.api.openapi_gen).

These assert that the generated document matches the live Flask route table
(paths, methods, auth), that generation is deterministic, and that the drift
check works — testing observable outputs, never source text.
"""

import re

import pytest
import yaml

from app.api import create_app
from app.api import openapi_gen


@pytest.fixture
def app(tmp_path):
    return create_app(koan_root=tmp_path, instance_dir=tmp_path / "instance")


def _expected_operations(app):
    """The (METHOD, openapi-path) pairs the live app actually serves (API only)."""
    expected = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint in openapi_gen._IGNORED_ENDPOINTS:
            continue
        methods = (rule.methods or set()) - openapi_gen._IGNORED_METHODS
        path = openapi_gen._openapi_path(rule.rule)
        for m in methods:
            expected.add((m.upper(), path))
    return expected


def _spec_operations(spec):
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
    }


def test_spec_matches_live_route_table(app):
    """Every registered route appears exactly once; no extra paths (FR-002, FR-004)."""
    spec = openapi_gen.build_spec(app)
    assert _spec_operations(spec) == _expected_operations(app)


def test_no_werkzeug_path_syntax_leaks(app):
    """Path params are rendered in OpenAPI {name} form, not Werkzeug <name> form."""
    spec = openapi_gen.build_spec(app)
    for path in spec["paths"]:
        assert "<" not in path and ">" not in path
    # A known parameterized route is present in braced form.
    assert "/v1/missions/{mission_id}" in spec["paths"]


def test_path_parameters_declared(app):
    spec = openapi_gen.build_spec(app)
    op = spec["paths"]["/v1/missions/{mission_id}"]["get"]
    params = {p["name"]: p for p in op.get("parameters", [])}
    assert "mission_id" in params
    assert params["mission_id"]["in"] == "path"
    assert params["mission_id"]["required"] is True


def test_health_is_public(app):
    """The unauthenticated liveness probe overrides the global security (FR-005)."""
    spec = openapi_gen.build_spec(app)
    assert spec["paths"]["/v1/health"]["get"]["security"] == []


def test_secured_route_inherits_global_bearer(app):
    """A token-protected route carries no per-op security (inherits bearerAuth)."""
    spec = openapi_gen.build_spec(app)
    assert "security" not in spec["paths"]["/v1/status"]["get"]
    # Global default requires the bearer scheme.
    assert spec["security"] == [{"bearerAuth": []}]


def test_security_scheme_and_error_component(app):
    spec = openapi_gen.build_spec(app)
    scheme = spec["components"]["securitySchemes"]["bearerAuth"]
    assert scheme == {"type": "http", "scheme": "bearer"}
    error = spec["components"]["schemas"]["Error"]
    assert error["properties"]["error"]["required"] == ["code", "message"]


def test_known_non_default_success_codes(app):
    spec = openapi_gen.build_spec(app)
    assert "202" in spec["paths"]["/v1/missions"]["post"]["responses"]
    assert "201" in spec["paths"]["/v1/projects"]["post"]["responses"]


def test_known_success_codes_declared(app):
    """Every SUCCESS_STATUS entry maps to a live route and applies its code.

    Guards the one hand-maintained map in the generator: a renamed/removed route
    leaves a stale entry (caught here), and this locks the declared code onto the
    operation. See the KEEP IN SYNC note in openapi_gen.py — add the entry and an
    assertion here together when a route starts returning a non-200 success.
    """
    spec = openapi_gen.build_spec(app)
    for (method, path), code in openapi_gen.SUCCESS_STATUS.items():
        assert path in spec["paths"], f"stale SUCCESS_STATUS entry: {path} is not a live route"
        operation = spec["paths"][path].get(method)
        assert operation is not None, f"stale SUCCESS_STATUS entry: {method.upper()} {path} not served"
        assert code in operation["responses"], (
            f"{method.upper()} {path} should declare success status {code}"
        )
        assert "200" not in operation["responses"], (
            f"{method.upper()} {path} has a declared non-200 success; 200 must not also appear"
        )


def test_document_is_valid_openapi_31(app):
    spec = openapi_gen.build_spec(app)
    assert spec["openapi"].startswith("3.1")
    assert spec["info"]["version"] == openapi_gen.API_VERSION
    assert spec["paths"]  # non-empty
    # Round-trips through YAML and back unchanged.
    text = openapi_gen.dump_yaml(spec)
    assert yaml.safe_load(text) == spec


def test_generation_is_deterministic(app, tmp_path):
    """Two independent builds produce byte-identical YAML (FR-006, SC-002)."""
    app2 = create_app(koan_root=tmp_path, instance_dir=tmp_path / "instance2")
    assert openapi_gen.dump_yaml(openapi_gen.build_spec(app)) == openapi_gen.dump_yaml(
        openapi_gen.build_spec(app2)
    )


def test_yaml_header_present(app):
    text = openapi_gen.dump_yaml(openapi_gen.build_spec(app))
    assert text.startswith("#")
    assert "do not edit by hand" in text.lower()


def test_check_passes_on_fresh_file(tmp_path, monkeypatch):
    """check() returns 0 when the file matches freshly-generated output (FR-007)."""
    out = tmp_path / "openapi.yaml"
    out.write_text(openapi_gen.render(), encoding="utf-8")
    assert openapi_gen.check(out) == 0


def test_check_detects_drift_and_prints_instruction(tmp_path, capsys):
    """check() returns 1 and prints the fix command when the file is stale (FR-010)."""
    out = tmp_path / "openapi.yaml"
    out.write_text("openapi: 3.1.0\npaths: {}\n", encoding="utf-8")
    rc = openapi_gen.check(out)
    assert rc == 1
    err = capsys.readouterr().err
    assert "make openapi" in err
    assert "out of date" in err


def test_check_missing_file_reports_drift(tmp_path):
    out = tmp_path / "does-not-exist.yaml"
    assert openapi_gen.check(out) == 1


def test_cli_generate_then_check(tmp_path):
    """The CLI writes a file that its own --check then accepts."""
    out = tmp_path / "openapi.yaml"
    assert openapi_gen.main(["--output", str(out)]) == 0
    assert out.exists()
    assert openapi_gen.main(["--check", "--output", str(out)]) == 0


def test_committed_artifact_is_current():
    """The checked-in koan/openapi.yaml matches the current code (guards the repo)."""
    from pathlib import Path

    repo_koan = Path(__file__).resolve().parents[1]  # koan/tests -> koan/
    committed = repo_koan / "openapi.yaml"
    assert committed.exists(), "koan/openapi.yaml missing — run `make openapi`"
    assert committed.read_text(encoding="utf-8") == openapi_gen.render(), (
        "koan/openapi.yaml is stale — run `make openapi` and commit it"
    )


def test_operation_ids_unique(app):
    spec = openapi_gen.build_spec(app)
    ids = [
        op["operationId"]
        for item in spec["paths"].values()
        for op in item.values()
    ]
    assert len(ids) == len(set(ids))


def test_tags_cover_blueprints(app):
    spec = openapi_gen.build_spec(app)
    tag_names = {t["name"] for t in spec["tags"]}
    # Blueprint-derived tags for the known blueprints plus the inline health route.
    assert {"missions", "projects", "admin", "status", "observability", "health"} <= tag_names
    # Every path param name is a valid identifier fragment (sanity on the regex).
    for path in spec["paths"]:
        for name in re.findall(r"{([^{}]+)}", path):
            assert name.isidentifier()
