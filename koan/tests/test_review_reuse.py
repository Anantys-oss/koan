"""Behavior tests for the reuse decision (spec 010, US1, FR-001)."""

from app.review_reuse import request_signature, should_reuse


class TestRequestSignature:
    def test_normalizes_order_and_dedup(self):
        a = request_signature(["--errors", "--architecture", "--errors"], False)
        b = request_signature(["--architecture", "--errors"], False)
        assert a == b
        assert a["focus_flags"] == ["--architecture", "--errors"]

    def test_coerces_discovery_bool(self):
        assert request_signature([], 1)["discovery_enabled"] is True
        assert request_signature([], 0)["discovery_enabled"] is False

    def test_ignores_blank_flags(self):
        assert request_signature(["", "  ", "--comments"], False)["focus_flags"] \
            == ["--comments"]


def _record(head="h1", base="b1", flags=None, discovery=False):
    return {
        "head_sha": head,
        "base_sha": base,
        "request_signature": request_signature(flags or [], discovery),
    }


class TestShouldReuse:
    def test_reuse_when_all_match(self):
        prior = _record()
        assert should_reuse(prior, "h1", "b1", request_signature([], False))

    def test_no_reuse_on_head_mismatch(self):
        assert not should_reuse(_record(head="h1"), "h2", "b1",
                                request_signature([], False))

    def test_no_reuse_on_base_movement(self):
        # Head identical, base moved -> effective diff changed -> re-derive (D2).
        assert not should_reuse(_record(base="b1"), "h1", "b2",
                                request_signature([], False))

    def test_no_reuse_on_signature_mismatch(self):
        prior = _record(flags=["--architecture"])
        assert not should_reuse(prior, "h1", "b1",
                                request_signature([], False))

    def test_no_reuse_on_discovery_toggle(self):
        prior = _record(discovery=False)
        assert not should_reuse(prior, "h1", "b1",
                                request_signature([], True))

    def test_no_reuse_without_prior_record(self):
        assert not should_reuse(None, "h1", "b1", request_signature([], False))
        assert not should_reuse({}, "h1", "b1", request_signature([], False))

    def test_no_reuse_when_shas_blank(self):
        assert not should_reuse(_record(), "", "b1", request_signature([], False))
        assert not should_reuse(_record(), "h1", "", request_signature([], False))

    def test_no_reuse_when_prior_missing_signature(self):
        prior = {"head_sha": "h1", "base_sha": "b1"}
        assert not should_reuse(prior, "h1", "b1", request_signature([], False))
