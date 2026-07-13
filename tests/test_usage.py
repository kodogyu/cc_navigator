import datetime
import time
import json
import pathlib
import tempfile
import unittest
import urllib.error

from ccnav import usage

PAYLOAD = {
    "five_hour": {"utilization": 26.0, "resets_at": "2026-07-13T06:29:59.865612+00:00"},
    "seven_day": {"utilization": 7.0, "resets_at": "2026-07-19T11:59:59.865637+00:00"},
    "limits": [
        {"kind": "session", "group": "session", "percent": 26, "severity": "normal",
         "resets_at": "2026-07-13T06:29:59.865612+00:00", "scope": None},
        {"kind": "weekly_all", "group": "weekly", "percent": 7, "severity": "normal",
         "resets_at": "2026-07-19T11:59:59.865637+00:00", "scope": None},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 3, "severity": "warning",
         "resets_at": "2026-07-19T11:59:59.866004+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}}},
    ],
}


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class CredentialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / ".credentials.json"

    def test_a_missing_file_yields_none(self):
        self.assertIsNone(usage.read_credentials(self.path))

    def test_a_garbage_file_yields_none(self):
        self.path.write_text("{not json")
        self.assertIsNone(usage.read_credentials(self.path))

    def test_a_file_without_the_oauth_block_yields_none(self):
        self.path.write_text(json.dumps({"mcpOAuth": {}}))
        self.assertIsNone(usage.read_credentials(self.path))

    def test_a_valid_file_yields_the_token_and_plan(self):
        self.path.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "sk-tok", "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x"}}))
        creds = usage.read_credentials(self.path)
        self.assertEqual(creds.access_token, "sk-tok")
        self.assertEqual(creds.subscription_type, "max")
        self.assertEqual(creds.rate_limit_tier, "default_claude_max_20x")

    def test_plan_name_renders_max_20x(self):
        creds = usage.Credentials("t", "max", "default_claude_max_20x")
        self.assertEqual(usage.plan_name(creds), "Max 20x")

    def test_plan_name_falls_back_to_the_subscription_type(self):
        self.assertEqual(usage.plan_name(usage.Credentials("t", "pro", "")), "Pro")

    def test_plan_name_of_nothing_is_empty(self):
        self.assertEqual(usage.plan_name(usage.Credentials("t", "", "")), "")


class ParseTest(unittest.TestCase):
    def test_parses_session_weekly_and_scoped_rows_in_order(self):
        entries = usage.parse(PAYLOAD).entries
        self.assertEqual([(e.label, e.percent) for e in entries],
                         [("세션 (5시간)", 26), ("주간 (전체)", 7), ("주간 (Fable)", 3)])

    def test_carries_severity_and_reset(self):
        scoped = usage.parse(PAYLOAD).entries[2]
        self.assertEqual(scoped.severity, "warning")
        self.assertTrue(scoped.resets_at.startswith("2026-07-19"))

    def test_an_unknown_kind_keeps_its_raw_name(self):
        entries = usage.parse({"limits": [{"kind": "monthly_x", "percent": 5}]}).entries
        self.assertEqual(entries[0].label, "monthly_x")

    def test_a_row_without_a_numeric_percent_is_dropped(self):
        entries = usage.parse({"limits": [
            {"kind": "session", "percent": None},
            {"kind": "weekly_all", "percent": "nope"},
            {"kind": "weekly_all", "percent": 9},
        ]}).entries
        self.assertEqual([e.percent for e in entries], [9])

    def test_a_shape_we_do_not_recognise_yields_no_entries(self):
        # The endpoint is undocumented: a changed shape must degrade, not raise.
        for bad in (None, [], "nope", {}, {"limits": "no"}, {"limits": [1, 2]}):
            self.assertEqual(usage.parse(bad).entries, [], repr(bad))


class DescribeResetTest(unittest.TestCase):
    NOW = datetime.datetime(2026, 7, 13, 4, 0, tzinfo=datetime.timezone.utc)

    def test_within_a_day_counts_down_in_hours_and_minutes(self):
        text = usage.describe_reset("2026-07-13T06:29:59+00:00", now=self.NOW)
        self.assertIn("2시간", text)
        self.assertIn("29분", text)
        self.assertIn("리셋", text)

    def test_a_trailing_z_is_accepted(self):
        # Python 3.8's fromisoformat rejects "Z"; the endpoint may send it.
        self.assertIn("2시간", usage.describe_reset("2026-07-13T06:29:59Z", now=self.NOW))

    def test_beyond_a_day_shows_the_date(self):
        text = usage.describe_reset("2026-07-19T11:59:59+00:00", now=self.NOW)
        self.assertIn("7월 19일", text)

    def test_an_unparseable_value_is_silent(self):
        self.assertEqual(usage.describe_reset("soon", now=self.NOW), "")
        self.assertEqual(usage.describe_reset(None, now=self.NOW), "")


class FetchTest(unittest.TestCase):
    def test_a_200_returns_the_payload_and_sends_a_bearer_token(self):
        seen = {}

        def opener(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            return _FakeResponse(PAYLOAD)

        payload, err = usage.fetch("sk-tok", opener=opener)
        self.assertEqual(err, "")
        self.assertEqual(payload["limits"][0]["percent"], 26)
        self.assertIn("/api/oauth/usage", seen["url"])
        self.assertEqual(seen["auth"], "Bearer sk-tok")

    def test_a_401_reports_expired_auth(self):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        payload, err = usage.fetch("sk-tok", opener=opener)
        self.assertIsNone(payload)
        self.assertIn("인증", err)

    def test_a_network_error_reports_it_without_raising(self):
        def opener(req, timeout=None):
            raise urllib.error.URLError("offline")

        payload, err = usage.fetch("sk-tok", opener=opener)
        self.assertIsNone(payload)
        self.assertIn("네트워크", err)

    def test_a_non_json_body_degrades_to_a_message(self):
        class Junk:
            def read(self):
                return b"<html>nope"

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        payload, err = usage.fetch("sk-tok", opener=lambda r, timeout=None: Junk())
        self.assertIsNone(payload)
        self.assertNotEqual(err, "")


class LoadTest(unittest.TestCase):
    def test_no_credentials_reports_a_login_hint(self):
        result, err = usage.load(read=lambda: None, opener=None)
        self.assertIsNone(result)
        self.assertIn("로그인", err)

    def test_a_good_fetch_returns_usage_with_the_plan(self):
        result, err = usage.load(
            read=lambda: usage.Credentials("t", "max", "default_claude_max_20x"),
            opener=lambda req, timeout=None: _FakeResponse(PAYLOAD))
        self.assertEqual(err, "")
        self.assertEqual(result.plan, "Max 20x")
        self.assertEqual(len(result.entries), 3)

    def test_an_unrecognised_shape_names_the_likely_cause(self):
        result, err = usage.load(
            read=lambda: usage.Credentials("t", "max", ""),
            opener=lambda req, timeout=None: _FakeResponse({"whatever": 1}))
        self.assertIsNone(result)
        self.assertIn("형식", err)

    def test_a_fetch_failure_is_passed_through(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("offline")

        result, err = usage.load(read=lambda: usage.Credentials("t", "", ""), opener=boom)
        self.assertIsNone(result)
        self.assertIn("네트워크", err)


class RedirectTokenLeakTest(unittest.TestCase):
    """urllib's redirect handler copies EVERY header except content-length/type onto
    the new request -- Authorization included, with no same-host check (unlike
    requests, which strips it across hosts). So one 302 would hand the account's
    OAuth bearer token to whatever host the redirect names, over plain http if it
    says so. The token must never leave api.anthropic.com: refuse redirects.

    This is proven against real sockets, not mocks: two local HTTP servers, one
    redirecting to the other, and the second records what it was sent.
    """

    def setUp(self):
        import http.server
        import threading

        self.leaked = []
        leaked = self.leaked

        class Sink(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # the redirect target -- records any Authorization
                leaked.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_a):
                pass

        self.sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)
        sink_port = self.sink.server_port
        threading.Thread(target=self.sink.serve_forever, daemon=True).start()
        self.addCleanup(self.sink.shutdown)

        class Redirector(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/stolen" % sink_port)
                self.end_headers()

            def log_message(self, *_a):
                pass

        self.redirector = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        self.url = "http://127.0.0.1:%d/usage" % self.redirector.server_port
        threading.Thread(target=self.redirector.serve_forever, daemon=True).start()
        self.addCleanup(self.redirector.shutdown)

    def test_the_bearer_token_is_never_followed_to_a_redirect_target(self):
        original = usage.USAGE_URL
        usage.USAGE_URL = self.url
        try:
            payload, err = usage.fetch("sk-secret-token")
        finally:
            usage.USAGE_URL = original

        self.assertEqual(self.leaked, [], "the token must not reach the redirect target")
        self.assertIsNone(payload)
        self.assertEqual(err, usage.ERR_NETWORK, "a redirect is just a failed fetch")


class TransientRetryTest(unittest.TestCase):
    """A one-off failure must not become the user's problem. The button used to report
    "네트워크" and the user simply pressed it again and it worked -- so the retry was
    real, it was just being done by a human. Do it once ourselves."""

    def test_a_transient_failure_is_retried_once_and_succeeds(self):
        calls = []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.URLError("connection reset")
            return _FakeResponse(PAYLOAD)

        payload, err = usage.fetch("sk-tok", opener=flaky)
        self.assertEqual(err, "")
        self.assertIsNotNone(payload)
        self.assertEqual(len(calls), 2, "one retry")

    def test_a_persistent_failure_still_reports_after_the_retry(self):
        calls = []

        def dead(req, timeout=None):
            calls.append(1)
            raise urllib.error.URLError("offline")

        payload, err = usage.fetch("sk-tok", opener=dead)
        self.assertIsNone(payload)
        self.assertEqual(err, usage.ERR_NETWORK)
        self.assertEqual(len(calls), 2, "tried twice, then gave up")

    def test_a_401_is_not_retried(self):
        calls = []

        def unauthorized(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        _payload, err = usage.fetch("sk-tok", opener=unauthorized)
        self.assertIn("인증", err)
        self.assertEqual(len(calls), 1, "a rejected token will be rejected again")


class HonestErrorsTest(unittest.TestCase):
    """Every non-401 HTTP failure used to be reported as "(네트워크)" -- a rate limit
    and a 500 both blamed the user's connection. Say what actually happened."""

    def _fetch_status(self, code):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "boom", {}, None)
        return usage.fetch("sk-tok", opener=opener)[1]

    def test_a_rate_limit_says_so(self):
        self.assertIn("잠시", self._fetch_status(429))
        self.assertNotIn("네트워크", self._fetch_status(429))

    def test_a_server_error_says_so_and_names_the_status(self):
        err = self._fetch_status(503)
        self.assertIn("503", err)
        self.assertNotIn("네트워크", err)

    def test_a_genuine_network_failure_still_says_network(self):
        def offline(req, timeout=None):
            raise urllib.error.URLError("offline")
        self.assertEqual(usage.fetch("sk-tok", opener=offline)[1], usage.ERR_NETWORK)


class ExpiredTokenTest(unittest.TestCase):
    """The access token lives ~8h and CLAUDE CODE refreshes it, not us. Sending a token
    we can see is expired just buys a 401 whose message ("다시 로그인하세요") is wrong:
    the user does not need to log in again, they need Claude Code to refresh it -- which
    happens on its own the next time a session runs. Check the clock before the call."""

    def _creds(self, expires_at_ms):
        return usage.Credentials("sk-tok", "max", "default_claude_max_20x", expires_at_ms)

    def test_an_expired_token_is_not_sent_and_the_message_is_actionable(self):
        called = []

        def opener(req, timeout=None):
            called.append(1)
            return _FakeResponse(PAYLOAD)

        past = int((time.time() - 60) * 1000)
        result, err = usage.load(read=lambda: self._creds(past), opener=opener)
        self.assertIsNone(result)
        self.assertEqual(called, [], "do not spend a request on a token we know is dead")
        self.assertIn("갱신", err, "tell them what actually fixes it")
        self.assertNotIn("네트워크", err)

    def test_a_valid_token_is_used(self):
        future = int((time.time() + 3600) * 1000)
        result, err = usage.load(read=lambda: self._creds(future),
                                 opener=lambda req, timeout=None: _FakeResponse(PAYLOAD))
        self.assertEqual(err, "")
        self.assertEqual(result.plan, "Max 20x")

    def test_a_credentials_file_with_no_expiry_is_still_tried(self):
        # Never let a missing field become a hard block -- send it and let the server decide.
        result, err = usage.load(read=lambda: self._creds(0),
                                 opener=lambda req, timeout=None: _FakeResponse(PAYLOAD))
        self.assertEqual(err, "")
        self.assertIsNotNone(result)
