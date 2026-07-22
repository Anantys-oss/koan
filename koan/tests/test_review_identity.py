"""Behavior tests for review finding identity (spec 010, FR-002).

Pure functions — no provider, no KOAN_ROOT-dependent imports.
"""

from app.review_identity import REGION_WINDOW, finding_key, same_finding


def _f(file="a.py", line=10, title="", comment=""):
    return {
        "file": file,
        "line_start": line,
        "line_end": line,
        "title": title,
        "comment": comment,
    }


class TestFindingKeyTolerance:
    def test_reworded_title_same_key(self):
        # Same file, same region, same category (security) but different wording.
        a = _f(title="SQL injection in query builder",
               comment="user input is concatenated into SQL")
        b = _f(title="Unsanitized input reaches the SQL string",
               comment="possible sql injection via concatenation")
        assert finding_key(a) == finding_key(b)

    def test_small_shift_within_bucket_same_key(self):
        # Lines within the same REGION_WINDOW bucket => identical key.
        a = _f(line=41, title="missing null check", comment="None deref")
        b = _f(line=48, title="missing null check", comment="None deref")
        assert a["line_start"] // REGION_WINDOW == b["line_start"] // REGION_WINDOW
        assert finding_key(a) == finding_key(b)

    def test_cross_bucket_shift_matched_by_same_finding(self):
        # A shift across a bucket boundary may change finding_key; cross-run
        # matching therefore uses same_finding (tolerant), which still matches.
        a = _f(line=19, title="missing null check", comment="None deref")
        b = _f(line=21, title="missing null check", comment="None deref")
        assert same_finding(a, b, line_tolerance=REGION_WINDOW)

    def test_different_file_different_key(self):
        a = _f(file="a.py", title="style nit")
        b = _f(file="b.py", title="style nit")
        assert finding_key(a) != finding_key(b)

    def test_different_category_different_key(self):
        a = _f(title="SQL injection risk", comment="injection")
        b = _f(title="rename this variable for clarity", comment="naming")
        assert finding_key(a) != finding_key(b)

    def test_key_is_stable_string(self):
        a = _f(title="race condition on shared counter")
        assert isinstance(finding_key(a), str)
        assert finding_key(a) == finding_key(dict(a))


class TestCategoryClassification:
    def test_security_bucket(self):
        assert "security" in finding_key(_f(title="XSS in template render"))

    def test_error_handling_bucket(self):
        assert "error-handling" in finding_key(
            _f(title="bare except swallows the failure"))

    def test_unmatched_is_other(self):
        assert finding_key(_f(title="", comment="")).endswith("|other")

    def test_author_mention_not_security(self):
        # "author"/"authored" must not be misclassified as security (M2 fix).
        key = finding_key(_f(title="the author's variable name is unclear",
                             comment="please rename for clarity"))
        assert "security" not in key

    def test_authorization_is_security(self):
        # But real auth terms still classify as security.
        assert "security" in finding_key(_f(title="missing authorization check"))
        assert "security" in finding_key(_f(title="auth check is bypassed"))


class TestSameFinding:
    def test_drift_within_tolerance_matches(self):
        a = _f(line=40, title="off-by-one in loop bound")
        b = _f(line=40 + REGION_WINDOW, title="loop bound is off by one")
        assert same_finding(a, b, line_tolerance=REGION_WINDOW)

    def test_drift_beyond_tolerance_does_not_match(self):
        a = _f(line=40, title="off-by-one in loop bound")
        b = _f(line=400, title="off-by-one in loop bound")
        assert not same_finding(a, b, line_tolerance=REGION_WINDOW)

    def test_different_category_never_matches(self):
        a = _f(line=40, title="SQL injection")
        b = _f(line=40, title="please add a docstring")
        assert not same_finding(a, b)

    def test_unknown_anchor_matches_on_file_and_category(self):
        a = _f(line=0, title="SQL injection")
        b = _f(line=999, title="injection via concatenation")
        assert same_finding(a, b)

    def test_different_file_never_matches(self):
        a = _f(file="a.py", line=10, title="perf: quadratic loop")
        b = _f(file="b.py", line=10, title="perf: quadratic loop")
        assert not same_finding(a, b)
