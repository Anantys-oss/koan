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


def test_tool_result_is_dim_return_arrow():
    assert r("tool_result toolu_015i88") == "↩"
    assert is_tick("tool_result toolu_015i88") is True


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


def test_run_collapses_consecutive_ticks():
    src = io.StringIO(
        "[cli] assistant — thinking\n"
        "[cli] system: thinking_tokens\n"
        "[cli] assistant — thinking\n"
    )
    out = io.StringIO()
    run(src, out)
    assert out.getvalue() == "•\n"


def test_run_never_crashes_on_garbled_line():
    src = io.StringIO("[cli] \n[cli]\n\x00\x1b[garbage\n[cli] assistant — text: ok\n")
    out = io.StringIO()
    run(src, out)  # must not raise
    assert "🧠 ok" in out.getvalue()
