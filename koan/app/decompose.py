"""
Mission decomposition — lightweight LLM call to split complex missions.

Public API:
    decompose_mission(mission_text, project_path, project_name) -> list[str] | None

Returns None when the mission is atomic (a legitimate classification), a
non-empty list of sub-task strings when composite, and raises DecomposeError
on CLI/parse failures so callers can distinguish a failure from a real atomic
verdict and run the mission whole *knowingly*.
"""

import json
from typing import List, Optional

from app.run_log import log_safe as _log_decompose

_MAX_SUBTASKS = 6


class DecomposeError(RuntimeError):
    """CLI/parse call failed — distinct from a legitimate 'atomic' verdict (None)."""


def decompose_mission(
    mission_text: str,
    project_path: str,
    project_name: str = "",
) -> Optional[List[str]]:
    """Call a lightweight model to decompose a mission into sub-tasks.

    Returns None if the mission is atomic (legitimate classification).
    Returns a non-empty list of sub-task strings if the mission is composite.
    Raises DecomposeError on CLI failures so callers can distinguish from atomic.
    """
    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_prompt

    prompt = load_prompt("decompose-mission", mission_text=mission_text)
    models = get_model_config(project_name)

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    from app.cli_exec import run_cli_with_retry

    try:
        result = run_cli_with_retry(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_path or None,
        )
    except Exception as e:
        _log_decompose("error", f"Decompose CLI call error: {e}")
        raise DecomposeError(str(e)) from e

    if result.returncode != 0:
        _log_decompose("error", f"Decompose CLI failed: {result.stderr[:200]}")
        raise DecomposeError(
            f"CLI exited {result.returncode}: {result.stderr[:200]}")

    return _parse_response(result.stdout)


def _parse_response(output: str) -> Optional[List[str]]:
    """Parse JSON output from the decompose prompt.

    Returns None when the mission is classified as atomic.
    Returns a (possibly truncated) list of sub-task strings when composite.
    Raises DecomposeError on malformed output so callers can distinguish
    parse failures from legitimate atomic classifications.
    """
    if not output or not output.strip():
        raise DecomposeError("Empty output from classifier")

    # Unwrap provider stream/json envelopes (Claude {"result": ...},
    # stream-json NDJSON, etc.) before looking for the verdict JSON — the same
    # way complexity_classifier does. Lazy import avoids a module cycle.
    try:
        from app.mission_runner import parse_claude_output

        text = parse_claude_output(output)
    except Exception as e:
        _log_decompose("warning", f"Decompose envelope unwrap failed: {e}")
        text = output.strip()

    text = (text or "").strip()
    if not text:
        raise DecomposeError(f"Empty payload after unwrap: {output[:200]}")

    # Strip markdown code fences if the model wrapped the JSON
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        in_fence = False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                inner.append(line)
        text = "\n".join(inner).strip()

    # Narrow to the outermost JSON object if there is surrounding prose
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise DecomposeError(f"Malformed JSON: {output[:200]}") from e

    if not isinstance(data, dict):
        raise DecomposeError(f"Expected dict, got {type(data).__name__}")

    mission_type = str(data.get("type", "")).lower().strip()
    if mission_type != "composite":
        return None

    subtasks = data.get("subtasks", [])
    if not isinstance(subtasks, list):
        # Composite verdict with a malformed subtasks field is a classifier
        # failure, not an atomic mission — raise so the caller can tell them
        # apart and runs the mission whole *knowingly*.
        raise DecomposeError(
            f"Composite verdict but subtasks is {type(subtasks).__name__}, "
            f"not a list: {output[:200]}")

    # Filter empty strings, cap at max
    valid = [str(t).strip() for t in subtasks if str(t).strip()]
    if not valid:
        # Composite verdict that yields zero usable sub-tasks is degenerate
        # classifier output — surface it rather than silently downgrading to
        # atomic, which would hide a classifier-quality problem (review 🟡).
        raise DecomposeError(
            f"Composite verdict but no usable sub-tasks: {output[:200]}")

    if len(valid) > _MAX_SUBTASKS:
        _log_decompose("warning",
            f"Truncating {len(valid)} sub-tasks to {_MAX_SUBTASKS}")
        valid = valid[:_MAX_SUBTASKS]

    return valid
