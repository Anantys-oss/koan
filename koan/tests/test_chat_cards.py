"""Tests for chat_cards.py — Card v2 builders."""

import pytest

from app.chat_cards import (
    truncate_for_gchat,
    build_command_response_card,
    build_error_card,
    build_ack_card,
    build_detection_feedback_card,
    build_permission_denied_card,
)


# ── truncate_for_gchat ───────────────────────────────────────────────

class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate_for_gchat("hello") == "hello"

    def test_exact_limit_unchanged(self):
        text = "a" * 3000
        assert truncate_for_gchat(text) == text

    def test_over_limit_truncated(self):
        text = "a" * 4000
        result = truncate_for_gchat(text)
        assert len(result) <= 3000
        assert "[résultat tronqué]" in result

    def test_custom_limit(self):
        text = "a" * 200
        result = truncate_for_gchat(text, max_chars=100)
        assert len(result) <= 100
        assert "[résultat tronqué]" in result


# ── build_command_response_card ──────────────────────────────────────

class TestCommandResponseCard:
    def test_basic_structure(self):
        cards = build_command_response_card("status", "All healthy")
        assert len(cards) == 1
        card = cards[0]["card"]
        assert "header" in card
        assert card["header"]["title"] == "governor status"
        assert "sections" in card

    def test_success_status(self):
        cards = build_command_response_card("status", "OK", status="success")
        header = cards[0]["card"]["header"]
        assert "check_circle" in header["imageUrl"]

    def test_error_status(self):
        cards = build_command_response_card("vault", "Error", status="error")
        header = cards[0]["card"]["header"]
        assert "error" in header["imageUrl"]

    def test_long_result_truncated(self):
        text = "x" * 5000
        cards = build_command_response_card("status", text)
        body = cards[0]["card"]["sections"][0]["widgets"][0]["textParagraph"]["text"]
        assert len(body) <= 3000
        assert "[résultat tronqué]" in body

    def test_subtitle(self):
        cards = build_command_response_card("scan", "result", subtitle="custom sub")
        assert cards[0]["card"]["header"]["subtitle"] == "custom sub"


# ── build_error_card ─────────────────────────────────────────────────

class TestErrorCard:
    def test_basic_error(self):
        cards = build_error_card("Something went wrong")
        card = cards[0]["card"]
        assert card["header"]["title"] == "Commande inconnue"
        body = card["sections"][0]["widgets"][0]["textParagraph"]["text"]
        assert "Something went wrong" in body

    def test_with_suggestions(self):
        cards = build_error_card("Unknown", suggestions=["status", "scan"])
        widgets = cards[0]["card"]["sections"][0]["widgets"]
        all_text = " ".join(w["textParagraph"]["text"] for w in widgets)
        assert "status" in all_text
        assert "scan" in all_text

    def test_without_suggestions(self):
        cards = build_error_card("Nope", suggestions=None)
        widgets = cards[0]["card"]["sections"][0]["widgets"]
        assert len(widgets) == 2  # error + help hint


# ── build_ack_card ───────────────────────────────────────────────────

class TestAckCard:
    def test_basic_ack(self):
        cards = build_ack_card("advisor scan")
        card = cards[0]["card"]
        assert "En cours" in card["header"]["subtitle"]
        assert "advisor scan" in card["header"]["title"]


# ── build_detection_feedback_card ────────────────────────────────────

class TestDetectionFeedbackCard:
    def test_has_three_buttons(self):
        cards = build_detection_feedback_card(
            "det-001", "Duplicate found", "repo-a", "repo-b", 0.85
        )
        sections = cards[0]["card"]["sections"]
        # Second section has buttons
        buttons = sections[1]["widgets"][0]["buttonList"]["buttons"]
        assert len(buttons) == 3
        assert buttons[0]["text"] == "Pertinent"
        assert buttons[1]["text"] == "Faux positif"
        assert buttons[2]["text"] == "Ignorer"

    def test_button_parameters(self):
        cards = build_detection_feedback_card(
            "det-XYZ", "desc", "src", "tgt", 0.75
        )
        button = cards[0]["card"]["sections"][1]["widgets"][0]["buttonList"]["buttons"][0]
        params = button["onClick"]["action"]["parameters"]
        param_dict = {p["key"]: p["value"] for p in params}
        assert param_dict["detectionId"] == "det-XYZ"
        assert param_dict["feedback"] == "relevant"

    def test_similarity_in_header(self):
        cards = build_detection_feedback_card("d", "desc", "s", "t", 0.92)
        subtitle = cards[0]["card"]["header"]["subtitle"]
        assert "92%" in subtitle


# ── build_permission_denied_card ─────────────────────────────────────

class TestPermissionDeniedCard:
    def test_citizen_message(self):
        cards = build_permission_denied_card("Alex", "vault", "citizen")
        body = cards[0]["card"]["sections"][0]["widgets"][0]["textParagraph"]["text"]
        assert "réservée aux governors" in body

    def test_unknown_message(self):
        cards = build_permission_denied_card("unknown@test.com", "status", "unknown")
        body = cards[0]["card"]["sections"][0]["widgets"][0]["textParagraph"]["text"]
        assert "non reconnu" in body

    def test_header_denied(self):
        cards = build_permission_denied_card("Test", "vault", "citizen")
        assert cards[0]["card"]["header"]["title"] == "Accès refusé"
