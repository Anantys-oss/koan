"""
Mission decomposition — lightweight LLM call to split complex missions.

Public API:
    decompose_mission(mission_text, project_path) -> list[str] | None
"""

import json
from typing import List, Optional

from app.run_log import log_safe as _log_decompose

_MAX_SUBTASKS = 6


class DecomposeError(RuntimeError):
    """CLI call failed — distinct from a legitimate 'atomic' classification (None)."""


def decompose_mission(mission_text: str, project_path: str) -> Optional[List[str]]:
    """Call a lightweight model to decompose a mission into sub-tasks.

    Returns None if the mission is atomic (legitimate classification).
    Returns a non-empty list of sub-task strings if the mission is composite.
    Raises DecomposeError on CLI failures so callers can distinguish from atomic.
    """
    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_prompt

    prompt = load_prompt("decompose-mission", mission_text=mission_text)
    models = get_model_config()

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
            cwd=project_path,
        )
        if result.returncode != 0:
            _log_decompose("error",
                f"CLI call failed: {result.stderr[:200]}")
            raise DecomposeError(
                f"CLI exited {result.returncode}: {result.stderr[:200]}")
        return _parse_response(result.stdout.strip())
    except DecomposeError:
        raise
    except Exception as e:
        _log_decompose("error", f"CLI call error: {e}")
        raise DecomposeError(str(e)) from e


def _parse_response(output: str) -> Optional[List[str]]:
    """Parse JSON output from the decompose prompt.

    Returns None on any parse error or when the mission is atomic.
    Returns a (possibly truncated) list of sub-task strings when composite.
    """
    if not output:
        return None

    # Strip markdown code fences if the model wrapped the JSON
    text = output.strip()
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

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        _log_decompose("warning", f"Malformed JSON: {output[:200]}")
        return None

    if not isinstance(data, dict):
        return None

    mission_type = data.get("type", "")
    if mission_type != "composite":
        return None

    subtasks = data.get("subtasks", [])
    if not isinstance(subtasks, list):
        return None

    # Filter empty strings, cap at max
    valid = [str(t).strip() for t in subtasks if str(t).strip()]
    if not valid:
        return None

    if len(valid) > _MAX_SUBTASKS:
        _log_decompose("warning",
            f"Truncating {len(valid)} sub-tasks to {_MAX_SUBTASKS}")
        valid = valid[:_MAX_SUBTASKS]

    return valid
