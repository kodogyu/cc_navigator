"""Tests for usage: weekly % from the Claude API and token $ from ccusage."""
import datetime
import json
import unittest

from ccnav import usage


# ---- ccusage token-dollar side ----------------------------------------------

class WeekStartTest(unittest.TestCase):
    def test_monday_is_its_own_week_start(self):
        mon = datetime.date(2026, 7, 13)  # a Monday
        self.assertEqual(usage.week_start(mon), mon)

    def test_midweek_maps_back_to_monday(self):
        self.assertEqual(
            usage.week_start(datetime.date(2026, 7, 14)), datetime.date(2026, 7, 13))

    def test_sunday_maps_to_the_monday_that_began_the_week(self):
        self.assertEqual(
            usage.week_start(datetime.date(2026, 7, 19)), datetime.date(2026, 7, 13))


class SumSinceMondayTest(unittest.TestCase):
    def setUp(self):
        self.today = datetime.date(2026, 7, 14)  # Tuesday; Monday = 07-13
        self.daily = [
            {"date": "2026-07-12", "totalCost": 99.0},   # last Sun -> excluded
            {"date": "2026-07-13", "totalCost": 110.81}, # Mon -> in
            {"date": "2026-07-14", "totalCost": 57.50},  # Tue -> in
        ]

    def test_sums_only_this_week(self):
        self.assertAlmostEqual(usage.sum_since_monday(self.daily, self.today), 168.31, places=2)

    def test_unparseable_rows_are_skipped(self):
        rows = self.daily + [{"date": "x", "totalCost": 1000}, {"date": "2026-07-14", "totalCost": None}]
        self.assertAlmostEqual(usage.sum_since_monday(rows, self.today), 168.31, places=2)


class ParseDailyTest(unittest.TestCase):
    def test_extracts_the_daily_array(self):
        text = json.dumps({"daily": [{"date": "2026-07-13", "totalCost": 1}], "totals": {}})
        self.assertEqual(usage.parse_daily(text), [{"date": "2026-07-13", "totalCost": 1}])

    def test_bad_json_or_shape_is_none(self):
        self.assertIsNone(usage.parse_daily("not json"))
        self.assertIsNone(usage.parse_daily(json.dumps({"weekly": []})))


class TokenCostTest(unittest.TestCase):
    def test_sums_this_weeks_days(self):
        run = lambda: json.dumps({"daily": [
            {"date": "2026-07-13", "totalCost": 110.81},
            {"date": "2026-07-14", "totalCost": 57.50}]})
        cost = usage.token_cost_this_week(today=datetime.date(2026, 7, 14), run=run)
        self.assertAlmostEqual(cost, 168.31, places=2)

    def test_none_when_ccusage_unavailable_or_garbage(self):
        self.assertIsNone(usage.token_cost_this_week(run=lambda: None))
        self.assertIsNone(usage.token_cost_this_week(run=lambda: "boom"))


# ---- Claude API weekly-percent side -----------------------------------------

_USAGE_PAYLOAD = json.dumps({
    "five_hour": {"utilization": 20.0},
    "seven_day": {"utilization": 24.0, "resets_at": "2026-07-19T11:59:59Z"},
})


class ParseWeeklyPercentTest(unittest.TestCase):
    def test_reads_seven_day_utilization(self):
        self.assertEqual(usage.parse_weekly_percent(_USAGE_PAYLOAD), 24.0)

    def test_bad_or_missing_shape_is_none(self):
        self.assertIsNone(usage.parse_weekly_percent("not json"))
        self.assertIsNone(usage.parse_weekly_percent(json.dumps({})))
        self.assertIsNone(usage.parse_weekly_percent(json.dumps({"seven_day": {}})))


class FetchWeeklyPercentTest(unittest.TestCase):
    def test_returns_percent_with_a_token_and_a_reachable_endpoint(self):
        pct = usage.fetch_weekly_percent(
            token_reader=lambda: "tok", http_get=lambda t: _USAGE_PAYLOAD)
        self.assertEqual(pct, 24.0)

    def test_no_token_means_none_and_no_request(self):
        called = []
        pct = usage.fetch_weekly_percent(
            token_reader=lambda: None, http_get=lambda t: called.append(t) or "")
        self.assertIsNone(pct)
        self.assertEqual(called, [], "must not call the API without a token")

    def test_network_error_degrades_to_none(self):
        def boom(_t):
            raise OSError("offline")
        self.assertIsNone(usage.fetch_weekly_percent(token_reader=lambda: "tok", http_get=boom))


# ---- combined snapshot -------------------------------------------------------

class FetchUsageTest(unittest.TestCase):
    def test_combines_both_readings(self):
        snap = usage.fetch_usage(
            budget=1315.0, today=datetime.date(2026, 7, 14),
            weekly=lambda: 24.0,
            token_cost=lambda today=None: 168.31,
        )
        self.assertEqual(snap.weekly_percent, 24.0)
        self.assertAlmostEqual(snap.token_cost, 168.31, places=2)
        self.assertAlmostEqual(snap.token_percent, 168.31 / 1315.0 * 100.0, places=2)
        self.assertFalse(snap.empty)

    def test_each_side_is_independent(self):
        # API up, ccusage down
        snap = usage.fetch_usage(weekly=lambda: 24.0, token_cost=lambda today=None: None)
        self.assertEqual(snap.weekly_percent, 24.0)
        self.assertIsNone(snap.token_percent)
        # API down, ccusage up
        snap = usage.fetch_usage(
            today=datetime.date(2026, 7, 14),
            weekly=lambda: None, token_cost=lambda today=None: 100.0)
        self.assertIsNone(snap.weekly_percent)
        self.assertIsNotNone(snap.token_percent)

    def test_empty_when_both_fail(self):
        snap = usage.fetch_usage(weekly=lambda: None, token_cost=lambda today=None: None)
        self.assertTrue(snap.empty)


if __name__ == "__main__":
    unittest.main()
