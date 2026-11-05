"""Tests for app.severity — shared severity → emoji / heading rendering."""

import pytest

from app import severity


def test_canonical_tiers_order():
    assert severity.CANONICAL_SEVERITIES == ("critical", "high", "medium", "low")


@pytest.mark.parametrize("tier,emoji", [
    ("critical", "🔴"), ("high", "🟠"), ("medium", "🟡"), ("low", "🟢"),
])
def test_canonical_emoji(tier, emoji):
    assert severity.severity_emoji(tier) == emoji


@pytest.mark.parametrize("alias,emoji", [
    ("warning", "🟡"),      # review warning → medium (must NOT be 🟠)
    ("suggestion", "🟢"),   # review suggestion → low
    ("CRITICAL", "🔴"),     # gha_audit / error-hunter uppercase
    ("LOW", "🟢"),
])
def test_alias_and_case_normalization(alias, emoji):
    assert severity.severity_emoji(alias) == emoji


def test_unknown_uses_caller_default():
    assert severity.severity_emoji("nonsense") == "⚪"
    assert severity.severity_emoji("nonsense", default="❓") == "❓"
    assert severity.severity_emoji("") == "⚪"


@pytest.mark.parametrize("tier,heading", [
    ("critical", "Blocking"), ("warning", "Important"), ("suggestion", "Suggestions"),
])
def test_review_headings_preserved(tier, heading):
    assert severity.severity_heading(tier) == heading
