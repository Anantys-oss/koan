"""Unit tests for the skill-dispatch stdout streaming pump.

Verifies that ``_pump_skill_stdout`` streams every line to disk while keeping
only a bounded tail in RAM, so a verbose skill session cannot buffer the full
transcript in memory (issue #2173).
"""
import io
import os
from collections import deque

os.environ.setdefault("KOAN_ROOT", "/tmp/test-koan")

from app.run import _pump_skill_stdout


def test_pump_writes_all_lines_to_both_sinks():
    lines = [f"line {i}\n" for i in range(5)]
    out_fh = io.StringIO()
    pending_fh = io.StringIO()
    tail = deque(maxlen=200)

    result_pending = _pump_skill_stdout(
        iter(lines), out_fh=out_fh, pending_fh=pending_fh, tail=tail
    )

    assert out_fh.getvalue() == "".join(f"line {i}\n" for i in range(5))
    assert pending_fh.getvalue() == "".join(f"line {i}\n" for i in range(5))
    assert list(tail) == [f"line {i}" for i in range(5)]
    assert result_pending is pending_fh


def test_pump_tail_is_bounded():
    lines = [f"{i}\n" for i in range(1000)]
    out_fh = io.StringIO()
    tail = deque(maxlen=200)

    _pump_skill_stdout(iter(lines), out_fh=out_fh, pending_fh=None, tail=tail)

    # Full transcript on disk, only the last 200 lines kept in RAM.
    assert out_fh.getvalue().count("\n") == 1000
    assert len(tail) == 200
    assert list(tail)[-1] == "999"
    assert list(tail)[0] == "800"


def test_pump_disables_pending_on_write_error_but_keeps_out_fh():
    class _Broken(io.StringIO):
        def write(self, *_):
            raise OSError("disk full")

    out_fh = io.StringIO()
    tail = deque(maxlen=200)
    result_pending = _pump_skill_stdout(
        iter(["a\n", "b\n"]), out_fh=out_fh, pending_fh=_Broken(), tail=tail
    )

    assert result_pending is None            # pending sink disabled
    assert out_fh.getvalue() == "a\nb\n"     # stdout_file write unaffected
    assert list(tail) == ["a", "b"]
