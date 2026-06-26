from app.usage_service import _bucket_by_week, build_usage_payload


def test_build_usage_payload_shape(tmp_path):
    # Empty instance dir → zeroed payload with all contract keys present.
    payload = build_usage_payload(tmp_path, days=7, project="", granularity="day",
                                  stacked=False, offset=0)
    for key in ("days", "start", "end", "total_input", "total_output",
                "cache_hit_rate", "count", "by_project", "by_model",
                "has_pricing", "estimated_cost", "estimated_cache_savings",
                "series", "granularity", "offset", "by_type", "by_mode"):
        assert key in payload
    assert payload["days"] == 7
    assert payload["granularity"] == "day"


def test_build_usage_payload_clamps_days(tmp_path):
    assert build_usage_payload(tmp_path, days=9999, project="", granularity="day",
                               stacked=False, offset=0)["days"] == 100
    assert build_usage_payload(tmp_path, days=0, project="", granularity="day",
                               stacked=False, offset=0)["days"] == 1


def test_bucket_by_week_aggregates():
    series = [
        {"date": "2026-06-01", "total_input": 10, "total_output": 5, "count": 1,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "cost": 1.0},
        {"date": "2026-06-02", "total_input": 20, "total_output": 7, "count": 2,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "cost": 2.0},
    ]
    out = _bucket_by_week(series)
    assert len(out) == 1
    assert out[0]["total_input"] == 30
    assert out[0]["cost"] == 3.0
