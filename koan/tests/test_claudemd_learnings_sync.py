"""Tests for the `/claudemd <project> learnings` sync pipeline.

Covers the deterministic managed-block mutation (pure, no I/O), the
`run_learnings_sync` early-exit control flow (Claude/git mocked out), and the
dispatch wiring that routes the `learnings` sub-argument.
"""

from unittest.mock import patch

from app import claudemd_refresh
from app.claudemd_refresh import (
    KOAN_LEARNINGS_BEGIN,
    KOAN_LEARNINGS_END,
    upsert_koan_learnings_block,
)
from app.skill_dispatch import _build_claudemd_cmd


# --- Phase 1: deterministic managed-block mutation ---------------------------


def test_insert_appends_block_when_absent():
    existing = "# My Project\n\nSome human notes.\n"
    out = upsert_koan_learnings_block(existing, "- Always run `make lint`.")
    assert existing.rstrip("\n") in out          # human content preserved
    assert KOAN_LEARNINGS_BEGIN in out and KOAN_LEARNINGS_END in out
    assert out.endswith("\n")
    assert out.count(KOAN_LEARNINGS_BEGIN) == 1


def test_replace_is_idempotent():
    base = "# P\n\nhuman\n"
    once = upsert_koan_learnings_block(base, "- Rule A")
    twice = upsert_koan_learnings_block(once, "- Rule A")
    assert once == twice                          # same input → byte-identical
    assert once.count(KOAN_LEARNINGS_BEGIN) == 1


def test_replace_swaps_only_the_block():
    base = "# P\n\nhuman-before\n"
    v1 = upsert_koan_learnings_block(base, "- Rule A")
    v2 = upsert_koan_learnings_block(v1, "- Rule B")
    assert "human-before" in v2                    # untouched
    assert "Rule A" not in v2 and "Rule B" in v2
    assert v2.count(KOAN_LEARNINGS_BEGIN) == 1      # no accumulation


def test_regex_metacharacters_in_distilled_are_literal():
    base = "# P\n"
    out = upsert_koan_learnings_block(base, r"- Use `$1` and `\g<0>` carefully")
    assert r"`$1`" in out and r"`\g<0>`" in out


def test_insert_into_empty_string_creates_block():
    out = upsert_koan_learnings_block("", "- Rule A")
    assert out.startswith(KOAN_LEARNINGS_BEGIN)
    assert out.endswith("\n")
    assert out.count(KOAN_LEARNINGS_BEGIN) == 1


# --- Phase 3: run_learnings_sync early-exit control flow ---------------------


def test_no_learnings_returns_0(tmp_path):
    with patch.object(claudemd_refresh, "_read_project_learnings", return_value=""):
        rc = claudemd_refresh.run_learnings_sync(str(tmp_path), "koan")
    assert rc == 0
    assert not (tmp_path / "CLAUDE.md").exists()   # nothing written


def test_sentinel_distillation_writes_nothing(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# P\n", encoding="utf-8")
    with patch.object(claudemd_refresh, "_read_project_learnings", return_value="- x"), \
         patch.object(claudemd_refresh, "_distill_learnings_cli",
                      return_value=claudemd_refresh._NO_DURABLE_SENTINEL):
        rc = claudemd_refresh.run_learnings_sync(str(tmp_path), "koan")
    assert rc == 0
    assert claudemd_refresh.KOAN_LEARNINGS_BEGIN not in (tmp_path / "CLAUDE.md").read_text()


def test_empty_distillation_writes_nothing(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# P\n", encoding="utf-8")
    with patch.object(claudemd_refresh, "_read_project_learnings", return_value="- x"), \
         patch.object(claudemd_refresh, "_distill_learnings_cli", return_value="   "):
        rc = claudemd_refresh.run_learnings_sync(str(tmp_path), "koan")
    assert rc == 0
    assert claudemd_refresh.KOAN_LEARNINGS_BEGIN not in (tmp_path / "CLAUDE.md").read_text()


# --- Phase 4: dispatch wiring -------------------------------------------------


def test_dispatch_default_has_no_mode_flag():
    cmd = _build_claudemd_cmd(
        ["python", "-m", "app.claudemd_refresh"], "koan", "/p/koan", "koan",
    )
    assert "--mode" not in cmd


def test_dispatch_learnings_adds_mode_flag():
    cmd = _build_claudemd_cmd(
        ["python", "-m", "app.claudemd_refresh"], "koan learnings", "/p/koan", "koan",
    )
    assert cmd[-2:] == ["--mode", "learnings"]
