"""Tests for bin/oc-claude — argument translation behavior.

Stubs `ocgo` on PATH, runs the wrapper, and asserts the translated argv.
Tests observable behavior (input argv → output argv), not source code.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "oc-claude"


def _make_fake_ocgo(tmp_path: Path):
    """Create a fake `ocgo` that records its argv to a file."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    record = tmp_path / "argv.txt"
    fake = bindir / "ocgo"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
    )
    fake.chmod(0o755)
    return bindir, record


def _run(args, env_extra, tmp_path):
    bindir, record = _make_fake_ocgo(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env.pop("OC_CLAUDE_MODEL", None)
    env.update(env_extra)
    proc = subprocess.run(
        [str(WRAPPER), *args], env=env, capture_output=True, text=True
    )
    recorded = record.read_text().splitlines() if record.exists() else []
    return proc, recorded


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_explicit_model_is_extracted_and_forwarded(tmp_path):
    proc, argv = _run(
        ["-p", "hello world", "--model", "kimi-k2.7-code", "--output-format", "json"],
        {},
        tmp_path,
    )
    assert proc.returncode == 0
    # ocgo launch claude --model kimi-k2.7-code -- -p "hello world" --output-format json
    assert argv[:4] == ["launch", "claude", "--model", "kimi-k2.7-code"]
    assert argv[4] == "--"
    assert argv[5:] == ["-p", "hello world", "--output-format", "json"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_model_equals_form_is_supported(tmp_path):
    _, argv = _run(["--model=deepseek-v4-pro", "-p", "hi"], {}, tmp_path)
    assert argv[:4] == ["launch", "claude", "--model", "deepseek-v4-pro"]
    assert "--model=deepseek-v4-pro" not in argv[5:]
    assert argv[5:] == ["-p", "hi"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_falls_back_to_env_var(tmp_path):
    _, argv = _run(["-p", "hi"], {"OC_CLAUDE_MODEL": "qwen-coder"}, tmp_path)
    assert argv[:4] == ["launch", "claude", "--model", "qwen-coder"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_falls_back_to_hard_default(tmp_path):
    _, argv = _run(["-p", "hi"], {}, tmp_path)
    assert argv[2:4] == ["--model", "kimi-k2.7-code"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_explicit_model_beats_env_fallback(tmp_path):
    _, argv = _run(
        ["-p", "hi", "--model", "deepseek-v4-pro"],
        {"OC_CLAUDE_MODEL": "qwen-coder"},
        tmp_path,
    )
    assert argv[2:4] == ["--model", "deepseek-v4-pro"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_remaining_args_forwarded_verbatim(tmp_path):
    _, argv = _run(
        ["--model", "kimi-k2.7-code", "--output-format", "stream-json", "--verbose"],
        {},
        tmp_path,
    )
    # model stripped, rest forwarded after --
    assert argv[4] == "--"
    rest = argv[5:]
    assert "--output-format" in rest
    assert "stream-json" in rest
    assert "--verbose" in rest
    assert "--model" not in rest


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_fallback_model_is_stripped(tmp_path):
    # OpenCode has no fallback concept; Koan's default --fallback-model sonnet
    # must not reach ocgo (both flag forms).
    _, argv = _run(
        ["-p", "hi", "--model", "kimi-k2.7-code", "--fallback-model", "sonnet"],
        {},
        tmp_path,
    )
    assert argv[2:4] == ["--model", "kimi-k2.7-code"]
    rest = argv[5:]
    assert "--fallback-model" not in rest
    assert "sonnet" not in rest
    assert rest == ["-p", "hi"]

    _, argv = _run(["-p", "hi", "--fallback-model=sonnet"], {}, tmp_path)
    assert "--fallback-model=sonnet" not in argv[5:]
    assert argv[5:] == ["-p", "hi"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_empty_args_after_model_stripping(tmp_path):
    # Every arg consumed as a flag → args array empty. Must not crash under
    # set -u on bash 3.2.
    proc, argv = _run(["--model", "kimi-k2.7-code"], {}, tmp_path)
    assert proc.returncode == 0
    assert argv == ["launch", "claude", "--model", "kimi-k2.7-code", "--"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_trailing_model_flag_does_not_abort(tmp_path):
    # --model as the last token (no value) must not trip set -e on shift.
    proc, argv = _run(["-p", "hi", "--model"], {}, tmp_path)
    assert proc.returncode == 0
    assert argv[2:4] == ["--model", "kimi-k2.7-code"]
    assert argv[5:] == ["-p", "hi"]


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required")
def test_missing_ocgo_fails_clearly(tmp_path):
    # Use an empty custom bin dir — but keep /bin and /usr/bin so bash is
    # reachable. Only ocgo is absent.
    empty_bin = tmp_path / "empty"
    empty_bin.mkdir()
    bash_dir = str(Path(shutil.which("bash")).parent)
    env = dict(os.environ)
    env["PATH"] = f"{empty_bin}:{bash_dir}"
    proc = subprocess.run(
        [str(WRAPPER), "-p", "hi"], env=env, capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "ocgo" in (proc.stderr + proc.stdout).lower()
