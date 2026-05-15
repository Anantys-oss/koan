"""Tests for memory_recall — task-aware learnings filtering (issue #1306)."""

import pytest

from app.memory_recall import (
    has_recall_full_tag,
    jaccard_score,
    score_and_select,
    tokenize,
)


# --- tokenize ---


def test_tokenize_lowercases_and_drops_stopwords():
    assert tokenize("The quick brown FOX") == {"quick", "brown", "fox"}


def test_tokenize_drops_short_tokens():
    # "is" and "to" are stopwords; "a" / "go" are below the 3-char threshold.
    assert tokenize("a is to go") == set()
    assert tokenize("go run now") == {"run", "now"}


def test_tokenize_empty_string():
    assert tokenize("") == set()


def test_tokenize_deduplicates():
    assert tokenize("test test test failure") == {"test", "failure"}


# --- jaccard_score ---


def test_jaccard_identical_sets():
    s = {"alpha", "beta"}
    assert jaccard_score(s, s) == 1.0


def test_jaccard_disjoint():
    assert jaccard_score({"a"}, {"b"}) == 0.0


def test_jaccard_partial_overlap():
    # |intersection| = 1, |union| = 3 → 1/3
    score = jaccard_score({"a", "b"}, {"b", "c"})
    assert score == pytest.approx(1 / 3)


def test_jaccard_empty_both_sides_returns_zero():
    assert jaccard_score(set(), set()) == 0.0


def test_jaccard_one_empty_side_returns_zero():
    assert jaccard_score({"a"}, set()) == 0.0


# --- has_recall_full_tag ---


def test_recall_full_tag_detected():
    assert has_recall_full_tag("fix the database [recall:full]")
    assert has_recall_full_tag("[RECALL:FULL] do something")


def test_recall_full_tag_absent():
    assert not has_recall_full_tag("plain mission text")
    assert not has_recall_full_tag("")


# --- score_and_select ---


def test_score_and_select_returns_relevant_lines_first():
    content = (
        "- Use postgres for migrations\n"
        "- CSS grid layouts work better than flexbox here\n"
        "- Always run pre-commit before push\n"
        "- Database connection pooling tunes at 25\n"
    )
    selected, total, dropped = score_and_select(
        content, "fix database migration error", max_k=2, recent_hedge=0,
    )
    assert total == 4
    assert len(selected) == 2
    assert dropped == 2
    # Both selected lines should mention database-related terms.
    joined = " ".join(selected).lower()
    assert "database" in joined or "postgres" in joined


def test_score_and_select_recent_hedge_always_kept():
    content = (
        "- ancient learning about foo\n"
        "- old learning about bar\n"
        "- medium-age learning about baz\n"
        "- recent learning about qux\n"
    )
    selected, _, _ = score_and_select(
        content, "foo", max_k=1, recent_hedge=2,
    )
    # max_k=1 picks the "foo" line; hedge=2 forces the last two lines in.
    joined = "\n".join(selected)
    assert "ancient learning about foo" in joined
    assert "medium-age learning about baz" in joined
    assert "recent learning about qux" in joined


def test_score_and_select_preserves_file_order():
    content = "- zebra\n- alpha\n- beta\n- gamma\n"
    selected, _, _ = score_and_select(
        content, "zebra alpha", max_k=4, recent_hedge=0,
    )
    # All four lines selected; output should be in original file order.
    assert selected == ["- zebra", "- alpha", "- beta", "- gamma"]


def test_score_and_select_drops_headers_and_blank_lines():
    content = (
        "# Project Learnings\n"
        "\n"
        "## Recent\n"
        "- real learning\n"
    )
    selected, total, _ = score_and_select(
        content, "real", max_k=10, recent_hedge=0,
    )
    assert total == 1
    assert selected == ["- real learning"]


def test_score_and_select_empty_file_returns_empty():
    selected, total, dropped = score_and_select("", "anything", max_k=10)
    assert selected == []
    assert total == 0
    assert dropped == 0


def test_score_and_select_deterministic():
    content = "- a learning about x\n- a learning about y\n- a learning about z\n"
    a = score_and_select(content, "x y z", max_k=2)
    b = score_and_select(content, "x y z", max_k=2)
    assert a == b


def test_score_and_select_no_mission_text_falls_back_to_recency():
    # All lines score 0.0 → ties broken by recency (later wins).
    content = "- l1\n- l2\n- l3\n- l4\n- l5\n"
    selected, _, _ = score_and_select(
        content, "", max_k=2, recent_hedge=0,
    )
    # The two most-recent (l5, l4) should be selected, in file order.
    assert selected == ["- l4", "- l5"]


def test_score_and_select_max_k_zero_keeps_only_hedge():
    content = "- l1\n- l2\n- l3\n- l4\n"
    selected, total, _ = score_and_select(
        content, "anything", max_k=0, recent_hedge=2,
    )
    assert total == 4
    assert selected == ["- l3", "- l4"]


def test_score_and_select_caps_at_total_lines():
    content = "- only one\n"
    selected, total, dropped = score_and_select(
        content, "one", max_k=100, recent_hedge=100,
    )
    assert total == 1
    assert len(selected) == 1
    assert dropped == 0
