"""Tests for the display-side make-logs formatter."""
import io

from app.log_fmt import _Palette, render_cli, run

PLAIN = _Palette(enabled=False)  # no ANSI → deterministic assertions


def r(body):
    text, _is_tick = render_cli(body, PLAIN)
    return text


def is_tick(body):
    _text, tick = render_cli(body, PLAIN)
    return tick


def test_thinking_collapses_to_tick():
    assert r("assistant — thinking") == "•"
    assert is_tick("assistant — thinking") is True


def test_system_thinking_tokens_is_tick():
    assert r("system: thinking_tokens") == "•"
    assert is_tick("system: thinking_tokens") is True


def test_text_preview_gets_brain_glyph():
    assert r("assistant — text: Now run.py — inject PYTEST_ADDOPTS") == \
        "🧠 Now run.py — inject PYTEST_ADDOPTS"


def test_tool_use_per_tool_icons():
    assert r("assistant — tool_use: Edit") == "✏️ Edit"
    assert r("assistant — tool_use: Read") == "📖 Read"
    assert r("assistant — tool_use: Bash") == "💻 Bash"
    assert r("assistant — tool_use: Frobnicate") == "🔧 Frobnicate"


def test_multi_part_assistant_keeps_real_parts_drops_ticks():
    # text preview containing ", " must NOT be split mid-preview
    out = r("assistant — tool_use: Edit, text: fix run.py, then test")
    assert out == "✏️ Edit  🧠 fix run.py, then test"


def test_successful_tool_result_is_suppressed():
    text, tick = render_cli("tool_result toolu_015i88", PLAIN)
    assert text is None          # dropped entirely, no ↩
    assert tick is True


def test_tool_use_renders_dim_command_preview():
    # PLAIN palette → dim() is a no-op, so preview appears verbatim
    assert r("assistant — tool_use: Bash: make lint && make test") == \
        "💻 Bash make lint && make test"


def test_tool_use_without_preview_unchanged():
    assert r("assistant — tool_use: Edit") == "✏️ Edit"


def test_tool_use_preview_with_colon_in_command():
    # partition on the FIRST ": " splits name from preview; a ':' inside
    # the command stays in the preview
    assert r("assistant — tool_use: Bash: git log --oneline") == \
        "💻 Bash git log --oneline"


def test_errored_tool_result_gets_error_glyph_and_never_collapses():
    assert r("tool_result toolu_015i88 (error)") == "❌ tool error"
    assert is_tick("tool_result toolu_015i88 (error)") is False


def test_result_line_gets_check():
    assert r("result: success (12s)") == "✅ result: success (12s)"


def test_session_init_and_warnings():
    assert r("session init (model=claude-opus-4-8)").endswith(
        "session init (model=claude-opus-4-8)")
    assert r("rate_limit_rejected resetsAt 5am").startswith("⚠")
    assert r("retry 2/3: overloaded").startswith("⚠")


def test_unknown_cli_shape_passes_verbatim():
    assert r("something totally new") == "something totally new"


def test_run_passes_non_cli_and_tail_headers_untouched():
    src = io.StringIO(
        "==> logs/run.log <==\n"
        "[14:03:01][mission] Starting mission\n"
        "[cli] assistant — tool_use: Edit\n"
    )
    out = io.StringIO()
    run(src, out)
    lines = out.getvalue().splitlines()
    assert lines[0] == "==> logs/run.log <=="
    assert lines[1] == "[14:03:01][mission] Starting mission"
    assert lines[2] == "✏️ Edit"


def test_run_accumulates_consecutive_thinking_dots():
    src = io.StringIO(
        "[cli] assistant — thinking\n"
        "[cli] system: thinking_tokens\n"
        "[cli] assistant — thinking\n"
    )
    out = io.StringIO()  # not a TTY → buffered dot-run path
    run(src, out)
    assert out.getvalue() == "•••\n"


def test_run_finalizes_dot_run_before_real_line():
    src = io.StringIO(
        "[cli] assistant — thinking\n"
        "[cli] assistant — thinking\n"
        "[cli] assistant — tool_use: Bash: make test\n"
    )
    out = io.StringIO()
    run(src, out)
    assert out.getvalue() == "••\n💻 Bash make test\n"


def test_run_suppressed_success_does_not_emit_or_break_dots():
    src = io.StringIO(
        "[cli] assistant — thinking\n"
        "[cli] tool_result toolu_1\n"       # success → suppressed
        "[cli] assistant — thinking\n"
        "[cli] assistant — text: done\n"
    )
    out = io.StringIO()
    run(src, out)
    # two thinking ticks accumulate across the suppressed success line
    assert out.getvalue() == "••\n🧠 done\n"


def test_run_never_crashes_on_garbled_line():
    src = io.StringIO("[cli] \n[cli]\n\x00\x1b[garbage\n[cli] assistant — text: ok\n")
    out = io.StringIO()
    run(src, out)  # must not raise
    assert "🧠 ok" in out.getvalue()


# ---------------------------------------------------------------------------
# classify_cli — structured rows for the dashboard progress timeline
# ---------------------------------------------------------------------------
from app.log_fmt import classify_cli


def test_classify_thinking_is_tick():
    rows = classify_cli("assistant — thinking")
    assert len(rows) == 1
    assert rows[0]["kind"] == "thinking"
    assert rows[0]["is_tick"] is True


def test_classify_tool_use_with_preview():
    rows = classify_cli("assistant — tool_use: Bash: make test")
    assert rows == [{
        "kind": "tool_use",
        "tool_name": "Bash",
        "icon": "💻",
        "preview": "make test",
        "label": "Bash",
        "is_tick": False,
        "raw": "assistant — tool_use: Bash: make test",
    }]


def test_classify_multipart_splits_tool_and_text():
    rows = classify_cli("assistant — tool_use: Edit, text: fix run.py, then test")
    assert [r["kind"] for r in rows] == ["tool_use", "text"]
    assert rows[0]["tool_name"] == "Edit"
    assert rows[1]["preview"] == "fix run.py, then test"


def test_classify_successful_tool_result_suppressed():
    assert classify_cli("tool_result toolu_015i88") == []


def test_classify_tool_result_error():
    rows = classify_cli("tool_result toolu_015i88 (error)")
    assert rows[0]["kind"] == "tool_error"


def test_classify_result_and_warning():
    assert classify_cli("result: success (12s)")[0]["kind"] == "result"
    assert classify_cli("retry 2/3: overloaded")[0]["kind"] == "warning"


def test_classify_unknown_is_raw():
    rows = classify_cli("something totally new")
    assert rows[0]["kind"] == "raw"
    assert rows[0]["preview"] == "something totally new"
