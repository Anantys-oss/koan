"""Tests for the /api/efficiency endpoint."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import os
os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.dashboard import app


@pytest.fixture
def client(tmp_path):
    app.config["TESTING"] = True
    with patch("app.dashboard.INSTANCE_DIR", tmp_path):
        outcomes = [
            {
                "timestamp": datetime.now().isoformat(),
                "project": "alpha",
                "outcome": "productive",
                "mode": "implement",
                "duration_minutes": 10,
                "mission_type": "implement",
            },
            {
                "timestamp": datetime.now().isoformat(),
                "project": "alpha",
                "outcome": "empty",
                "mode": "review",
                "duration_minutes": 5,
                "mission_type": "review",
            },
            {
                "timestamp": datetime.now().isoformat(),
                "project": "alpha",
                "outcome": "blocked",
                "mode": "implement",
                "duration_minutes": 3,
                "mission_type": "implement",
            },
        ]
        (tmp_path / "session_outcomes.json").write_text(json.dumps(outcomes))

        usage_dir = tmp_path / "usage"
        usage_dir.mkdir()
        today_str = date.today().isoformat()
        lines = [
            json.dumps({"ts": datetime.now().isoformat(), "project": "alpha",
                        "model": "claude-sonnet-4-20250514", "input_tokens": 8000,
                        "output_tokens": 2000}),
            json.dumps({"ts": datetime.now().isoformat(), "project": "alpha",
                        "model": "claude-sonnet-4-20250514", "input_tokens": 4000,
                        "output_tokens": 1000}),
        ]
        (usage_dir / f"{today_str}.jsonl").write_text("\n".join(lines) + "\n")

        with app.test_client() as c:
            yield c


def test_efficiency_basic(client):
    resp = client.get("/api/efficiency?days=7")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "alpha" in data["by_project"]
    alpha = data["by_project"]["alpha"]
    assert alpha["productive_count"] == 1
    assert alpha["empty_count"] == 1
    assert alpha["blocked_count"] == 1
    assert alpha["total_tokens"] == 15000
    assert alpha["tokens_per_productive_outcome"] == 15000
    assert 0.66 < alpha["waste_pct"] < 0.67


def test_efficiency_no_data(tmp_path):
    app.config["TESTING"] = True
    with patch("app.dashboard.INSTANCE_DIR", tmp_path):
        (tmp_path / "session_outcomes.json").write_text("[]")
        with app.test_client() as c:
            resp = c.get("/api/efficiency?days=7")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["by_project"] == {}


def test_efficiency_cost_only_project(client, tmp_path):
    """Project in cost data but not in outcome data."""
    with patch("app.dashboard.INSTANCE_DIR", tmp_path):
        (tmp_path / "session_outcomes.json").write_text("[]")
        usage_dir = tmp_path / "usage"
        usage_dir.mkdir(exist_ok=True)
        today_str = date.today().isoformat()
        line = json.dumps({"ts": datetime.now().isoformat(), "project": "orphan",
                           "model": "m", "input_tokens": 5000, "output_tokens": 1000})
        (usage_dir / f"{today_str}.jsonl").write_text(line + "\n")
        resp = client.get("/api/efficiency?days=7")
        data = resp.get_json()
        orphan = data["by_project"]["orphan"]
        assert orphan["productive_count"] == 0
        assert orphan["total_tokens"] == 6000
        assert orphan["tokens_per_productive_outcome"] is None
        assert orphan["waste_pct"] == 1.0
