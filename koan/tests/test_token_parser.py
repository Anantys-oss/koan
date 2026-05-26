"""Tests for token_parser.py — Claude JSON output token extraction."""

import json
import pytest
from pathlib import Path

from app.token_parser import TokenResult, extract_tokens, compute_cache_hit_rate


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
