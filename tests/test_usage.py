import datetime
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
