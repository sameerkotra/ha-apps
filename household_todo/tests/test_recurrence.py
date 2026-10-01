import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import recurrence as r  # noqa: E402

D = date.fromisoformat


class ValidateRule(unittest.TestCase):
    def test_valid(self):
        for rule, anchor in [
            ("daily", "2026-09-21"),
            ("weeks:1:1", "2026-09-21"),          # a Monday
            ("weeks:2:1", "2026-09-21"),
            ("weeks:1:1,4", "2026-09-24"),         # a Thursday
            ("every:14", "2026-09-22"),
            ("monthly:31", "2026-01-01"),
            ("nth:-1:5", "2026-01-01"),
            ("nth:2:2", "2026-01-01"),
        ]:
            self.assertIsNone(r.validate_rule(rule, anchor), (rule, anchor))

    def test_invalid_rule(self):
        for rule in ["", "weekly", "weeks:0:1", "weeks:53:1", "weeks:1:8", "weeks:1:4,1", "weeks:1:1,1",
                     "every:0", "every:1000", "monthly:0", "monthly:32", "nth:5:1", "nth:0:1", "nth:1:0", None, 3]:
            self.assertIsNotNone(r.validate_rule(rule, "2026-09-21"), rule)

    def test_anchor_must_be_valid_date(self):
        self.assertIsNotNone(r.validate_rule("daily", "2026-02-30"))
        self.assertIsNotNone(r.validate_rule("daily", "20260921"))
        self.assertIsNotNone(r.validate_rule("daily", None))

    def test_weeks_anchor_must_match_weekday(self):
        # 2026-09-22 is a Tuesday
        msg = r.validate_rule("weeks:1:1", "2026-09-22")
        self.assertIsNotNone(msg)
        self.assertIn("Mon", msg)
        self.assertIsNone(r.validate_rule("weeks:1:1,2", "2026-09-22"))


class OccursOn(unittest.TestCase):
    def test_never_before_anchor(self):
        self.assertFalse(r.occurs_on("daily", "2026-09-21", "2026-09-20"))
        self.assertTrue(r.occurs_on("daily", "2026-09-21", "2026-09-21"))

    def test_weekly_monday(self):
        anchor = "2026-09-21"  # Monday
        self.assertTrue(r.occurs_on("weeks:1:1", anchor, "2026-09-28"))
        self.assertFalse(r.occurs_on("weeks:1:1", anchor, "2026-09-27"))
        self.assertFalse(r.occurs_on("weeks:1:1", anchor, "2026-09-29"))

    def test_biweekly_phase_across_year_boundary(self):
        anchor = "2026-12-14"  # Monday
        expected = ["2026-12-14", "2026-12-28", "2027-01-11", "2027-01-25"]
        got = [d.isoformat() for d in r.next_rule_occurrences("weeks:2:1", anchor, "2026-12-14", 4)]
        self.assertEqual(got, expected)
        self.assertFalse(r.occurs_on("weeks:2:1", anchor, "2026-12-21"))
        self.assertFalse(r.occurs_on("weeks:2:1", anchor, "2027-01-04"))

    def test_biweekly_two_days_uses_week_of_anchor(self):
        # Mon+Thu every 2 weeks, anchored on a Thursday: the anchor's own
        # week counts, so the Monday of that week (before the anchor) is not
        # an occurrence, but Monday two weeks later is.
        anchor = "2026-09-24"  # Thursday
        self.assertFalse(r.occurs_on("weeks:2:1,4", anchor, "2026-09-21"))
        self.assertTrue(r.occurs_on("weeks:2:1,4", anchor, "2026-09-24"))
        self.assertFalse(r.occurs_on("weeks:2:1,4", anchor, "2026-09-28"))
        self.assertTrue(r.occurs_on("weeks:2:1,4", anchor, "2026-10-05"))

    def test_every_n_days(self):
        self.assertTrue(r.occurs_on("every:10", "2026-09-01", "2026-09-11"))
        self.assertFalse(r.occurs_on("every:10", "2026-09-01", "2026-09-12"))

    def test_monthly_31_clamps(self):
        for day, expected in [("2026-04-30", True), ("2026-04-29", False), ("2026-05-31", True),
                              ("2026-05-30", False), ("2027-02-28", True), ("2028-02-29", True),
                              ("2028-02-28", False)]:
            self.assertEqual(r.occurs_on("monthly:31", "2026-01-01", day), expected, day)

    def test_monthly_15(self):
        self.assertTrue(r.occurs_on("monthly:15", "2026-01-01", "2026-07-15"))
        self.assertFalse(r.occurs_on("monthly:15", "2026-01-01", "2026-07-16"))

    def test_nth_weekday(self):
        # Sep 2026: Thursdays are 3, 10, 17, 24
        self.assertTrue(r.occurs_on("nth:1:4", "2026-01-01", "2026-09-03"))
        self.assertFalse(r.occurs_on("nth:1:4", "2026-01-01", "2026-09-10"))
        self.assertTrue(r.occurs_on("nth:2:4", "2026-01-01", "2026-09-10"))
        self.assertTrue(r.occurs_on("nth:4:4", "2026-01-01", "2026-09-24"))

    def test_last_weekday_in_all_month_lengths(self):
        # last Friday of: Feb 2027 (28 days), Feb 2028 (29), Apr 2026 (30), Jul 2026 (31)
        for month_start, last_friday in [("2027-02-01", "2027-02-26"), ("2028-02-01", "2028-02-25"),
                                         ("2026-04-01", "2026-04-24"), ("2026-07-01", "2026-07-31")]:
            start = D(month_start)
            hits = [d for d in (start + timedelta(days=i) for i in range(31))
                    if d.month == start.month and r.occurs_on("nth:-1:5", "2026-01-01", d)]
            self.assertEqual([h.isoformat() for h in hits], [last_friday], month_start)


class NextOnOrAfter(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-09-22"), D("2026-09-28"))
        self.assertEqual(r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-09-21"), D("2026-09-21"))

    def test_before_anchor_starts_at_anchor(self):
        self.assertEqual(r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-01-01"), D("2026-09-21"))

    def test_sparsest_rule_terminates(self):
        self.assertEqual(r.next_on_or_after("every:999", "2026-01-01", "2026-01-02"), D("2026-01-01") + timedelta(days=999))

    def test_skip_keeps_phase(self):
        # biweekly Monday, skip 5 Oct -> next is still 19 Oct
        anchor = "2026-09-21"
        self.assertEqual(r.next_on_or_after("weeks:2:1", anchor, "2026-10-01"), D("2026-10-05"))
        self.assertEqual(r.next_on_or_after("weeks:2:1", anchor, "2026-10-01", removed={D("2026-10-05")}), D("2026-10-19"))

    def test_added_earlier_wins(self):
        self.assertEqual(r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-10-06", added={D("2026-10-10")}), D("2026-10-10"))
        self.assertEqual(r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-10-06", added={D("2026-10-20")}), D("2026-10-12"))

    def test_added_past_is_ignored(self):
        self.assertEqual(r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-10-06", added={D("2026-10-01")}), D("2026-10-12"))

    def test_move_removes_and_adds(self):
        # Mon 5 Oct moved to Tue 6 Oct
        got = r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-10-03", removed={D("2026-10-05")}, added={D("2026-10-06")})
        self.assertEqual(got, D("2026-10-06"))

    def test_only_added_left(self):
        self.assertEqual(r.next_on_or_after("every:999", "2026-01-01", "2026-01-02", added={D("2026-02-01")}), D("2026-02-01"))

    def test_many_removed(self):
        removed = {D("2026-09-21") + timedelta(days=7 * i) for i in range(50)}
        got = r.next_on_or_after("weeks:1:1", "2026-09-21", "2026-09-21", removed=removed)
        self.assertEqual(got, D("2026-09-21") + timedelta(days=7 * 50))


class SensorScenario(unittest.TestCase):
    """The on/off window from §8f: on iff next - today <= lead_days."""

    def sensor_on(self, rule, anchor, today, lead, removed=(), added=()):
        nxt = r.next_on_or_after(rule, anchor, today, removed, added)
        return (nxt - D(today)).days <= lead

    def test_weekly_monday_sunday_and_monday(self):
        # Mon 2026-09-21 anchor. Week of Mon 28 Sep: Sun 27 on, Mon 28 on, Tue 29..Sat 3 off, Sun 4 on
        expect = {"2026-09-27": True, "2026-09-28": True, "2026-09-29": False, "2026-10-03": False, "2026-10-04": True}
        for day, on in expect.items():
            self.assertEqual(self.sensor_on("weeks:1:1", "2026-09-21", day, 1), on, day)

    def test_biweekly_only_pickup_weeks(self):
        # pickups Mon 21 Sep, Mon 5 Oct, Mon 19 Oct
        expect = {"2026-09-20": True, "2026-09-21": True, "2026-09-22": False, "2026-09-27": False,
                  "2026-09-28": False, "2026-10-04": True, "2026-10-05": True, "2026-10-06": False}
        for day, on in expect.items():
            self.assertEqual(self.sensor_on("weeks:2:1", "2026-09-21", day, 1), on, day)

    def test_lead_zero_is_day_of(self):
        self.assertTrue(self.sensor_on("weeks:1:1", "2026-09-21", "2026-09-28", 0))
        self.assertFalse(self.sensor_on("weeks:1:1", "2026-09-21", "2026-09-27", 0))

    def test_lead_two_is_three_days(self):
        for day, on in {"2026-09-25": False, "2026-09-26": True, "2026-09-27": True, "2026-09-28": True, "2026-09-29": False}.items():
            self.assertEqual(self.sensor_on("weeks:1:1", "2026-09-21", day, 2), on, day)

    def test_skip_monday_sensor_stays_off(self):
        rem = {D("2026-10-05")}
        for day, on in {"2026-10-04": False, "2026-10-05": False, "2026-10-11": True, "2026-10-12": True}.items():
            self.assertEqual(self.sensor_on("weeks:1:1", "2026-09-21", day, 1, removed=rem), on, day)

    def test_move_monday_to_tuesday(self):
        rem, add = {D("2026-10-05")}, {D("2026-10-06")}
        for day, on in {"2026-10-04": False, "2026-10-05": True, "2026-10-06": True, "2026-10-07": False}.items():
            self.assertEqual(self.sensor_on("weeks:1:1", "2026-09-21", day, 1, removed=rem, added=add), on, day)

    def test_add_saturday(self):
        add = {D("2026-10-10")}
        for day, on in {"2026-10-08": False, "2026-10-09": True, "2026-10-10": True, "2026-10-11": True}.items():
            self.assertEqual(self.sensor_on("weeks:1:1", "2026-09-21", day, 1, added=add), on, day)


class IsEffective(unittest.TestCase):
    def test_all(self):
        rem, add = {D("2026-10-05")}, {D("2026-10-06")}
        self.assertFalse(r.is_effective("weeks:1:1", "2026-09-21", "2026-10-05", rem, add))
        self.assertTrue(r.is_effective("weeks:1:1", "2026-09-21", "2026-10-06", rem, add))
        self.assertTrue(r.is_effective("weeks:1:1", "2026-09-21", "2026-10-12", rem, add))
        self.assertFalse(r.is_effective("weeks:1:1", "2026-09-21", "2026-10-07", rem, add))


class Describe(unittest.TestCase):
    def test_labels(self):
        cases = {
            "daily": "Daily",
            "weeks:1:1": "Every Monday",
            "weeks:1:1,4": "Every Mon & Thu",
            "weeks:1:1,3,5": "Every Mon, Wed & Fri",
            "weeks:2:1": "Every 2 weeks on Mon",
            "every:10": "Every 10 days",
            "monthly:15": "Monthly on the 15th",
            "monthly:1": "Monthly on the 1st",
            "monthly:22": "Monthly on the 22nd",
            "monthly:23": "Monthly on the 23rd",
            "monthly:11": "Monthly on the 11th",
            "nth:2:2": "2nd Tuesday of the month",
            "nth:-1:5": "Last Friday of the month",
        }
        for rule, label in cases.items():
            self.assertEqual(r.describe_rule(rule), label, rule)


class MonthsAndYears(unittest.TestCase):
    def test_validation(self):
        for rule in ("months:2:15", "months:36:31", "years:1", "years:10"):
            self.assertIsNone(r.validate_rule(rule, "2026-09-21"), rule)
        for rule in ("months:1:15", "months:37:1", "months:3:0", "months:3:32", "years:0", "years:11", "years:x"):
            self.assertIsNotNone(r.validate_rule(rule, "2026-09-21"), rule)

    def test_every_3_months(self):
        got = r.next_rule_occurrences("months:3:15", "2026-10-15", "2026-10-01", 4)
        self.assertEqual([d.isoformat() for d in got], ["2026-10-15", "2027-01-15", "2027-04-15", "2027-07-15"])
        self.assertFalse(r.occurs_on("months:3:15", "2026-10-15", "2026-11-15"))
        self.assertFalse(r.occurs_on("months:3:15", "2026-10-15", "2027-01-16"))

    def test_months_clamp_to_short_month(self):
        # every 2 months on the 31st, counted from Jan: Feb is not in the cycle, Mar/May/Jul are; Nov 30 clamps
        self.assertTrue(r.occurs_on("months:2:31", "2026-01-31", "2026-03-31"))
        self.assertTrue(r.occurs_on("months:2:31", "2026-01-31", "2026-11-30"))
        self.assertFalse(r.occurs_on("months:2:31", "2026-01-31", "2026-02-28"))
        # every 3 months on the 30th from Nov: Feb clamps to the 28th
        self.assertTrue(r.occurs_on("months:3:30", "2026-11-30", "2027-02-28"))

    def test_months_never_before_anchor(self):
        self.assertFalse(r.occurs_on("months:2:15", "2026-10-15", "2026-08-15"))

    def test_every_5_years(self):
        got = r.next_rule_occurrences("years:5", "2026-11-03", "2026-01-01", 3)
        self.assertEqual([d.isoformat() for d in got], ["2026-11-03", "2031-11-03", "2036-11-03"])
        self.assertFalse(r.occurs_on("years:5", "2026-11-03", "2027-11-03"))

    def test_leap_day_anchor(self):
        got = r.next_rule_occurrences("years:1", "2024-02-29", "2024-01-01", 4)
        self.assertEqual([d.isoformat() for d in got], ["2024-02-29", "2025-02-28", "2026-02-28", "2027-02-28"])
        got = r.next_rule_occurrences("years:2", "2024-02-29", "2024-01-01", 3)
        self.assertEqual([d.isoformat() for d in got], ["2024-02-29", "2026-02-28", "2028-02-29"])

    def test_sparse_rule_terminates_with_exceptions(self):
        # years:10 is the sparsest legal rule; stepping must reach the next occurrence
        self.assertEqual(r.next_on_or_after("years:10", "2026-05-05", "2026-05-06"), D("2036-05-05"))
        self.assertEqual(r.next_on_or_after("years:10", "2026-05-05", "2026-05-06", removed=["2036-05-05"]), D("2046-05-05"))

    def test_labels_and_gaps(self):
        self.assertEqual(r.describe_rule("months:3:15"), "Every 3 months on the 15th")
        self.assertEqual(r.describe_rule("years:5"), "Every 5 years")
        self.assertEqual(r.describe_rule("years:1"), "Every year")
        self.assertEqual(r.min_gap_days("months:6:1"), 168)
        self.assertEqual(r.min_gap_days("years:2"), 730)


class Gaps(unittest.TestCase):
    def test_min_gap(self):
        self.assertEqual(r.min_gap_days("daily"), 1)
        self.assertEqual(r.min_gap_days("weeks:1:1"), 7)
        self.assertEqual(r.min_gap_days("weeks:2:1"), 14)
        self.assertEqual(r.min_gap_days("weeks:1:1,4"), 3)
        self.assertEqual(r.min_gap_days("every:5"), 5)
        self.assertEqual(r.min_gap_days("monthly:15"), 28)


class Between(unittest.TestCase):
    def test_range(self):
        got = r.rule_occurrences_between("weeks:1:1", "2026-09-21", "2026-09-01", "2026-10-15")
        self.assertEqual([d.isoformat() for d in got], ["2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"])


if __name__ == "__main__":
    unittest.main()
