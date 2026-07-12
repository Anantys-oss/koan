from app.mission_outcome import classify_failure


def test_zero_exit_has_no_category():
    assert classify_failure(0, stagnated=False, cause_tag="") is None


def test_stagnation_flag():
    assert classify_failure(1, stagnated=True, cause_tag="stagnation:loop") == "stagnation"


def test_stagnation_from_cause_tag_without_flag():
    assert classify_failure(1, stagnated=False, cause_tag="stagnation:loop") == "stagnation"


def test_default_nonzero_is_agent_error():
    assert classify_failure(1, stagnated=False, cause_tag="") == "agent_error"


def test_sigterm_timeout():
    # 128+15 (SIGTERM) is how the mission-timeout kill surfaces
    assert classify_failure(143, stagnated=False, cause_tag="") == "timeout"


def test_sigkill_timeout():
    assert classify_failure(137, stagnated=False, cause_tag="") == "timeout"
