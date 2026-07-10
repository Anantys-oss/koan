"""Unit tests for the shared GitHub-alert builder."""
import pytest

from app.github_alerts import build_alert


@pytest.mark.parametrize("kind", ["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"])
def test_native_kinds_emit_github_block(kind):
    result = build_alert(kind, "hello world")
    assert result == f"> [!{kind}]\n> hello world"

def test_kind_is_case_insensitive():
    assert build_alert("warning", "x") == "> [!WARNING]\n> x"

def test_multiline_text_prefixes_every_line():
    result = build_alert("NOTE", "line one\nline two")
    assert result == "> [!NOTE]\n> line one\n> line two"

def test_blank_paragraph_separator_becomes_bare_quote():
    result = build_alert("TIP", "para one\n\npara two")
    assert result == "> [!TIP]\n> para one\n>\n> para two"

def test_no_leading_or_trailing_blank_lines_added():
    # Caller owns surrounding whitespace; helper never adds it.
    result = build_alert("WARNING", "body")
    assert not result.startswith("\n")
    assert not result.endswith("\n")

def test_non_github_provider_degrades_to_plain_prefix():
    assert build_alert("WARNING", "watch out", provider="jira") == "WARNING: watch out"

def test_non_github_provider_preserves_multiline_body():
    assert build_alert("NOTE", "a\nb", provider="jira") == "NOTE: a\nb"

def test_invalid_kind_raises_value_error():
    with pytest.raises(ValueError, match="Unknown alert kind"):
        build_alert("QUESTION", "not a native type")
