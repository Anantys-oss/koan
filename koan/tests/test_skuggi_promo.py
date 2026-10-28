"""Tests for app.skuggi_promo — the Skuggi touchpoints and their opt-out."""

from unittest.mock import patch

import pytest

from app import skuggi_promo


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(skuggi_promo, "_send", messages.append)
    return messages


def _config(cfg):
    return patch("app.utils.load_config", return_value=cfg)


class TestPromoEnabled:
    def test_on_when_key_absent(self):
        with _config({}):
            assert skuggi_promo.promo_enabled() is True

    def test_on_when_section_has_no_promo_key(self):
        with _config({"skuggi": {}}):
            assert skuggi_promo.promo_enabled() is True

    def test_on_when_explicitly_true(self):
        with _config({"skuggi": {"promo": True}}):
            assert skuggi_promo.promo_enabled() is True

    def test_off_only_when_explicitly_false(self):
        with _config({"skuggi": {"promo": False}}):
            assert skuggi_promo.promo_enabled() is False

    def test_on_when_config_unreadable(self):
        with patch("app.utils.load_config", side_effect=OSError("boom")):
            assert skuggi_promo.promo_enabled() is True


class TestLaunchNotice:
    def test_sent_once(self, tmp_path, sent):
        with _config({}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert len(sent) == 1
        assert "utm_content=launch" in sent[0]

    def test_failed_send_retries_next_boot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skuggi_promo, "_send", lambda m: (_ for _ in ()).throw(RuntimeError()))
        with _config({}), pytest.raises(RuntimeError):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert skuggi_promo._read_state(str(tmp_path)) == {}

    def test_silent_when_opted_out(self, tmp_path, sent):
        with _config({"skuggi": {"promo": False}}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert sent == []


class TestNudges:
    def test_cooldown_per_trigger(self, tmp_path, sent):
        with _config({}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is True
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is False
            assert skuggi_promo.maybe_nudge(str(tmp_path), "pr") is True
        assert len(sent) == 2

    def test_fires_again_after_cooldown(self, tmp_path, sent):
        with _config({}):
            skuggi_promo.maybe_nudge(str(tmp_path), "quota")
            later = skuggi_promo.time.time() + skuggi_promo.NUDGE_COOLDOWN_SECONDS + 1
            with patch.object(skuggi_promo.time, "time", return_value=later):
                assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is True

    def test_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skuggi_promo, "_send", lambda m: (_ for _ in ()).throw(RuntimeError()))
        with _config({}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is False

    def test_unknown_trigger_ignored(self, tmp_path, sent):
        with _config({}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "nope") is False
        assert sent == []

    def test_silent_when_opted_out(self, tmp_path, sent):
        with _config({"skuggi": {"promo": False}}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is False
        assert sent == []

    @pytest.mark.parametrize("exit_code,url,expected", [
        (1, "", "utm_content=failure"),
        (0, "https://github.com/o/r/pull/7", "utm_content=pr"),
    ])
    def test_after_mission_picks_trigger(self, tmp_path, sent, exit_code, url, expected):
        with _config({}):
            assert skuggi_promo.maybe_nudge_after_mission(str(tmp_path), exit_code, url)
        assert expected in sent[0]

    def test_after_mission_without_pr_stays_quiet(self, tmp_path, sent):
        with _config({}):
            assert skuggi_promo.maybe_nudge_after_mission(str(tmp_path), 0, "") is False
        assert sent == []

    def test_fleet_threshold(self, tmp_path, sent):
        threshold = skuggi_promo.FLEET_PROJECT_THRESHOLD
        with _config({}):
            assert skuggi_promo.maybe_nudge_fleet(str(tmp_path), threshold) is False
            assert skuggi_promo.maybe_nudge_fleet(str(tmp_path), threshold + 1) is True
        assert str(threshold + 1) in sent[0]


class TestFooter:
    @pytest.mark.parametrize("medium", ["report", "help", "status", "banner"])
    def test_carries_tracked_link(self, medium):
        with _config({}):
            assert f"utm_medium={medium}" in skuggi_promo.footer(medium)

    def test_empty_when_opted_out(self):
        with _config({"skuggi": {"promo": False}}):
            assert skuggi_promo.footer("help") == ""


def test_config_validator_accepts_skuggi_section():
    from app.config_validator import validate_config
    assert validate_config({"skuggi": {"promo": False}}) == []
    assert validate_config({"skuggi": {"promo": "nope"}}) != []
