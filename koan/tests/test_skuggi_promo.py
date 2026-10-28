"""Tests for app.skuggi_promo — the Skuggi touchpoints and their opt-out."""

from unittest.mock import patch

import pytest

from app import skuggi_promo


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def _record(message):
        messages.append(message)
        return True

    monkeypatch.setattr(skuggi_promo, "_send", _record)
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

    def test_off_when_config_unreadable(self):
        with patch("app.utils.load_config", side_effect=OSError("boom")):
            assert skuggi_promo.promo_enabled() is False

    @pytest.mark.parametrize("value", ["false", 0, "off", None])
    def test_off_when_value_not_true(self, value):
        with _config({"skuggi": {"promo": value}}):
            assert skuggi_promo.promo_enabled() is False


class TestLaunchNotice:
    def test_sent_once(self, tmp_path, sent):
        with _config({}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert len(sent) == 1
        assert "utm_content=launch" in sent[0]

    def test_failed_send_retries_next_boot(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skuggi_promo, "_send", lambda m: (_ for _ in ()).throw(RuntimeError()))
        with _config({}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert skuggi_promo._read_state(str(tmp_path)) == {}

    def test_suppressed_send_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skuggi_promo, "_send", lambda m: "suppressed")
        with _config({}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert "launch" in skuggi_promo._read_state(str(tmp_path))

    def test_state_write_failure_does_not_raise(self, tmp_path, sent, monkeypatch):
        monkeypatch.setattr(
            skuggi_promo, "_write_state", lambda *a: (_ for _ in ()).throw(OSError("ro")))
        with _config({}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert len(sent) == 1

    def test_silent_when_opted_out(self, tmp_path, sent):
        with _config({"skuggi": {"promo": False}}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert sent == []

    def test_undelivered_send_not_recorded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skuggi_promo, "_send", lambda m: False)
        with _config({}):
            skuggi_promo.maybe_send_launch_notice(str(tmp_path))
        assert skuggi_promo._read_state(str(tmp_path)) == {}


class TestSendVerbatim:
    @pytest.mark.parametrize("result,expected", [
        (True, True),
        (False, False),
        ("suppressed", False),
    ])
    def test_only_real_delivery_counts(self, result, expected):
        with patch("app.notify.send_telegram", return_value=result):
            assert skuggi_promo.send_verbatim("hi") is expected


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

    def test_undelivered_nudge_keeps_cooldown_unarmed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skuggi_promo, "_send", lambda m: False)
        with _config({}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is False
        assert skuggi_promo._read_state(str(tmp_path)) == {}

    def test_suppressed_nudge_arms_cooldown(self, tmp_path, monkeypatch):
        calls = []

        def _suppressed(message):
            calls.append(message)
            return "suppressed"

        monkeypatch.setattr(skuggi_promo, "_send", _suppressed)
        with _config({}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is False
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is False
        assert len(calls) == 1

    def test_sent_nudge_survives_state_write_failure(self, tmp_path, sent, monkeypatch):
        monkeypatch.setattr(
            skuggi_promo, "_write_state", lambda *a: (_ for _ in ()).throw(OSError("ro")))
        with _config({}):
            assert skuggi_promo.maybe_nudge(str(tmp_path), "quota") is True

    def test_corrupt_state_treated_as_empty(self, tmp_path):
        (tmp_path / ".skuggi-promo.json").write_text("{not json")
        assert skuggi_promo._read_state(str(tmp_path)) == {}

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


class TestRunWiring:
    def test_no_nudge_after_suppressed_autonomous_run(self, tmp_path):
        from app import run
        with patch.object(run, "_notify_mission_outcome", return_value=False), \
                patch.object(skuggi_promo, "maybe_nudge_after_mission") as nudge:
            run._notify_mission_end(str(tmp_path), "p", 1, 5, 0, "", "https://x/pull/1")
        nudge.assert_not_called()

    def test_nudge_after_sent_outcome(self, tmp_path):
        from app import run
        with patch.object(run, "_notify_mission_outcome", return_value=True), \
                patch.object(skuggi_promo, "maybe_nudge_after_mission") as nudge:
            run._notify_mission_end(str(tmp_path), "p", 1, 5, 0, "T", "https://x/pull/1")
        nudge.assert_called_once()


class TestReportFooter:
    def test_footer_sent_verbatim_after_report(self):
        from app import daily_report
        with patch.object(daily_report, "generate_report", return_value="REPORT"), \
                patch.object(daily_report, "format_and_send", return_value=True) as fmt, \
                patch.object(daily_report, "mark_report_sent"), \
                patch.object(skuggi_promo, "footer", return_value="FOOTER"), \
                patch.object(skuggi_promo, "send_verbatim") as verbatim:
            assert daily_report.send_daily_report("morning") is True
        fmt.assert_called_once_with("REPORT")
        verbatim.assert_called_once_with("FOOTER")


def test_config_validator_accepts_skuggi_section():
    from app.config_validator import validate_config
    assert validate_config({"skuggi": {"promo": False}}) == []
    assert validate_config({"skuggi": {"promo": "nope"}}) != []
