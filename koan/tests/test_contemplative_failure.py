"""Tests for contemplative-session failure detection and messaging.

Covers the fix for silent contemplative failures: when the provider gateway
returns a transient error (e.g. an HTTP 529 overload), the contemplative path
now surfaces a clear, throttled message instead of letting the agent emit
generic "Run failed / went sideways" text.
"""

from unittest.mock import patch

import pytest

from app.run import (
    _classify_contemplative_failure,
    _notify_contemplative_failure,
)


# A gateway 529 overload result payload (generic, trimmed).
GATEWAY_529_STDOUT = (
    '{"type":"result","subtype":"success","is_error":true,'
    '"api_error_status":529,"result":"API Error: 529 [1305]'
    "[The service may be temporarily overloaded, please try again later]."
    '","total_cost_usd":0,"usage":{"input_tokens":0,"output_tokens":0}}'
)


class TestClassifyContemplativeFailure:
    def test_success_exit0_no_error(self):
        ok = '{"is_error": false, "result": "a genuine reflection"}'
        failed, sig, reason = _classify_contemplative_failure(0, ok, "")
        assert failed is False
        assert sig == ""
        assert reason == ""

    def test_tool_result_error_does_not_false_trigger(self):
        # A failed tool call inside an otherwise-successful stream-json session
        # carries is_error:true on the tool_result, not the session. Must not
        # be mistaken for a contemplative failure.
        stream = (
            '{"type":"user","message":{"content":[{"type":"tool_result",'
            '"is_error":true,"content":"Command failed"}]}}\n'
            '{"type":"result","subtype":"success","is_error":false,'
            '"result":"a genuine reflection"}'
        )
        failed, sig, reason = _classify_contemplative_failure(0, stream, "")
        assert failed is False

    def test_gateway_529_nonzero_exit(self):
        failed, sig, reason = _classify_contemplative_failure(1, GATEWAY_529_STDOUT, "")
        assert failed is True
        assert sig == "overload:529"
        assert "529" in reason

    def test_gateway_529_false_success_exit0(self):
        # CLI exits 0 but the result JSON carries is_error:true + api_error_status.
        failed, sig, reason = _classify_contemplative_failure(0, GATEWAY_529_STDOUT, "")
        assert failed is True
        assert sig == "overload:529"

    def test_quota(self):
        stderr = "Error: out of extra usage quota. resets 10am."
        failed, sig, reason = _classify_contemplative_failure(1, "", stderr)
        assert failed is True
        assert sig == "quota"

    def test_auth(self):
        stderr = "Error: oauth token has expired, please run /login"
        failed, sig, reason = _classify_contemplative_failure(1, "", stderr)
        assert failed is True
        assert sig == "auth"

    def test_generic_nonzero_exit(self):
        failed, sig, reason = _classify_contemplative_failure(42, "odd output", "")
        assert failed is True
        assert sig.startswith("exit:")
        assert "42" in reason


class TestNotifyContemplativeFailure:
    def _write(self, tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text)
        return str(path)

    def test_overload_notifies_once(self, tmp_path):
        instance = str(tmp_path)
        out = self._write(tmp_path, "out.txt", GATEWAY_529_STDOUT)
        with patch("app.run._notify") as mock_notify:
            _notify_contemplative_failure(instance, "ios", 1, out, "")
            assert mock_notify.called
            msg = mock_notify.call_args[0][1]
            assert "529" in msg
            assert "overloaded" in msg
        # Tracker persisted.
        assert (tmp_path / ".contemplative-failure-notify.json").exists()

    def test_same_signature_within_cooldown_no_resend(self, tmp_path):
        instance = str(tmp_path)
        out = self._write(tmp_path, "out.txt", GATEWAY_529_STDOUT)
        with patch("app.run._notify") as mock_notify:
            _notify_contemplative_failure(instance, "ios", 1, out, "")
            _notify_contemplative_failure(instance, "ios", 1, out, "")
            assert mock_notify.call_count == 1

    def test_different_signature_renotifies(self, tmp_path):
        instance = str(tmp_path)
        out = self._write(tmp_path, "out.txt", GATEWAY_529_STDOUT)
        quota_out = self._write(tmp_path, "quota.txt", "")
        quota_err = self._write(tmp_path, "quota.err", "out of extra usage quota")
        with patch("app.run._notify") as mock_notify:
            _notify_contemplative_failure(instance, "ios", 1, out, "")
            _notify_contemplative_failure(instance, "ios", 1, quota_out, quota_err)
            assert mock_notify.call_count == 2

    def test_success_clears_tracker(self, tmp_path):
        instance = str(tmp_path)
        fail_out = self._write(tmp_path, "fail.txt", GATEWAY_529_STDOUT)
        ok_out = self._write(tmp_path, "ok.txt", '{"is_error": false}')
        with patch("app.run._notify"):
            _notify_contemplative_failure(instance, "ios", 1, fail_out, "")
            assert (tmp_path / ".contemplative-failure-notify.json").exists()
            # Recovery -> tracker cleared, no notification.
            _notify_contemplative_failure(instance, "ios", 0, ok_out, "")
            assert not (tmp_path / ".contemplative-failure-notify.json").exists()

    def test_success_does_not_notify(self, tmp_path):
        instance = str(tmp_path)
        ok = self._write(tmp_path, "ok.txt", '{"is_error": false}')
        with patch("app.run._notify") as mock_notify:
            _notify_contemplative_failure(instance, "ios", 0, ok, "")
            assert mock_notify.called is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
