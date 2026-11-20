"""Opt-in Codex adversarial-review backend for /review.

Runs the codex-plugin-cc companion (``adversarial-review --json``) inside the
pinned PR-head worktree and maps its structured ``result`` into Kōan's
REVIEW_SCHEMA, so presentation and verdict logic stay provider-independent.
See specs/skills/review.md ("Opt-in Codex adversarial backend").
"""

import json
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from app.review_schema import validate_review

# Codex adversarial severities → Kōan severities (unknown → suggestion).
_SEVERITY_MAP = {
    "critical": "critical",
    "high": "critical",
    "medium": "warning",
    "low": "suggestion",
}


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _map_finding(item: dict) -> dict:
    body = str(item.get("body") or "").strip() or "No details provided."
    rec = str(item.get("recommendation") or "").strip()
    if rec:
        body = f"{body}\n\n**Recommendation:** {rec}"
    line_start = max(0, _int(item.get("line_start"), 0))
    line_end = max(line_start, _int(item.get("line_end"), line_start))
    return {
        "file": str(item.get("file") or "").strip() or "unknown",
        "line_start": line_start,
        "line_end": line_end,
        "severity": _SEVERITY_MAP.get(
            str(item.get("severity") or "").lower(), "suggestion"),
        "title": str(item.get("title") or "").strip() or "Finding",
        "comment": body,
        "code_snippet": "",
    }


def map_adversarial_to_review(adversarial: dict) -> dict:
    """Convert a companion adversarial ``result`` into a REVIEW_SCHEMA dict.

    ``lgtm`` is derived from the mapped severities — the provider ``verdict``
    is ignored so the formal GitHub verdict follows Kōan's severity rule.
    """
    findings = adversarial.get("findings")
    file_comments = [
        _map_finding(f) for f in (findings if isinstance(findings, list) else [])
        if isinstance(f, dict)
    ]
    summary = str(adversarial.get("summary") or "").strip() or "Review completed."
    steps = adversarial.get("next_steps")
    steps = [str(s).strip() for s in (steps if isinstance(steps, list) else [])]
    steps = [s for s in steps if s]
    if steps:
        summary += "\n\n**Next steps:**\n" + "\n".join(f"- {s}" for s in steps)
    has_blocker = any(
        c["severity"] in {"critical", "warning"} for c in file_comments)
    return {
        "file_comments": file_comments,
        "review_summary": {
            "lgtm": not has_blocker, "summary": summary, "checklist": [],
        },
    }


def _invoke_companion(
    script: Path, project_path: str, base_ref: str, timeout_seconds: int,
    model: str,
) -> Tuple[Optional[subprocess.CompletedProcess], str]:
    """Run the companion; return ``(proc, "")`` or ``(None, error)``."""
    cmd = ["node", str(script), "adversarial-review", "--json", "--base", base_ref]
    if model:
        cmd += ["--model", model]
    try:
        return subprocess.run(
            cmd, cwd=project_path, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout_seconds,
            check=False,
        ), ""
    except FileNotFoundError:
        return None, "node binary not found"
    except subprocess.TimeoutExpired:
        return None, f"codex adversarial-review timed out after {timeout_seconds}s"


def _parse_companion_stdout(stdout: str) -> Tuple[Optional[dict], str]:
    """Parse the whole stdout as one JSON envelope; return ``(review, error)``."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, f"companion output is not JSON: {exc}"

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        detail = payload.get("parseError") if isinstance(payload, dict) else None
        return None, f"structured result missing ({detail or 'no result'})"

    review = map_adversarial_to_review(result)
    ok, errors = validate_review(review)
    if not ok:
        return None, "mapped review failed validation: " + "; ".join(errors)
    return review, ""


def run_adversarial_review(
    *,
    project_path: str,
    base_ref: str,
    companion_script: str,
    timeout_seconds: int = 900,
    model: str = "",
) -> Tuple[Optional[dict], str, str]:
    """Run the companion and return ``(review_data, raw_stdout, error)``.

    ``review_data`` is a validated REVIEW_SCHEMA dict, or ``None`` with a
    non-empty ``error``. Never raises for expected failures.
    """
    if not companion_script:
        return None, "", "codex companion_script not configured"
    script = Path(companion_script).expanduser()
    if not script.is_file():
        return None, "", f"codex companion not found: {companion_script}"

    proc, error = _invoke_companion(
        script, project_path, base_ref, timeout_seconds, model)
    if proc is None:
        return None, "", error

    stdout = (proc.stdout or "").strip()
    if not stdout:
        stderr = (proc.stderr or "").strip()[:200]
        return None, "", (
            f"codex adversarial-review produced no output (exit={proc.returncode})"
            + (f": {stderr}" if stderr else ""))

    review, error = _parse_companion_stdout(stdout)
    return review, stdout, error
