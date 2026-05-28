"""Tests for token_parser.py — Claude JSON output token extraction."""

import json
import pytest
from pathlib import Path

from app.token_parser import (
    TokenResult,
    extract_tokens,
    extract_session_id,
    compute_cache_hit_rate,
)


@pytest.fixture
def claude_json_toplevel(tmp_path):
    f = tmp_path / "toplevel.json"
    f.write_text(json.dumps({
        "input_tokens": 1500,
        "output_tokens": 500,
        "model": "claude-sonnet-4-20250514",
    }))
    return f


@pytest.fixture
def claude_json_nested(tmp_path):
    f = tmp_path / "nested.json"
    f.write_text(json.dumps({
        "result": "Done.",
        "model": "claude-opus-4-20250514",
        "usage": {
            "input_tokens": 3000,
            "output_tokens": 1000,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 2000,
        },
    }))
    return f


@pytest.fixture
def claude_json_camel(tmp_path):
    f = tmp_path / "camel.json"
    f.write_text(json.dumps({
        "input_tokens": 100,
        "output_tokens": 50,
        "modelUsage": {
            "claude-sonnet": {
                "cacheCreationInputTokens": 200,
                "cacheReadInputTokens": 800,
            }
        },
    }))
    return f


class TestExtractTokens:
    def test_toplevel_fields(self, claude_json_toplevel):
        result = extract_tokens(claude_json_toplevel)
        assert result is not None
        assert result.input_tokens == 1500
        assert result.output_tokens == 500
        assert result.model == "claude-sonnet-4-20250514"
        assert result.total_tokens == 2000

    def test_nested_usage(self, claude_json_nested):
        result = extract_tokens(claude_json_nested)
        assert result is not None
        assert result.input_tokens == 3000
        assert result.output_tokens == 1000
        assert result.cache_creation_input_tokens == 500
        assert result.cache_read_input_tokens == 2000

    def test_camelcase_model_usage(self, claude_json_camel):
        result = extract_tokens(claude_json_camel)
        assert result is not None
        assert result.cache_creation_input_tokens == 200
        assert result.cache_read_input_tokens == 800

    def test_stats_fallback(self, tmp_path):
        f = tmp_path / "stats.json"
        f.write_text(json.dumps({
            "stats": {"input_tokens": 100, "output_tokens": 50},
        }))
        result = extract_tokens(f)
        assert result is not None
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    def test_nonexistent_file(self, tmp_path):
        assert extract_tokens(tmp_path / "nope.json") is None

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        assert extract_tokens(f) is None

    def test_no_tokens(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text(json.dumps({"result": "hello"}))
        assert extract_tokens(f) is None

    def test_cost_usd(self, tmp_path):
        f = tmp_path / "cost.json"
        f.write_text(json.dumps({
            "input_tokens": 100,
            "output_tokens": 50,
            "total_cost_usd": 0.0042,
        }))
        result = extract_tokens(f)
        assert result is not None
        assert result.cost_usd == 0.0042

    def test_to_dict_roundtrip(self, claude_json_nested):
        result = extract_tokens(claude_json_nested)
        d = result.to_dict()
        assert d["input_tokens"] == 3000
        assert d["cache_read_input_tokens"] == 2000
        assert d["model"] == "claude-opus-4-20250514"

    def test_codex_jsonl_turn_completed_usage(self, tmp_path):
        f = tmp_path / "codex.jsonl"
        f.write_text("\n".join([
            json.dumps({"type": "thread.started"}),
            json.dumps({
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 2769595,
                    "cached_input_tokens": 2650240,
                    "output_tokens": 16146,
                    "reasoning_output_tokens": 8124,
                },
            }),
        ]))

        result = extract_tokens(f)

        assert result is not None
        assert result.input_tokens == 119355
        assert result.cache_read_input_tokens == 2650240
        assert result.output_tokens == 16146

    def test_codex_rollout_token_count_total_usage(self, tmp_path):
        f = tmp_path / "codex-rollout.jsonl"
        f.write_text("\n".join([
            json.dumps({"type": "thread.started"}),
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 26549,
                            "cached_input_tokens": 22272,
                            "output_tokens": 1590,
                            "reasoning_output_tokens": 1152,
                            "total_tokens": 28139,
                        }
                    },
                },
            }),
        ]))

        result = extract_tokens(f)

        assert result is not None
        assert result.input_tokens == 4277
        assert result.cache_read_input_tokens == 22272
        assert result.output_tokens == 1590

    def test_codex_jsonl_last_usage_event_wins(self, tmp_path):
        f = tmp_path / "codex-mixed.jsonl"
        f.write_text("\n".join([
            json.dumps({
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1010,
                    "cached_input_tokens": 1000,
                    "output_tokens": 11,
                },
            }),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message"}}),
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 2000,
                            "cached_input_tokens": 1500,
                            "output_tokens": 400,
                        }
                    },
                },
            }),
        ]))

        result = extract_tokens(f)

        assert result is not None
        assert result.input_tokens == 500
        assert result.cache_read_input_tokens == 1500
        assert result.output_tokens == 400

    def test_codex_rollout_without_total_usage_returns_none(self, tmp_path):
        f = tmp_path / "codex-empty-token-count.jsonl"
        f.write_text("\n".join([
            json.dumps({"type": "thread.started"}),
            json.dumps({
                "type": "event_msg",
                "payload": {"type": "token_count", "info": None},
            }),
        ]))

        assert extract_tokens(f) is None


class TestCacheHitRate:
    def test_basic_hit_rate(self):
        assert compute_cache_hit_rate(100, 800, 100) == 0.8

    def test_zero_tokens(self):
        assert compute_cache_hit_rate(0, 0, 0) == 0.0

    def test_no_cache(self):
        assert compute_cache_hit_rate(1000, 0, 0) == 0.0

    def test_full_cache(self):
        assert compute_cache_hit_rate(0, 1000, 0) == 1.0

    def test_token_result_method(self, claude_json_nested):
        result = extract_tokens(claude_json_nested)
        # 2000 / (3000 + 2000 + 500) = 2000/5500 ≈ 0.3636
        assert abs(result.cache_hit_rate() - 2000 / 5500) < 0.001


class TestExtractSessionId:
    """Tests for extract_session_id()."""

    def test_extracts_session_id(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text(json.dumps({
            "result": "Done.",
            "session_id": "550e8400-e29b-41d4-a716-446655440000",
            "input_tokens": 100,
            "output_tokens": 50,
        }))
        assert extract_session_id(f) == "550e8400-e29b-41d4-a716-446655440000"

    def test_returns_none_for_missing_field(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"result": "Done.", "input_tokens": 100}))
        assert extract_session_id(f) is None

    def test_returns_none_for_empty_string(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"session_id": "", "input_tokens": 100}))
        assert extract_session_id(f) is None

    def test_returns_none_for_whitespace_only(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"session_id": "  ", "input_tokens": 100}))
        assert extract_session_id(f) is None

    def test_returns_none_for_nonexistent_file(self, tmp_path):
        assert extract_session_id(tmp_path / "nope.json") is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        assert extract_session_id(f) is None

    def test_returns_none_for_non_string_session_id(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"session_id": 12345, "input_tokens": 100}))
        assert extract_session_id(f) is None

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"session_id": "  abc-123  "}))
        assert extract_session_id(f) == "abc-123"
