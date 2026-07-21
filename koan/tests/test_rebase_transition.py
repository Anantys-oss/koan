"""Tests for the /rebase transition-notice helper (rebase_transition)."""

from datetime import timedelta

from app import rebase_transition


class TestNoticeActive:
    def test_active_before_deadline(self):
        before = rebase_transition.FIX_NOTICE_DEADLINE - timedelta(days=1)
        assert rebase_transition.notice_active(before) is True

    def test_inactive_after_deadline(self):
        after = rebase_transition.FIX_NOTICE_DEADLINE + timedelta(days=1)
        assert rebase_transition.notice_active(after) is False

    def test_inactive_at_deadline(self):
        # The window is half-open: the deadline instant is already closed.
        assert rebase_transition.notice_active(rebase_transition.FIX_NOTICE_DEADLINE) is False


class TestNoticeText:
    def test_chat_notice_mentions_fix(self):
        text = rebase_transition.chat_notice()
        assert "`/fix`" in text
        assert "--fix" in text
        assert "Heads up" in text

    def test_chat_notice_advertises_fix_before_rebase_fix(self):
        # /fix is the simpler, preferred form; --fix is the alternative.
        text = rebase_transition.chat_notice()
        assert text.index("`/fix`") < text.index("`/rebase --fix`")

    def test_pr_comment_notice_is_note_alert(self):
        text = rebase_transition.pr_comment_notice()
        assert text.startswith("> [!NOTE]")
        assert "`/fix`" in text
        assert "--fix" in text
