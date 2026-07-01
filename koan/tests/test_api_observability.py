import pytest

from app.api import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KOAN_API_TOKEN", "secret123")
    (tmp_path / "instance").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run.log").write_text("hello run\n", encoding="utf-8")
    # create_app already accepts these kwargs and stores them in app.config.
    app = create_app(koan_root=tmp_path, instance_dir=tmp_path / "instance")
    app.config["TESTING"] = True
    return app.test_client()


def _auth():
    return {"Authorization": "Bearer secret123"}


def test_usage_requires_token(client):
    assert client.get("/v1/usage").status_code == 401


def test_usage_ok(client):
    r = client.get("/v1/usage?days=7", headers=_auth())
    assert r.status_code == 200
    assert r.get_json()["days"] == 7


def test_metrics_requires_token(client):
    assert client.get("/v1/metrics").status_code == 401


def test_metrics_ok(client):
    r = client.get("/v1/metrics?days=30", headers=_auth())
    assert r.status_code == 200
    assert "by_project" in r.get_json()


def test_metrics_reports_security_blocks(tmp_path, monkeypatch):
    """Global metrics expose security_blocks_7d sourced from .security-audit.jsonl."""
    import json

    monkeypatch.setenv("KOAN_API_TOKEN", "secret123")
    instance = tmp_path / "instance"
    instance.mkdir()
    (tmp_path / "logs").mkdir()
    audit = instance / ".security-audit.jsonl"
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    audit.write_text(
        json.dumps({"ts": now, "approved": False, "project": "p"}) + "\n"
        + json.dumps({"ts": now, "approved": True, "project": "p"}) + "\n",
        encoding="utf-8",
    )
    app = create_app(koan_root=tmp_path, instance_dir=instance)
    app.config["TESTING"] = True
    r = app.test_client().get("/v1/metrics?days=30", headers=_auth())
    assert r.status_code == 200
    assert r.get_json()["security_blocks_7d"] == 1


def test_logs_ok(client):
    r = client.get("/v1/logs?source=run&limit=100", headers=_auth())
    assert r.status_code == 200
    body = r.get_json()
    assert body["lines"][0]["text"] == "hello run"
    assert body["lines"][0]["n"] == 1


def test_logs_requires_token(client):
    assert client.get("/v1/logs").status_code == 401


def test_usage_bad_days_returns_422(client):
    r = client.get("/v1/usage?days=abc", headers=_auth())
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == "invalid_request"


def test_metrics_bad_days_returns_422(client):
    r = client.get("/v1/metrics?days=oops", headers=_auth())
    assert r.status_code == 422


def test_logs_bad_limit_returns_422(client):
    r = client.get("/v1/logs?limit=xyz", headers=_auth())
    assert r.status_code == 422
