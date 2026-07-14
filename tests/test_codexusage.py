import unittest

from ccnav import codexusage


RATE_LIMITS = {
    "rateLimits": {
        "limitId": "codex",
        "limitName": None,
        "primary": {
            "usedPercent": 26,
            "windowDurationMins": 300,
            "resetsAt": 1784000000,
        },
        "secondary": {
            "usedPercent": 7,
            "windowDurationMins": 10080,
            "resetsAt": 1784600000,
        },
        "planType": "plus",
    }
}


class ParseTest(unittest.TestCase):
    def test_parses_plan_primary_secondary_and_reset(self):
        result = codexusage.parse(RATE_LIMITS)
        self.assertEqual(result.plan, "Plus")
        self.assertEqual([entry.label for entry in result.entries], [
            "세션 (5시간)", "주간",
        ])
        self.assertEqual([entry.percent for entry in result.entries], [26, 7])
        self.assertTrue(result.entries[0].resets_at.endswith("+00:00"))

    def test_uses_the_multi_bucket_view_without_duplicating_legacy(self):
        payload = dict(RATE_LIMITS, rateLimitsByLimitId={
            "codex": RATE_LIMITS["rateLimits"],
            "spark": {
                "limitName": "Spark",
                "planType": "plus",
                "primary": {"usedPercent": 80, "windowDurationMins": 1440},
                "secondary": None,
            },
        })
        result = codexusage.parse(payload)
        self.assertEqual(len(result.entries), 3)
        self.assertEqual(result.entries[-1].label, "Spark · 일간")
        self.assertEqual(result.entries[-1].severity, "warning")

    def test_unknown_or_partial_shape_is_empty_not_an_exception(self):
        for payload in (None, [], {}, {"rateLimits": {"primary": {}}}):
            self.assertEqual(codexusage.parse(payload).entries, [])

    def test_arbitrary_window_duration_gets_an_honest_label(self):
        payload = {"rateLimits": {
            "planType": "team",
            "primary": {"usedPercent": 3, "windowDurationMins": 120},
        }}
        self.assertEqual(codexusage.parse(payload).entries[0].label, "2시간 한도")


class LoadTest(unittest.TestCase):
    def test_calls_the_local_app_server_and_returns_usage(self):
        seen = {}

        def request(argv, messages, response_id, timeout=None, ready_id=None):
            seen["argv"] = argv
            seen["messages"] = messages
            seen["response_id"] = response_id
            seen["ready_id"] = ready_id
            return 0, {"id": 2, "result": RATE_LIMITS}

        result, error = codexusage.load(request=request)
        self.assertEqual(error, "")
        self.assertEqual(result.plan, "Plus")
        self.assertEqual(seen["argv"], ["codex", "app-server", "--stdio"])
        self.assertEqual(seen["response_id"], 2)
        self.assertEqual(seen["ready_id"], 1)
        self.assertEqual(seen["messages"][-1]["method"], "account/rateLimits/read")

    def test_missing_binary_or_timeout_is_a_plain_error(self):
        result, error = codexusage.load(
            request=lambda *_a, **_k: (127, None))
        self.assertIsNone(result)
        self.assertIn("Codex", error)

    def test_auth_error_has_actionable_login_text(self):
        response = {"id": 2, "error": {"message": "Not authenticated"}}
        result, error = codexusage.load(
            request=lambda *_a, **_k: (0, response))
        self.assertIsNone(result)
        self.assertIn("로그인", error)

    def test_changed_response_shape_is_reported(self):
        result, error = codexusage.load(
            request=lambda *_a, **_k: (0, {"id": 2, "result": {"new": "shape"}}))
        self.assertIsNone(result)
        self.assertIn("형식", error)
