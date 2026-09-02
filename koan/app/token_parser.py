"""
Token Parser — Single source of truth for provider JSON token extraction.

Parses provider JSON/JSONL output files (Claude and Codex) to extract token
usage, cache metrics, model info, and cost data. All modules that need token
data should import from here rather than implementing their own parsing.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TokenResult:
    """Structured token usage extracted from provider output."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "unknown"
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cache_hit_rate(self) -> float:
        """Compute cache hit rate: cache_read / total_input_with_cache."""
        return compute_cache_hit_rate(
            self.input_tokens,
            self.cache_read_input_tokens,
            self.cache_creation_input_tokens,
        )

    def to_dict(self) -> dict:
        """Convert to dict for backward compatibility with existing callers."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cost_usd": self.cost_usd,
        }


def compute_cache_hit_rate(
    input_tokens: int, cache_read: int, cache_create: int
) -> float:
    """Compute cache hit rate from token components.

    Formula: cache_read / (input_tokens + cache_read + cache_create)
    where input_tokens is the non-cached input count.
    """
    total = input_tokens + cache_read + cache_create
    if total <= 0:
        return 0.0
    return cache_read / total


def extract_tokens(claude_json_path: Path) -> Optional[TokenResult]:
    """Extract structured token info from Claude JSON output.

    Tries multiple known field layouts:
    - Top-level: input_tokens + output_tokens
    - Nested: usage.input_tokens + usage.output_tokens
    - Fallback keys: stats, metadata, session

    Returns:
        TokenResult with all fields populated, or None if no tokens found
        or file unreadable.
    """
    try:
        raw = claude_json_path.read_text()
    except OSError:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _extract_tokens_from_jsonl(raw)

    if isinstance(data, dict):
        return _extract_tokens_from_dict(data)

    return None


def _extract_tokens_from_jsonl(raw: str) -> Optional[TokenResult]:
    """Extract the last usage-bearing event from provider JSONL output."""
    last_result: Optional[TokenResult] = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        result = _extract_tokens_from_dict(event)
        if result is not None and _has_usage(result):
            last_result = result
    return last_result


def _primary_model_from_usage(data: dict) -> str:
    """Extract primary model name from modelUsage keys.

    Claude CLI ``--output-format json`` omits a top-level ``model`` field but
    includes a ``modelUsage`` dict keyed by model identifier.  When multiple
    models appear (e.g. Haiku for summarisation + Opus for the task), pick the
    one with the highest reported cost.
    """
    model_usage = data.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return ""
    if len(model_usage) == 1:
        return next(iter(model_usage))
    best = ""
    best_cost = -1.0
    for name, stats in model_usage.items():
        if not isinstance(stats, dict):
            continue
        cost = stats.get("costUSD", 0) or 0
        if cost > best_cost:
            best_cost = cost
            best = name
    return best


def _extract_tokens_from_dict(data: dict) -> Optional[TokenResult]:
    """Extract token info from one JSON object/event."""
    codex_result = _extract_codex_token_count(data)
    if codex_result is not None:
        return codex_result

    camel_result = _extract_camelcase_usage(data)
    if camel_result is not None:
        return camel_result

    model = data.get("model") or _primary_model_from_usage(data) or "unknown"

    # Try top-level fields
    inp = data.get("input_tokens", 0)
    out = data.get("output_tokens", 0)
    if inp or out:
        return _build_result(inp, out, model, data)

    # Try nested usage object
    usage = data.get("usage", {})
    if isinstance(usage, dict):
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        if inp or out:
            return _build_result(inp, out, model, data)

    # Try stats or metadata
    for key in ("stats", "metadata", "session"):
        sub = data.get(key, {})
        if isinstance(sub, dict):
            inp = sub.get("input_tokens", 0)
            out = sub.get("output_tokens", 0)
            if inp or out:
                return _build_result(
                    inp,
                    out,
                    _stats_model(sub, model),
                    _with_stats_cache(data, sub, inp),
                )
            nested = _extract_nested_model_tokens(data, sub, model)
            if nested is not None:
                return nested

    return None


def _extract_nested_model_tokens(
    data: dict, stats: dict, model: str
) -> Optional[TokenResult]:
    """Extract tokens from a per-model ``stats.models.<id>.tokens`` breakdown.

    Gemini CLI's ``--output-format json`` object — the shape the mission path
    actually produces — reports no flattened ``stats.input_tokens``; the counts
    live under ``models.<id>.tokens.{prompt,candidates,cached}``. Counts are
    summed across models (a session can fall back pro→flash mid-run) and the
    model id is the dominant one. Shape-keyed on the nested field names, so no
    other stats-reporting envelope is affected.
    """
    models = stats.get("models")
    if not isinstance(models, dict) or not models:
        return None

    total_in = total_out = total_cached = 0
    for entry in models.values():
        tokens = entry.get("tokens") if isinstance(entry, dict) else None
        if not isinstance(tokens, dict):
            continue
        total_in += int(tokens.get("prompt", 0) or 0)
        total_out += int(tokens.get("candidates", 0) or 0)
        total_cached += int(tokens.get("cached", 0) or 0)

    if not (total_in or total_out):
        return None

    # ``cached`` is a SUBSET of prompt tokens; Koan input excludes cache hits.
    cached = clamp_cached_input(total_cached, total_in)
    resolved = model if model and model != "unknown" else dominant_stats_model(models)
    return _build_result(
        total_in - cached,
        total_out,
        resolved or "unknown",
        {**data, "usage": {"cache_read_input_tokens": cached}} if cached else data,
    )


def _stats_model(stats: dict, fallback: str) -> str:
    """Resolve the model id from a ``stats.models`` map when *fallback* is unknown.

    Gemini CLI reports per-model token breakdowns keyed by model id and has no
    top-level ``model`` field on its result event.
    """
    if fallback and fallback != "unknown":
        return fallback
    return dominant_stats_model(stats.get("models")) or fallback


def dominant_stats_model(models) -> str:
    """Pick the model id accounting for the most tokens in a ``stats.models`` map.

    Mirrors :func:`_primary_model_from_usage`: a session that fell back
    pro→flash under quota pressure must not be priced against whichever id
    happens to come first in the dict. Handles both the flattened
    (``total_tokens``) and the nested (``tokens.total``) per-model shapes.
    """
    if not isinstance(models, dict) or not models:
        return ""
    if len(models) == 1:
        return str(next(iter(models)))
    best = ""
    best_total = -1
    for name, entry in models.items():
        total = _model_entry_total(entry)
        if total > best_total:
            best_total = total
            best = str(name)
    return best


def _model_entry_total(entry) -> int:
    """Total tokens for one ``stats.models`` entry, flattened or nested."""
    if not isinstance(entry, dict):
        return 0
    total = int(entry.get("total_tokens", 0) or 0)
    if total:
        return total
    tokens = entry.get("tokens")
    if isinstance(tokens, dict):
        total = int(tokens.get("total", 0) or 0)
        if total:
            return total
        return int(tokens.get("prompt", 0) or 0) + int(
            tokens.get("candidates", 0) or 0
        )
    return int(entry.get("input_tokens", 0) or 0) + int(
        entry.get("output_tokens", 0) or 0
    )


_WARNED_CACHE_INCONSISTENCY = False


def _warn(message: str) -> None:
    """Log a token-accounting warning without importing run_log at module load."""
    from app.run_log import log_safe
    log_safe("warning", f"[tokens] {message}")


def clamp_cached_input(cached, input_tokens) -> int:
    """Return the cache-hit count safe to subtract from *input_tokens*.

    A ``cached`` count is a SUBSET of input tokens. When a provider reports
    more cache hits than input, keeping the raw figure would count the same
    tokens twice (once in input, once as cache reads), so both fields are
    clamped together and the inconsistency is logged once per process rather
    than silently skipped.
    """
    global _WARNED_CACHE_INCONSISTENCY
    cached = int(cached or 0)
    input_tokens = int(input_tokens or 0)
    if cached <= 0:
        return 0
    if cached > input_tokens:
        if not _WARNED_CACHE_INCONSISTENCY:
            _WARNED_CACHE_INCONSISTENCY = True
            _warn(
                f"cached={cached} exceeds input_tokens={input_tokens}; "
                "clamping both to avoid double counting"
            )
        return input_tokens
    return cached


def _with_stats_cache(data: dict, stats: dict, input_tokens) -> dict:
    """Surface a ``stats.cached`` count in the shape ``_build_result`` reads.

    Shape-keyed, not provider-keyed: a ``cached`` field inside a stats object is
    a SUBSET of ``input_tokens`` (Gemini CLI semantics), and Koan accounting
    excludes cache hits from input. Returns *data* unchanged when there is no
    cache count, so no other stats-reporting shape is affected. An existing
    ``usage`` object wins (it is the more specific source) but is logged as a
    conflict rather than dropped silently.
    """
    cached = clamp_cached_input(stats.get("cached", 0), input_tokens)
    if not cached:
        return data
    existing = data.get("usage")
    if isinstance(existing, dict) and existing:
        if not existing.get("cached_input_tokens") and not existing.get(
            "cache_read_input_tokens"
        ):
            return {**data, "usage": {**existing, "cached_input_tokens": cached}}
        return data
    return {**data, "usage": {"cached_input_tokens": cached}}


def _has_usage(result: TokenResult) -> bool:
    """Return True when any token bucket is populated."""
    return (
        result.input_tokens > 0
        or result.output_tokens > 0
        or result.cache_creation_input_tokens > 0
        or result.cache_read_input_tokens > 0
    )


def _extract_camelcase_usage(data: dict) -> Optional[TokenResult]:
    """Extract camelCase ``usage`` objects (haze-style result envelopes).

    Haze >= 0.7.0 reports usage in its terminal envelope as
    ``{"usage": {"inputTokens": N, "outputTokens": N, "cacheReadTokens": N,
    "cacheWriteTokens": N, "reasoningTokens": N}}``. Shape-keyed on field
    presence so any camelCase-reporting CLI benefits — no provider names.
    Returns None when the shape is absent or every bucket is zero.
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    if "inputTokens" not in usage and "outputTokens" not in usage:
        return None

    input_tokens = int(usage.get("inputTokens", 0) or 0)
    # reasoningTokens is a SUBSET of outputTokens in AI-SDK-based reporting
    # (OpenAI completion_tokens_details semantics) — already accounted inside
    # outputTokens; adding it would double-count reasoning-model output.
    output_tokens = int(usage.get("outputTokens", 0) or 0)
    cache_read = int(usage.get("cacheReadTokens", 0) or 0)
    cache_write = int(usage.get("cacheWriteTokens", 0) or 0)

    # Align with the rest of Koan accounting: input_tokens excludes cache hits.
    if cache_read > 0:
        input_tokens = max(0, input_tokens - cache_read)

    result = TokenResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=str(data.get("model") or "unknown"),
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )
    return result if _has_usage(result) else None


def _extract_codex_token_count(data: dict) -> Optional[TokenResult]:
    """Extract token usage from Codex token_count rollout events.

    Codex rollout JSONL can include usage details as:
    {
      "type": "event_msg",
      "payload": {
        "type": "token_count",
        "info": {"total_token_usage": {...}}
      }
    }
    """
    payload = data.get("payload")
    if not (
        isinstance(payload, dict)
        and data.get("type") == "event_msg"
        and payload.get("type") == "token_count"
    ):
        return None

    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    total = info.get("total_token_usage")
    if not isinstance(total, dict):
        return None

    input_tokens = int(total.get("input_tokens", 0) or 0)
    output_tokens = int(total.get("output_tokens", 0) or 0)
    cached_input = int(total.get("cached_input_tokens", 0) or 0)

    # Align with the rest of Koan accounting: input_tokens excludes cache hits.
    if cached_input > 0:
        input_tokens = max(0, input_tokens - cached_input)

    model = info.get("model") or data.get("model") or "unknown"
    return TokenResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=str(model),
        cache_read_input_tokens=cached_input,
    )


def _build_result(
    input_tokens: int, output_tokens: int, model: str, data: dict
) -> TokenResult:
    """Build a TokenResult with cache and cost fields from raw JSON data."""
    cache_creation = 0
    cache_read = 0

    # Try nested usage object (snake_case — Claude CLI JSON format)
    usage = data.get("usage", {})
    if isinstance(usage, dict):
        cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cached_input = usage.get("cached_input_tokens", 0) or 0
        if cached_input and not cache_read:
            cache_read = cached_input
            input_tokens = max(0, input_tokens - cache_read)

    # Fallback: modelUsage entries (camelCase — alternate format)
    if not cache_creation and not cache_read:
        model_usage = data.get("modelUsage", {})
        if isinstance(model_usage, dict):
            for model_data in model_usage.values():
                if isinstance(model_data, dict):
                    cache_creation += (
                        model_data.get("cacheCreationInputTokens", 0) or 0
                    )
                    cache_read += (
                        model_data.get("cacheReadInputTokens", 0) or 0
                    )

    # Extract cost_usd from top-level field (reported by Claude CLI)
    cost_usd = data.get("total_cost_usd")
    if cost_usd is not None and isinstance(cost_usd, (int, float)):
        cost_usd = round(cost_usd, 6)
    else:
        cost_usd = 0.0

    return TokenResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        cost_usd=cost_usd,
    )


def extract_session_id(claude_json_path: Path) -> Optional[str]:
    """Extract session_id from Claude CLI JSON output.

    The Claude Code CLI includes a ``session_id`` field in its
    ``--output-format json`` response. This ID can be passed to
    ``--resume`` to continue the same conversation context.

    Returns:
        Session ID string, or None if not found or file unreadable.
    """
    try:
        data = json.loads(claude_json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    sid = data.get("session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None
