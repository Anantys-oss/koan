"""Tests for review on PR events (app.github_pr_event_review)."""

import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from app.github_notification_tracker import (
    _TRACKER_FILE_PR_EVENT_SEED,
    clear_review_cooldown,
    get_pr_event_seeded_at,
    set_review_cooldown,
    track_thread,
)
from app.github_pr_event_review import scan_pr_event_review_missions
from app.skills import Skill, SkillCommand, SkillRegistry

from tests.store_helpers import seed_missions

EMPTY = "# Missions\n\n## Pending\n\n## In Progress\n\n## Done\n"
PR42 = "https://github.com/sukria/koan/pull/42"
FETCH = "app.github_pr_event_review._fetch_open_prs"


def _pr(number=42, sha="sha-aaaa", *, draft=False, login="alice",
        branch="feature/x", labels=(), updated_ago=60):
    updated = datetime.now(timezone.utc) - timedelta(seconds=updated_ago)
    return {
        "number": number,
        "url": f"https://github.com/sukria/koan/pull/{number}",
        "headRefOid": sha,
        "headRefName": branch,
        "isDraft": draft,
        "author": {"login": login, "is_bot": False},
        "labels": [{"name": name} for name in labels],
        "updatedAt": updated.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _projects(**review):
    return {"projects": {"koan": {
        "path": "/workspace/koan",
        "github_url": "sukria/koan",
        "github": {"pr_event_review": {"enabled": True, **review}},
    }}}


def _config(interval=0, max_age=24):
    return {"github": {
        "nickname": "koan-bot",
        "authorized_users": ["alice"],
        "parallel_workers": 1,
        "max_age_hours": max_age,
        "pr_event_review": {"scan_interval_minutes": interval},
    }}


@pytest.fixture
def registry():
    reg = SkillRegistry()
    reg._register(Skill(
        name="review", scope="core", description="Review PR",
        github_enabled=True, github_context_aware=True,
        commands=[SkillCommand(name="review", aliases=["rv"])],
    ))
    return reg


@pytest.fixture
def inst(tmp_path, monkeypatch):
    monkeypatch.setenv("KOAN_ROOT", str(tmp_path))
    instance = tmp_path / "instance"
    instance.mkdir()
    seed_missions(instance, EMPTY)
    return instance


@pytest.fixture(autouse=True)
def _fixed_prefix_and_label():
    with patch("app.config.get_branch_prefix", return_value="koan/"), \
         patch("app.config.get_review_pause_label", return_value="PauseReview"):
        yield


def _seed(inst, ago=3600):
    (inst / _TRACKER_FILE_PR_EVENT_SEED).write_text(
        json.dumps({"sukria/koan": time.time() - ago}))


def _scan(inst, registry, prs, *, projects=None, config=None, force=False):
    with patch(FETCH, return_value=prs) as fetch:
        queued = scan_pr_event_review_missions(
            projects or _projects(), config or _config(), registry,
            str(inst), force=force,
        )
    return queued, fetch


def _reviews(inst):
    return (inst / "missions.md").read_text().count("/review ")


def _finish_review(inst):
    """Simulate the queued review running to completion."""
    seed_missions(inst, EMPTY)
    clear_review_cooldown(str(inst), "sukria", "koan", "42")


class TestPrEventReviewScan:
    def test_queues_review_for_new_pr(self, inst, registry):
        _seed(inst)
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 1
        assert f"[project:koan] /review {PR42}" in (inst / "missions.md").read_text()

    def test_first_scan_only_seeds(self, inst, registry):
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 0
        assert _reviews(inst) == 0
        assert get_pr_event_seeded_at(str(inst), "sukria/koan") is not None

    def test_activity_on_pre_seed_head_not_reviewed(self, inst, registry):
        _scan(inst, registry, [_pr(updated_ago=7200)])
        _seed(inst)  # backdate the seed: the bump below is clearly after it
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 0
        assert _reviews(inst) == 0

    def test_push_after_seed_reviews_pre_seed_pr(self, inst, registry):
        _scan(inst, registry, [_pr(sha="sha-1", updated_ago=7200)])
        _seed(inst)
        queued, _ = _scan(inst, registry, [_pr(sha="sha-2")])
        assert queued == 1

    def test_draft_at_seed_reviewed_when_marked_ready(self, inst, registry):
        _scan(inst, registry, [_pr(draft=True, updated_ago=7200)])
        _seed(inst)
        queued, _ = _scan(inst, registry, [_pr(draft=False)])
        assert queued == 1

    def test_paused_at_seed_reviewed_when_unpaused(self, inst, registry):
        _scan(inst, registry, [_pr(labels=("PauseReview",), updated_ago=7200)])
        _seed(inst)
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 1

    def test_same_sha_not_requeued(self, inst, registry):
        _seed(inst)
        _scan(inst, registry, [_pr()])
        _finish_review(inst)
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 0

    def test_new_sha_requeues_with_on_push(self, inst, registry):
        _seed(inst)
        _scan(inst, registry, [_pr(sha="sha-1")])
        _finish_review(inst)
        queued, _ = _scan(inst, registry, [_pr(sha="sha-2")])
        assert queued == 1

    def test_new_sha_ignored_without_on_push(self, inst, registry):
        _seed(inst)
        projects = _projects(on_push=False)
        _scan(inst, registry, [_pr(sha="sha-1")], projects=projects)
        _finish_review(inst)
        queued, _ = _scan(inst, registry, [_pr(sha="sha-2")], projects=projects)
        assert queued == 0

    def test_cooldown_defers_without_tracking(self, inst, registry):
        _seed(inst)
        set_review_cooldown(str(inst), "sukria", "koan", "42")
        first, _ = _scan(inst, registry, [_pr()])
        clear_review_cooldown(str(inst), "sukria", "koan", "42")
        second, _ = _scan(inst, registry, [_pr()])
        assert (first, second) == (0, 1)

    def test_draft_skipped_unless_include_drafts(self, inst, registry):
        _seed(inst)
        skipped, _ = _scan(inst, registry, [_pr(draft=True)])
        included, _ = _scan(inst, registry, [_pr(draft=True)],
                            projects=_projects(include_drafts=True))
        assert (skipped, included) == (0, 1)

    def test_pause_label_skipped(self, inst, registry):
        _seed(inst)
        queued, _ = _scan(inst, registry, [_pr(labels=("PauseReview",))])
        assert queued == 0

    def test_bot_and_koan_branch_prs_skipped(self, inst, registry):
        _seed(inst)
        prs = [_pr(number=1, login="koan-bot"), _pr(number=2, branch="koan/fix-x")]
        queued, _ = _scan(inst, registry, prs)
        assert queued == 0

    def test_unauthorized_author_skipped(self, inst, registry):
        _seed(inst)
        queued, _ = _scan(inst, registry, [_pr(login="mallory")])
        assert queued == 0

    def test_pr_older_than_max_age_skipped(self, inst, registry):
        _seed(inst, ago=3 * 86400)
        queued, _ = _scan(inst, registry, [_pr(updated_ago=2 * 86400)])
        assert queued == 0

    def test_active_mission_blocks_duplicate(self, inst, registry):
        _seed(inst)
        seed_missions(inst, EMPTY.replace(
            "## Pending\n", f"## Pending\n- [project:koan] /review {PR42} 📬\n"))
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 0
        assert _reviews(inst) == 1

    def test_max_per_cycle_caps_queue(self, inst, registry):
        _seed(inst)
        prs = [_pr(number=n, sha=f"sha-{n}") for n in (1, 2, 3)]
        projects = _projects(max_per_cycle=2)
        first, _ = _scan(inst, registry, prs, projects=projects)
        second, _ = _scan(inst, registry, prs, projects=projects)
        assert (first, second) == (2, 1)

    def test_fetch_failure_retries_next_poll(self, inst, registry):
        _seed(inst)
        config = _config(interval=60)
        failed, _ = _scan(inst, registry, None, config=config)
        queued, fetch = _scan(inst, registry, [_pr()], config=config)
        assert (failed, queued) == (0, 1)
        fetch.assert_called_once()

    def test_force_bypasses_scan_throttle(self, inst, registry):
        _seed(inst)
        config = _config(interval=60)
        _scan(inst, registry, [], config=config)
        _, throttled = _scan(inst, registry, [_pr()], config=config)
        queued, forced = _scan(inst, registry, [_pr()], config=config, force=True)
        throttled.assert_not_called()
        forced.assert_called_once()
        assert queued == 1

    def test_disabled_project_not_fetched(self, inst, registry):
        _, fetch = _scan(inst, registry, [_pr()], projects=_projects(enabled=False))
        fetch.assert_not_called()

    def test_head_queued_by_requested_review_scan_not_requeued(self, inst, registry):
        _seed(inst)
        track_thread(str(inst), "review_scan:sukria/koan#42:sha-aaaa")
        set_review_cooldown(str(inst), "sukria", "koan", "42")
        during, _ = _scan(inst, registry, [_pr()])
        clear_review_cooldown(str(inst), "sukria", "koan", "42")
        after, _ = _scan(inst, registry, [_pr()])
        assert (during, after) == (0, 0)

    def test_reenable_after_disable_reseeds(self, inst, registry):
        _seed(inst)
        _scan(inst, registry, [_pr()], projects=_projects(enabled=False))
        queued, _ = _scan(inst, registry, [_pr()])
        assert queued == 0
        assert get_pr_event_seeded_at(str(inst), "sukria/koan") > time.time() - 60
