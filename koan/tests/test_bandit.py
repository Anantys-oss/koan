"""Tests for bandit.py — Thompson Sampling multi-armed bandit."""

import json
from pathlib import Path

import pytest

from app.bandit import (
    BanditState,
    load_bandit_state,
    save_bandit_state,
    thompson_sample,
    update_bandit,
)


# ---------------------------------------------------------------------------
# thompson_sample
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alpha,beta", [
    (1.0, 1.0),
    (10.0, 1.0),
    (1.0, 10.0),
    (5.0, 5.0),
    (100.0, 1.0),
])
def test_thompson_sample_valid_range(alpha, beta):
    """Sampled value is always in [0, 1]."""
    state = BanditState(params={"proj": (alpha, beta)})
    for _ in range(50):
        val = thompson_sample(state, "proj")
        assert 0.0 <= val <= 1.0


def test_thompson_sample_unknown_project_uses_prior():
    """Unknown project samples from (1, 1) uniform prior."""
    state = BanditState()
    val = thompson_sample(state, "new_project")
    assert 0.0 <= val <= 1.0


# ---------------------------------------------------------------------------
# update_bandit
# ---------------------------------------------------------------------------

def test_update_bandit_success():
    """Success increments alpha by 1."""
    state = BanditState(params={"proj": (1.0, 1.0)})
    update_bandit(state, "proj", success=True)
    assert state.params["proj"] == (2.0, 1.0)


def test_update_bandit_failure():
    """Failure increments beta by 1."""
    state = BanditState(params={"proj": (1.0, 1.0)})
    update_bandit(state, "proj", success=False)
    assert state.params["proj"] == (1.0, 2.0)


def test_update_bandit_new_project_success():
    """Updating an unseen project starts from (1,1) prior then increments alpha."""
    state = BanditState()
    update_bandit(state, "new_proj", success=True)
    assert state.params["new_proj"] == (2.0, 1.0)


def test_update_bandit_new_project_failure():
    """Updating an unseen project starts from (1,1) prior then increments beta."""
    state = BanditState()
    update_bandit(state, "new_proj", success=False)
    assert state.params["new_proj"] == (1.0, 2.0)


# ---------------------------------------------------------------------------
# load_bandit_state
# ---------------------------------------------------------------------------

def test_load_missing_file(tmp_path):
    """Returns a fresh BanditState when the file is absent."""
    state = load_bandit_state(str(tmp_path))
    assert isinstance(state, BanditState)
    assert state.params == {}


def test_load_malformed_json(tmp_path):
    """Returns a fresh BanditState on parse error."""
    (tmp_path / ".bandit-state.json").write_text("not valid json")
    state = load_bandit_state(str(tmp_path))
    assert isinstance(state, BanditState)
    assert state.params == {}


def test_load_invalid_values_ignored(tmp_path):
    """Entries with invalid (alpha, beta) values are silently dropped."""
    data = {
        "good": [5.0, 3.0],
        "zero_alpha": [0.0, 1.0],  # alpha must be > 0
        "wrong_length": [1.0, 2.0, 3.0],
        "non_numeric": ["a", "b"],
    }
    (tmp_path / ".bandit-state.json").write_text(json.dumps(data))
    state = load_bandit_state(str(tmp_path))
    assert "good" in state.params
    assert state.params["good"] == (5.0, 3.0)
    assert "zero_alpha" not in state.params
    assert "wrong_length" not in state.params
    assert "non_numeric" not in state.params


# ---------------------------------------------------------------------------
# save_bandit_state / round-trip
# ---------------------------------------------------------------------------

def test_save_and_reload(tmp_path):
    """Round-trip write/read preserves values exactly."""
    state = BanditState(params={"proj_a": (3.0, 7.0), "proj_b": (10.0, 2.0)})
    save_bandit_state(state, str(tmp_path))

    loaded = load_bandit_state(str(tmp_path))
    assert loaded.params["proj_a"] == (3.0, 7.0)
    assert loaded.params["proj_b"] == (10.0, 2.0)


def test_save_creates_file(tmp_path):
    """save_bandit_state creates the file if it does not exist."""
    state = BanditState(params={"proj": (2.0, 1.0)})
    save_bandit_state(state, str(tmp_path))
    assert (tmp_path / ".bandit-state.json").exists()


# ---------------------------------------------------------------------------
# Thompson Sampling selection bias
# ---------------------------------------------------------------------------

def test_high_alpha_wins_more():
    """A project with alpha=10, beta=1 is selected far more often than alpha=1, beta=10.

    Over 100 trials we check that the high-quality project wins at least 70%.
    The test uses argmax of Beta samples (same logic as iteration_manager).
    """
    state = BanditState(params={
        "good": (10.0, 1.0),
        "bad": (1.0, 10.0),
    })
    candidates = ["good", "bad"]
    wins = {"good": 0, "bad": 0}

    for _ in range(100):
        samples = {name: thompson_sample(state, name) for name in candidates}
        winner = max(samples, key=samples.__getitem__)
        wins[winner] += 1

    assert wins["good"] > 70, (
        f"Expected 'good' to win >70/100 trials, got {wins['good']}"
    )
