"""Tests for governor_daily_report — data collection, narrative, storage."""

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def env_setup(tmp_path, monkeypatch):
    koan_root = tmp_path / "koan"
    koan_root.mkdir()
    instance = koan_root / "instance"
    instance.mkdir()
    (instance / "config.yaml").write_text(
        "advisor:\n  enabled: false\n  summary_model: claude-haiku\n"
        "budget_controller:\n  litellm_url: http://localhost:4000\n"
    )

    events_dir = instance / "watcher" / "events"
    events_dir.mkdir(parents=True)

    monkeypatch.setenv("KOAN_ROOT", str(koan_root))

    import app.utils
    monkeypatch.setattr(app.utils, "KOAN_ROOT", koan_root)
    import app.governor_daily_report as mod
    monkeypatch.setattr(mod, "KOAN_ROOT", koan_root)
    monkeypatch.setattr(mod, "INSTANCE_DIR", instance)
    monkeypatch.setattr(mod, "REPORTS_DIR", instance / "reports")

    return {"koan_root": koan_root, "instance": instance, "events_dir": events_dir}


def _write_journal_events(events_dir: Path, target_date: date, events: list[dict]):
    filepath = events_dir / f"{target_date.isoformat()}.jsonl"
    with open(filepath, "w") as f:
        for evt in events:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")


class TestCollectDayData:
    def test_empty_day(self, env_setup):
        from app.governor_daily_report import _collect_day_data
        data = _collect_day_data(date(2026, 3, 4))
        assert data["events_count"] == 0
        assert data["citizen_events"] == {}

    def test_day_with_events(self, env_setup, monkeypatch):
        events = [
            {"author": "dany-yourart", "author_type": "citizen", "type": "push",
             "repo": "emailfactory", "summary": "test", "timestamp": "2026-03-04T10:00:00Z"},
            {"author": "dany-yourart", "author_type": "citizen", "type": "push",
             "repo": "emailfactory", "summary": "test2", "timestamp": "2026-03-04T11:00:00Z"},
            {"author": "JBocage", "author_type": "tech", "type": "push",
             "repo": "fetching", "summary": "fix", "timestamp": "2026-03-04T12:00:00Z"},
        ]
        _write_journal_events(env_setup["events_dir"], date(2026, 3, 4), events)

        # Patch report_generator INSTANCE_DIR to use our tmp_path
        import app.report_generator as rg
        monkeypatch.setattr(rg, "INSTANCE_DIR", env_setup["instance"])

        from app.governor_daily_report import _collect_day_data
        data = _collect_day_data(date(2026, 3, 4))
        assert data["events_count"] == 3
        assert data["citizen_events"]["dany-yourart"] == 2
        assert len(data["top_citizens"]) == 1


class TestFallbackNarrative:
    def test_basic_output(self):
        from app.governor_daily_report import _fallback_narrative
        data = {
            "events_count": 5,
            "citizen_events": {"alice": 3, "bob": 2},
            "detections_count": 1,
            "credential_alerts": 0,
            "budget_total": 12.50,
            "top_citizens": [
                {"login": "alice", "events": 3},
                {"login": "bob", "events": 2},
            ],
            "fp_rate": 0.0,
        }
        text = _fallback_narrative(data, date(2026, 3, 4))
        assert "2026-03-04" in text
        assert "5" in text
        assert "12.50" in text
        assert "alice" in text

    def test_empty_data(self):
        from app.governor_daily_report import _fallback_narrative
        data = {
            "events_count": 0,
            "citizen_events": {},
            "detections_count": 0,
            "credential_alerts": 0,
            "budget_total": 0.0,
            "top_citizens": [],
            "fp_rate": 0.0,
        }
        text = _fallback_narrative(data, date(2026, 3, 4))
        assert "0" in text


class TestStoreReport:
    def test_creates_yaml_file(self, env_setup):
        from app.governor_daily_report import _store_report
        data = {"events_count": 3, "citizen_events": {}, "detections_count": 0,
                "fp_rate": 0.0, "credential_alerts": 0, "budget_total": 0.0,
                "top_citizens": []}
        _store_report(date(2026, 3, 4), data, "Test narrative")

        report_file = env_setup["instance"] / "reports" / "2026-03-04.yaml"
        assert report_file.exists()
        import yaml
        with open(report_file) as f:
            report = yaml.safe_load(f)
        assert report["date"] == "2026-03-04"
        assert report["narrative"] == "Test narrative"
        assert report["data"]["events_count"] == 3


class TestBuildReportCard:
    def test_card_structure(self):
        from app.governor_daily_report import build_report_card
        cards = build_report_card("Test narrative", date(2026, 3, 4))
        assert len(cards) == 1
        card = cards[0]["card"]
        assert "2026-03-04" in card["header"]["title"]
        widgets = card["sections"][0]["widgets"]
        assert "Test narrative" in widgets[0]["textParagraph"]["text"]


class TestGenerateNarrative:
    @patch("app.advisor.helpers.summarize_with_llm", return_value="LLM narrative")
    def test_llm_success(self, mock_llm):
        from app.governor_daily_report import _generate_narrative
        data = {"events_count": 0, "citizen_events": {}, "top_citizens": [],
                "detections_count": 0, "fp_rate": 0.0, "credential_alerts": 0,
                "budget_total": 0.0}
        config = {"advisor": {"summary_model": "claude-haiku"}}
        result = _generate_narrative(data, date(2026, 3, 4), config)
        assert result == "LLM narrative"

    @patch("app.advisor.helpers.summarize_with_llm", side_effect=Exception("LLM down"))
    def test_fallback_on_llm_error(self, mock_llm):
        from app.governor_daily_report import _generate_narrative
        data = {"events_count": 5, "citizen_events": {"a": 3}, "top_citizens": [],
                "detections_count": 0, "fp_rate": 0.0, "credential_alerts": 0,
                "budget_total": 10.0}
        config = {"advisor": {}}
        result = _generate_narrative(data, date(2026, 3, 4), config)
        assert "2026-03-04" in result
        assert "10.00" in result
