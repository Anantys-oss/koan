"""Tests for the feature tip system (app.feature_tips)."""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest

from app.feature_tips import (
    _KEY_DEV_SKILLS,
    _TIP_INTERVAL,
    _format_tip,
    _get_eligible_skills,
    _score_skill,
    mark_active,
    maybe_send_feature_tip,
    pick_tip,
    reset_tip_throttle,
)


# --- Fixtures ---

@dataclass
class FakeCommand:
    name: str
    description: str = ""
    aliases: list = field(default_factory=list)
    usage: str = ""


@dataclass
class FakeSkill:
    name: str
    scope: str
    description: str = ""
    audience: str = "bridge"
    commands: list = field(default_factory=list)


def _make_skill(name, scope="core", audience="bridge", desc="", usage=""):
    cmd = FakeCommand(name=name, description=desc, usage=usage)
    return FakeSkill(name=name, scope=scope, description=desc, audience=audience, commands=[cmd])


class FakeRegistry:
    def __init__(self, skills):
        self._skills = skills

    def list_all(self):
        return self._skills


# --- _get_eligible_skills ---

def test_filters_non_core():
    skills = [
        _make_skill("foo", scope="custom"),
        _make_skill("bar", scope="core"),
    ]
    registry = FakeRegistry(skills)
    result = _get_eligible_skills(registry)
    assert len(result) == 1
    assert result[0].name == "bar"


def test_filters_agent_audience():
    skills = [
        _make_skill("agent_only", audience="agent"),
        _make_skill("bridge_ok", audience="bridge"),
        _make_skill("hybrid_ok", audience="hybrid"),
    ]
    registry = FakeRegistry(skills)
    result = _get_eligible_skills(registry)
    names = {s.name for s in result}
    assert names == {"bridge_ok", "hybrid_ok"}


def test_filters_no_commands():
    skill = FakeSkill(name="empty", scope="core", audience="bridge", commands=[])
    registry = FakeRegistry([skill])
    assert _get_eligible_skills(registry) == []


# --- _format_tip ---

def test_format_tip_basic():
    skill = _make_skill("status", desc="Show Koan status")
    msg = _format_tip(skill)
    assert "/status" in msg
    assert "Show Koan status" in msg
    assert "Did you know?" in msg


def test_format_tip_with_usage():
    skill = _make_skill("plan", desc="Plan an idea", usage="/plan <idea>")
    msg = _format_tip(skill)
    assert "/plan <idea>" in msg
    assert "Example:" in msg


# --- _score_skill ---

class TestScoreSkill:
    def test_recently_hinted_excluded(self):
        assert _score_skill("status", set(), {"status"}) == -1

    def test_unused_key_skill_highest(self):
        score = _score_skill("fix", set(), set())
        assert score == 15  # 10 (unused) + 5 (key dev unused)

    def test_unused_regular_skill(self):
        score = _score_skill("status", set(), set())
        assert score == 10  # 10 (unused) + 0 (not key dev)

    def test_used_key_skill(self):
        score = _score_skill("fix", {"fix"}, set())
        assert score == 2  # 0 (used) + 2 (key dev used)

    def test_used_regular_skill(self):
        score = _score_skill("status", {"status"}, set())
        assert score == 0


# --- pick_tip ---

def test_pick_tip_records_hint(tmp_path):
    skills = [_make_skill("status"), _make_skill("plan")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry):
        tip = pick_tip(str(tmp_path))

    assert tip is not None
    from app.skill_usage import get_recently_hinted
    hinted = get_recently_hinted(str(tmp_path))
    assert len(hinted) == 1


def test_pick_tip_prefers_unused_key_skills(tmp_path):
    skills = [_make_skill("status"), _make_skill("fix")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry):
        tip = pick_tip(str(tmp_path))

    assert "/fix" in tip


def test_pick_tip_skips_recently_hinted(tmp_path):
    from app.skill_usage import record_hint_shown
    record_hint_shown(str(tmp_path), "status")

    skills = [_make_skill("status"), _make_skill("plan")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry):
        tip = pick_tip(str(tmp_path))

    assert "/plan" in tip


def test_pick_tip_skips_used_prefers_unused(tmp_path):
    from app.skill_usage import record_usage
    record_usage(str(tmp_path), "plan")

    skills = [_make_skill("status"), _make_skill("plan")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry):
        tip = pick_tip(str(tmp_path))

    assert "/status" in tip


def test_pick_tip_returns_none_all_hinted(tmp_path):
    from app.skill_usage import record_hint_shown
    record_hint_shown(str(tmp_path), "status")

    skills = [_make_skill("status")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry):
        tip = pick_tip(str(tmp_path))

    assert tip is None


def test_pick_tip_no_skills(tmp_path):
    registry = FakeRegistry([])
    with patch("app.skills.build_registry", return_value=registry):
        assert pick_tip(str(tmp_path)) is None


def test_pick_tip_all_same_score_picks_randomly(tmp_path):
    skills = [_make_skill("a"), _make_skill("b"), _make_skill("c")]
    registry = FakeRegistry(skills)

    seen = set()
    for _ in range(50):
        with patch("app.skills.build_registry", return_value=registry):
            tip = pick_tip(str(tmp_path))
        if tip:
            for s in ["a", "b", "c"]:
                if f"/{s}" in tip:
                    seen.add(s)
    assert len(seen) >= 2


# --- maybe_send_feature_tip ---

def test_maybe_send_throttled(tmp_path):
    reset_tip_throttle()
    skills = [_make_skill("status")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry), \
         patch("app.utils.append_to_outbox") as mock_outbox:
        assert maybe_send_feature_tip(str(tmp_path)) is True
        assert mock_outbox.call_count == 1

        # Second call throttled (hint was just shown, so even without
        # time throttle it would return None — but time throttle catches first)
        assert maybe_send_feature_tip(str(tmp_path)) is False
        assert mock_outbox.call_count == 1

    reset_tip_throttle()


def test_maybe_send_after_interval(tmp_path):
    reset_tip_throttle()
    skills = [_make_skill("status"), _make_skill("plan")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry), \
         patch("app.utils.append_to_outbox") as mock_outbox, \
         patch("app.feature_tips.time") as mock_time:
        mock_time.monotonic.side_effect = [0.0, 0.0 + _TIP_INTERVAL + 1]
        assert maybe_send_feature_tip(str(tmp_path)) is True
        # Productive work resets the idle guard
        mark_active()
        assert maybe_send_feature_tip(str(tmp_path)) is True
        assert mock_outbox.call_count == 2

    reset_tip_throttle()


def test_idle_tip_guard_blocks_second_tip(tmp_path):
    """Only one tip per idle period — second call blocked even after interval."""
    reset_tip_throttle()
    skills = [_make_skill("status"), _make_skill("plan")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry), \
         patch("app.utils.append_to_outbox") as mock_outbox, \
         patch("app.feature_tips.time") as mock_time:
        mock_time.monotonic.side_effect = [0.0, 0.0 + _TIP_INTERVAL + 1]
        assert maybe_send_feature_tip(str(tmp_path)) is True
        # Without mark_active(), second tip is blocked
        assert maybe_send_feature_tip(str(tmp_path)) is False
        assert mock_outbox.call_count == 1

    reset_tip_throttle()


def test_mark_active_resets_idle_guard(tmp_path):
    """mark_active() allows a new tip in the next idle period."""
    reset_tip_throttle()
    skills = [_make_skill("status"), _make_skill("plan")]
    registry = FakeRegistry(skills)

    with patch("app.skills.build_registry", return_value=registry), \
         patch("app.utils.append_to_outbox") as mock_outbox, \
         patch("app.feature_tips.time") as mock_time:
        mock_time.monotonic.side_effect = [0.0, 0.0 + _TIP_INTERVAL + 1]
        assert maybe_send_feature_tip(str(tmp_path)) is True
        mark_active()
        assert maybe_send_feature_tip(str(tmp_path)) is True
        assert mock_outbox.call_count == 2

    reset_tip_throttle()


# --- Key dev skills set ---

def test_key_dev_skills_contains_expected():
    for skill in ["fix", "plan", "review", "implement", "rebase", "squash"]:
        assert skill in _KEY_DEV_SKILLS
