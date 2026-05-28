"""
Token Parser — Single source of truth for Claude JSON output token extraction.

Parses Claude CLI JSON output files to extract token usage, cache metrics,
model info, and cost data. All modules that need token data should import
from here rather than implementing their own parsing.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TokenResult:
    """Structured token usage extracted from Claude JSON output."""

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
        if result is not None and result.total_tokens > 0:
            last_result = result
    return last_result


def _extract_tokens_from_dict(data: dict) -> Optional[TokenResult]:
    """Extract token info from one JSON object/event."""
    model = data.get("model", "unknown")

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
                return _build_result(inp, out, model, data)

    return None


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
