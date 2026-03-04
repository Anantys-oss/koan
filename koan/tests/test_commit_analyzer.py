"""Tests for commit_analyzer — LLM analysis, card building, routing."""

import json
import os
import sys
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
        "smart_notifications:\n  enabled: true\n  analysis_model: claude-haiku\n"
        "  max_tokens: 600\n  citizen_full_analysis: true\n  tech_light_notify: true\n"
        "advisor:\n  enabled: false\n"
        "budget_controller:\n  litellm_url: http://localhost:4000\n"
    )
    (instance / "watcher" / "events").mkdir(parents=True)

    monkeypatch.setenv("KOAN_ROOT", str(koan_root))
    monkeypatch.setenv("LITELLM_MASTER_KEY", "fake-key")

    import importlib
    import app.utils
    monkeypatch.setattr(app.utils, "KOAN_ROOT", koan_root)
    import app.commit_analyzer as mod
    monkeypatch.setattr(mod, "KOAN_ROOT", koan_root)
    monkeypatch.setattr(mod, "INSTANCE_DIR", instance)

    return {"koan_root": koan_root, "instance": instance}


def _make_event(author="dany-yourart", author_type="citizen", repo="emailfactory",
                platform="github", event_type="push", summary="feat: test"):
    evt = MagicMock()
    evt.author = author
    evt.author_type = author_type
    evt.repo = repo
    evt.platform = platform
    evt.type = event_type
    evt.summary = summary
    evt.timestamp = "2026-03-04T10:00:00Z"
    return evt


class TestParseAnalysisJson:
    def test_valid_json(self):
        from app.commit_analyzer import _parse_analysis_json
        data = json.dumps({
            "resume_fonctionnel": "Ajout newsletter",
            "analyse_technique": "Pattern MVC correct",
            "contexte_orga": "Seul sur ce repo",
            "recommandations": ["Tester", "Review"],
            "niveau_attention": "info",
            "tags": ["feature"],
        })
        result = _parse_analysis_json(data)
        assert result is not None
        assert result["resume_fonctionnel"] == "Ajout newsletter"
        assert result["niveau_attention"] == "info"

    def test_json_in_code_block(self):
        from app.commit_analyzer import _parse_analysis_json
        data = '```json\n{"resume_fonctionnel": "test", "analyse_technique": "ok", "niveau_attention": "info"}\n```'
        result = _parse_analysis_json(data)
        assert result is not None
        assert result["resume_fonctionnel"] == "test"

    def test_missing_required_fields(self):
        from app.commit_analyzer import _parse_analysis_json
        data = json.dumps({"resume_fonctionnel": "test"})
        result = _parse_analysis_json(data)
        assert result is None

    def test_invalid_json(self):
        from app.commit_analyzer import _parse_analysis_json
        result = _parse_analysis_json("not json at all")
        assert result is None


class TestBuildSmartCard:
    def test_card_structure(self):
        from app.commit_analyzer import _build_smart_card
        analysis = {
            "resume_fonctionnel": "Ajout newsletter",
            "analyse_technique": "Pattern MVC",
            "contexte_orga": "Seul sur ce repo",
            "recommandations": ["Tester", "Review"],
            "niveau_attention": "attention",
            "tags": ["feature", "data"],
        }
        event = _make_event()
        cards = _build_smart_card(analysis, event)

        assert len(cards) == 1
        card = cards[0]["card"]
        assert "Smart Notification" in card["header"]["title"]
        assert card["header"]["subtitle"] == "dany-yourart • attention"
        assert len(card["sections"][0]["widgets"]) == 3  # decorated + analysis + recos

    def test_card_without_recos(self):
        from app.commit_analyzer import _build_smart_card
        analysis = {
            "resume_fonctionnel": "Fix",
            "analyse_technique": "OK",
            "contexte_orga": "Solo",
            "recommandations": [],
            "niveau_attention": "info",
            "tags": [],
        }
        event = _make_event()
        cards = _build_smart_card(analysis, event)
        assert len(cards[0]["card"]["sections"][0]["widgets"]) == 2  # no recos widget


class TestRouting:
    @patch("app.commit_analyzer._analyze_citizen_commit")
    @patch("app.commit_analyzer._send_light_notification")
    def test_citizen_push_triggers_full_analysis(self, mock_light, mock_full):
        from app.commit_analyzer import analyze_and_notify
        event = _make_event(author_type="citizen")
        config = {"citizen_full_analysis": True, "tech_light_notify": True}
        analyze_and_notify(event, config)
        mock_full.assert_called_once()
        mock_light.assert_not_called()

    @patch("app.commit_analyzer._analyze_citizen_commit")
    @patch("app.commit_analyzer._send_light_notification")
    def test_tech_push_triggers_light(self, mock_light, mock_full):
        from app.commit_analyzer import analyze_and_notify
        event = _make_event(author_type="tech")
        config = {"citizen_full_analysis": True, "tech_light_notify": True}
        analyze_and_notify(event, config)
        mock_light.assert_called_once()
        mock_full.assert_not_called()

    @patch("app.commit_analyzer._analyze_citizen_commit")
    @patch("app.commit_analyzer._send_light_notification")
    def test_governor_notify_disabled(self, mock_light, mock_full):
        from app.commit_analyzer import analyze_and_notify
        event = _make_event(author_type="governor")
        config = {"governor_light_notify": False}
        analyze_and_notify(event, config)
        mock_light.assert_not_called()
        mock_full.assert_not_called()

    @patch("app.commit_analyzer._send_light_notification")
    def test_pr_event_triggers_light(self, mock_light):
        from app.commit_analyzer import analyze_and_notify
        event = _make_event(event_type="pull_request", author_type="unknown")
        config = {"tech_light_notify": True}
        analyze_and_notify(event, config)
        mock_light.assert_called_once()


class TestGetOrgContext:
    def test_empty_journal(self):
        from app.commit_analyzer import _get_org_context
        result = _get_org_context("dany-yourart", "emailfactory")
        assert result["repo"] == "emailfactory"
        assert result["recent_authors"] == []


class TestSendCards:
    @patch("app.watcher.notifier.send_notification", return_value=True)
    def test_sends_via_watcher_notifier(self, mock_send):
        from app.commit_analyzer import _send_cards
        cards = [{"cardId": "test", "card": {"header": {"title": "Test"}}}]
        event = _make_event()
        result = _send_cards(cards, event, {})
        mock_send.assert_called_once()
        assert result is True
