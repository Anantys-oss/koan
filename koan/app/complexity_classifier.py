"""Mission complexity pre-classifier.

Assigns a complexity tier to a mission before dispatch, enabling model
selection, timeout, and max-turns routing in build_mission_command().

Tiers:
    TRIVIAL  — tiny mechanical change, no design needed
    SIMPLE   — small self-contained change, 1-3 files
    MEDIUM   — moderate multi-file work (default on failure)
    COMPLEX  — architectural / large-scope work

The tier is determined by a single lightweight-model call (Haiku by
default).  Any parse or network failure degrades gracefully to MEDIUM.

The prompt lives in koan/system-prompts/complexity_classifier.md.
"""

import json
import sys
from enum import Enum
from typing import Optional


class MissionTier(str, Enum):
    """Complexity tier for a mission."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


# Map of lowercase string → enum value for robust parsing
_TIER_MAP = {t.value: t for t in MissionTier}

# Fallback when classification fails — conservative middle ground
_DEFAULT_TIER = MissionTier.MEDIUM


def classify_mission_complexity(
    mission_text: str,
    project_name: str = "",
) -> MissionTier:
    """Classify a mission into a complexity tier using the lightweight model.

    Calls the lightweight model (resolved via get_model_config) with a short
    structured prompt.  Parses the JSON response and returns the tier.

    Args:
        mission_text: The raw mission description text.
        project_name: Optional project name for per-project model overrides.

    Returns:
        MissionTier enum value.  Defaults to MEDIUM on any error.
    """
    if not mission_text or not mission_text.strip():
        return _DEFAULT_TIER

    try:
        from app.cli_provider import build_full_command
        from app.config import get_model_config
        from app.prompts import load_prompt
    except ImportError as e:
        print(f"[complexity_classifier] Import error: {e}", file=sys.stderr)
        return _DEFAULT_TIER

    try:
        prompt = load_prompt("complexity_classifier", mission_text=mission_text)
    except Exception as e:
        print(f"[complexity_classifier] Prompt load error: {e}", file=sys.stderr)
        return _DEFAULT_TIER

    try:
        models = get_model_config(project_name)
        model = models.get("lightweight", "haiku")
        fallback = models.get("fallback", "sonnet")

        cmd = build_full_command(
            prompt=prompt,
            allowed_tools=[],
            model=model,
            fallback=fallback,
            max_turns=1,
        )
    except Exception as e:
        print(f"[complexity_classifier] Command build error: {e}", file=sys.stderr)
        return _DEFAULT_TIER

    try:
        from app.cli_exec import run_cli_with_retry

        result = run_cli_with_retry(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(
                f"[complexity_classifier] CLI failed (exit={result.returncode}): "
                f"{result.stderr[:200]}",
                file=sys.stderr,
            )
            return _DEFAULT_TIER

        return _parse_tier_response(result.stdout)
    except Exception as e:
        print(f"[complexity_classifier] CLI error: {e}", file=sys.stderr)
        return _DEFAULT_TIER


def _parse_tier_response(response: str) -> MissionTier:
    """Parse the tier from a classifier response string.

    Expected format (from the prompt):
        {"tier": "trivial", "rationale": "..."}

    Falls back to MEDIUM on any parse failure.

    Args:
        response: Raw stdout from the classifier CLI call.

    Returns:
        MissionTier enum value.
    """
    if not response:
        return _DEFAULT_TIER

    # Extract JSON from the response — it may be wrapped in markdown fences
    text = response.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        in_fence = False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not line.startswith("```"):
                inner.append(line)
        text = "\n".join(inner).strip()

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        print(
            f"[complexity_classifier] No JSON found in response: {text[:100]}",
            file=sys.stderr,
        )
        return _DEFAULT_TIER

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError as e:
        print(
            f"[complexity_classifier] JSON parse error: {e} — response: {text[:100]}",
            file=sys.stderr,
        )
        return _DEFAULT_TIER

    tier_str = str(data.get("tier", "")).lower().strip()
    tier = _TIER_MAP.get(tier_str)
    if tier is None:
        print(
            f"[complexity_classifier] Unknown tier '{tier_str}' — defaulting to medium",
            file=sys.stderr,
        )
        return _DEFAULT_TIER

    return tier
