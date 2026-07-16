"""GitHub @mention intent classifier using Claude.

When natural_language mode is enabled, this module classifies free-form
@mention text into a recognized bot command using a lightweight Claude call.

Only used as a fallback when the rigid command parser fails to match.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# Meta commands are never a natural-language user intent — they route, they
# aren't a skill the user asks for by name.
_META_COMMANDS = {"gh_request", "help"}
# Excluded from the keyword layer ONLY, so pure questions ("ask what …") stay
# free-form instead of being promoted to /ask on a bare keyword.
_KEYWORD_EXCLUDED = _META_COMMANDS | {"ask"}

# URL-type guard: which subject kind each command requires. Centralized here so
# the bridge and /gh_request share one implementation (previously duplicated
# inline in gh_request/handler.py::_classify_request).
_NEEDS_PR = {"rebase", "recreate", "review"}
_NEEDS_ISSUE = {"fix", "implement"}


@dataclass
class IntentMatch:
    """A resolved @mention intent promoted to a real github-enabled skill."""

    command: str
    context: str
    source: str        # "keyword" | "model"
    confidence: float  # 1.0 for keyword; 0..1 for model


def _github_keyword_lexicon(registry, exclude):
    """Map every github-enabled command name + alias -> primary command name."""
    lex = {}
    for skill in registry.list_all():
        if not getattr(skill, "github_enabled", False):
            continue
        for cmd in skill.commands:
            if cmd.name in exclude:
                continue
            for token in [cmd.name, *getattr(cmd, "aliases", [])]:
                if token:
                    lex[token.lower()] = cmd.name
    return lex


def match_skill_keyword(text, registry, window=5):
    """Promote when exactly one distinct skill keyword appears in the first
    ``window`` word tokens after the @mention. Whole-word only.

    Returns an ``IntentMatch`` (source="keyword", confidence=1.0) or None when
    there are zero or multiple distinct skill hits (ambiguous ⇒ escalate).
    """
    if not text or not text.strip():
        return None
    lex = _github_keyword_lexicon(registry, _KEYWORD_EXCLUDED)
    if not lex:
        return None
    tokens = re.findall(r"[A-Za-z_]+", text)[: max(1, window)]
    hits = []
    for tok in tokens:
        cmd = lex.get(tok.lower())
        if cmd and cmd not in hits:
            hits.append(cmd)
    if len(hits) != 1:
        return None
    command = hits[0]
    # Strip the matched command's own tokens from the context so we don't feed
    # "do a review" back to /review; skills accept free context either way.
    synonyms = {t for t, c in lex.items() if c == command}
    context = " ".join(
        w for w in text.split() if w.strip(".,!?").lower() not in synonyms
    ).strip()
    return IntentMatch(command=command, context=context, source="keyword", confidence=1.0)


def _url_type_ok(command, subject_kind):
    """Whether ``command`` can run against a subject of ``subject_kind``.

    ``subject_kind`` is "pr", "issue", or "" (unknown). Never block on missing
    info: an unknown subject always passes.
    """
    if not subject_kind:
        return True
    if command in _NEEDS_PR and subject_kind != "pr":
        return False
    if command in _NEEDS_ISSUE and subject_kind != "issue":
        return False
    return True


def _model_candidates(registry):
    """github-enabled (command, description) tuples for the model classifier,
    excluding meta commands. Sorted, deduplicated by primary name."""
    seen = {}
    for skill in registry.list_all():
        if not getattr(skill, "github_enabled", False):
            continue
        for cmd in skill.commands:
            if cmd.name in _META_COMMANDS or cmd.name in seen:
                continue
            seen[cmd.name] = cmd.description or skill.description
    return sorted(seen.items())


def resolve_github_intent(
    text,
    registry,
    subject_kind="",
    project_path=None,
    min_confidence=0.75,
    keyword_window=5,
):
    """Single entry point for the bridge + /gh_request intent ladder.

    Layer 1 (keyword) → Layer 2 (model + confidence). Returns an ``IntentMatch``
    to promote, or None when the caller should fall through to free-form.
    """
    if not text or not text.strip():
        return None

    # Layer 1 — keyword (free, deterministic)
    match = match_skill_keyword(text, registry, window=keyword_window)
    if match:
        if _url_type_ok(match.command, subject_kind):
            log.info(
                "GitHub intent: source=keyword command=%s conf=1.0 text=%s",
                match.command, text[:80],
            )
            return match
        # Confident keyword hit rejected by the URL-type guard (e.g. a `review`
        # keyword on an Issue). Record the near-miss before escalating.
        log.debug(
            "GitHub intent: keyword command=%s discarded by URL guard "
            "(subject_kind=%s); escalating to model",
            match.command, subject_kind or "unknown",
        )

    # Layer 2 — cheap model + confidence gate
    if not project_path:
        log.debug(
            "GitHub intent: skipping model classification — no project_path",
        )
        return None
    candidates = _model_candidates(registry)
    if not candidates:
        return None
    result = classify_intent(text, candidates, project_path, subject_kind=subject_kind)
    if not result:
        return None
    command = result.get("command")
    confidence = result.get("confidence", 0.0)
    if not command or command in _META_COMMANDS or confidence < min_confidence:
        return None
    if not _url_type_ok(command, subject_kind):
        return None
    skill = registry.find_by_command(command)
    if skill is None or not getattr(skill, "github_enabled", False):
        return None
    log.info(
        "GitHub intent: source=model command=%s conf=%.2f text=%s",
        command, confidence, text[:80],
    )
    return IntentMatch(
        command=command,
        context=result.get("context", ""),
        source="model",
        confidence=confidence,
    )


def classify_intent(
    message: str,
    commands: List[Tuple[str, str]],
    project_path: str,
    subject_kind: str = "",
) -> Optional[dict]:
    """Classify a natural-language @mention into a bot command.

    Args:
        message: The raw comment text (after @mention, code blocks stripped).
        commands: List of (command_name, description) tuples for available
            github-enabled commands.
        project_path: Path to the project directory (for Claude CLI).

    Returns:
        Dict with "command" (str or None) and "context" (str) keys,
        or None if classification failed (CLI error, timeout, etc.).
    """
    if not message or not message.strip():
        return None

    if not commands:
        return None

    from app.cli_provider import run_command
    from app.prompts import load_prompt

    # Build the commands list for the prompt
    commands_text = "\n".join(
        f"- `{name}` — {desc}" for name, desc in commands
    )

    # Load and fill the prompt template
    prompt_template = load_prompt("github-intent")
    if not prompt_template:
        log.warning("GitHub intent: could not load github-intent.md prompt")
        return None

    prompt = prompt_template.replace("{COMMANDS}", commands_text)
    prompt = prompt.replace("{MESSAGE}", message.strip())
    hint = {
        "pr": "This comment is on a Pull Request.",
        "issue": "This comment is on an Issue.",
    }.get(subject_kind, "")
    prompt = prompt.replace("{SUBJECT_KIND}", hint)

    try:
        output = run_command(
            prompt=prompt,
            project_path=project_path,
            allowed_tools=[],
            model_key="lightweight",
            max_turns=1,
            timeout=30,
            max_turns_source=None,
        )
    except (RuntimeError, OSError) as e:
        log.warning("GitHub intent: Claude CLI failed: %s", e)
        return None

    return _parse_classification(output)


def _parse_classification(output: str) -> Optional[dict]:
    """Parse the JSON classification from Claude's output.

    Handles various output formats: raw JSON, JSON in code blocks,
    or JSON embedded in explanatory text.

    Returns:
        Dict with "command" and "context" keys, or None on parse failure.
    """
    if not output or not output.strip():
        return None

    text = output.strip()

    # Try to extract JSON from code block first
    import re
    code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if code_block:
        text = code_block.group(1).strip()

    # Try to find a JSON object in the text
    brace_start = text.find('{')
    brace_end = text.rfind('}')
    if brace_start >= 0 and brace_end > brace_start:
        text = text[brace_start:brace_end + 1]

    try:
        result = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.debug("GitHub intent: failed to parse JSON: %s", text[:200])
        return None

    if not isinstance(result, dict):
        return None

    # Normalize the result
    command = result.get("command")
    context = str(result.get("context", "")).strip()

    # command must be a string or None
    if command is not None:
        command = str(command).strip().lstrip("/")
        if not command:
            command = None

    # confidence: fail closed to 0.0 (→ below threshold → free-form) when
    # missing or unparseable; clamp to [0.0, 1.0]. Log the degradation so a
    # wholesale Layer-2 outage (prompt drift / model swap dropping the field)
    # is observable instead of silently disabling the model layer.
    if "confidence" not in result:
        log.warning(
            "GitHub intent: model omitted 'confidence' (command=%r); "
            "failing closed to 0.0 → free-form",
            command,
        )
    confidence_raw = result.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        log.warning(
            "GitHub intent: unparseable 'confidence' %r; failing closed to 0.0",
            confidence_raw,
        )
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    return {"command": command, "context": context, "confidence": confidence}
