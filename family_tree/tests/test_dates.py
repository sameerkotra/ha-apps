"""dates.py: parsing, canonical storage, display, round trips, ages and the living rule (§4, §5)."""
import _env  # noqa: F401

import unittest
from datetime import date

from app import dates
from app.dates import DateError


def row(cols):
    """from_parts output is a valid stored row for to_parts/display."""
    return cols


class FromParts(unittest.TestCase):
    def test_exact_full(self):
        c = dates.from_parts({"qual": "exact", "d": 12, "m": 3, "y": 1950})
        self.assertEqual(c, {"date_text": "12 MAR 1950", "date_y": 1950, "date_m": 3, "date_d": 12,
                             "date_approx": 0, "sort_key": "1950-03-12"})
        self.assertEqual(dates.display(c), "12 March 1950")
        self.assertEqual(dates.to_parts(c), {"qual": "exact", "d": 12, "m": 3, "y": 1950})

    def test_default_qualifier_is_exact(self):
        c = dates.from_parts({"y": 1950})
        self.assertEqual(c["date_text"], "1950")
        self.assertEqual(c["sort_key"], "1950-00-00")
        self.assertEqual(c["date_approx"], 0)
        self.assertEqual(dates.display(c), "1950")

    def test_month_year(self):
        c = dates.from_parts({"m": 3, "y": 1950})
        self.assertEqual(c["date_text"], "MAR 1950")
        self.assertEqual(c["sort_key"], "1950-03-00")
        self.assertEqual(dates.display(c), "March 1950")
        self.assertEqual(dates.to_parts(c), {"qual": "exact", "d": None, "m": 3, "y": 1950})

    def test_about(self):
        c = dates.from_parts({"qual": "about", "y": 1950})
        self.assertEqual(c["date_text"], "ABT 1950")
        self.assertEqual(c["date_approx"], 1)
        self.assertEqual(c["sort_key"], "1950-00-00")
        self.assertEqual(dates.display(c), "about 1950")
        self.assertEqual(dates.to_parts(c)["qual"], "about")

    def test_before(self):
        c = dates.from_parts({"qual": "before", "m": 6, "y": 1920})
        self.assertEqual(c["date_text"], "BEF JUN 1920")
        self.assertEqual(c["date_approx"], 1)
        self.assertEqual(dates.display(c), "before June 1920")
        self.assertEqual(dates.to_parts(c), {"qual": "before", "d": None, "m": 6, "y": 1920})

    def test_after(self):
        c = dates.from_parts({"qual": "after", "d": 1, "m": 1, "y": 1900})
        self.assertEqual(c["date_text"], "AFT 1 JAN 1900")
        self.assertEqual(dates.display(c), "after 1 January 1900")
        self.assertEqual(dates.to_parts(c)["qual"], "after")

    def test_between_years(self):
        c = dates.from_parts({"qual": "between", "y": 1900, "y2": 1910})
        self.assertEqual(c["date_text"], "BET 1900 AND 1910")
        self.assertEqual(c["date_y"], 1900)
        self.assertEqual(c["date_approx"], 1)
        self.assertEqual(c["sort_key"], "1900-00-00")
        self.assertEqual(dates.display(c), "between 1900 and 1910")
        self.assertEqual(dates.to_parts(c), {"qual": "between", "d": None, "m": None, "y": 1900,
                                             "d2": None, "m2": None, "y2": 1910})

    def test_between_full_dates(self):
        c = dates.from_parts({"qual": "between", "d": 5, "m": 2, "y": 1900, "d2": 7, "m2": 3, "y2": 1900})
        self.assertEqual(c["date_text"], "BET 5 FEB 1900 AND 7 MAR 1900")
        self.assertEqual(dates.display(c), "between 5 February 1900 and 7 March 1900")
        p = dates.to_parts(c)
        self.assertEqual((p["d2"], p["m2"], p["y2"]), (7, 3, 1900))

    def test_between_same_date_allowed(self):
        c = dates.from_parts({"qual": "between", "y": 1900, "y2": 1900})
        self.assertEqual(c["date_text"], "BET 1900 AND 1900")

    def test_yearless_day_month(self):
        c = dates.from_parts({"d": 12, "m": 3})
        self.assertEqual(c["date_text"], "12 MAR")
        self.assertIsNone(c["date_y"])
        self.assertEqual((c["date_m"], c["date_d"]), (3, 12))
        self.assertIsNone(c["sort_key"])
        self.assertEqual(dates.display(c), "12 March")
        self.assertEqual(dates.to_parts(c), {"qual": "exact", "d": 12, "m": 3, "y": None})

    def test_29_feb_without_year_ok(self):
        c = dates.from_parts({"d": 29, "m": 2})
        self.assertEqual(c["date_text"], "29 FEB")
        self.assertEqual(dates.display(c), "29 February")

    def test_29_feb_leap_year_2000_ok(self):
        c = dates.from_parts({"d": 29, "m": 2, "y": 2000})
        self.assertEqual(c["sort_key"], "2000-02-29")

    def test_29_feb_1900_rejected(self):
        with self.assertRaises(DateError):
            dates.from_parts({"d": 29, "m": 2, "y": 1900})

    def test_31_april_rejected(self):
        with self.assertRaises(DateError):
            dates.from_parts({"d": 31, "m": 4, "y": 2001})
        with self.assertRaises(DateError):
            dates.from_parts({"d": 31, "m": 4})

    def test_empty_is_none(self):
        self.assertIsNone(dates.from_parts(None))
        self.assertIsNone(dates.from_parts({}))
        self.assertIsNone(dates.from_parts({"qual": "about", "d": None, "m": "", "y": None}))

    def test_rejected_combinations(self):
        bad = [
            {"d": 12},                                        # day without month
            {"d": 12, "y": 1950},                             # day + year, no month
            {"m": 3},                                         # month alone
            {"qual": "between", "d": 1, "m": 1, "d2": 2, "m2": 1},     # between without years
            {"qual": "between", "y": 1950},                   # between with one year
            {"qual": "between", "y": 1910, "y2": 1900},       # second before first
            {"qual": "between", "m": 5, "y": 1900, "m2": 4, "y2": 1900},
            {"qual": "about", "d": 1, "m": 2},                # qualifiers need a year
            {"qual": "before", "d": 1, "m": 2},
            {"qual": "sometime", "y": 1900},                  # unknown qualifier
            {"m": 13, "y": 1900},
            {"m": 0, "y": 1900},
            {"y": 0},
            {"y": 2201},
            {"d": 0, "m": 1, "y": 1900},
            {"y": "abc"},
        ]
        for b in bad:
            with self.subTest(b=b):
                with self.assertRaises(DateError):
                    dates.from_parts(b)

    def test_date_error_is_value_error(self):
        self.assertTrue(issubclass(DateError, ValueError))


class ParseText(unittest.TestCase):
    def check(self, text, date_text, y=None, m=None, d=None, approx=0):
        c = dates.parse_text(text)
        self.assertEqual(c["date_text"], date_text, text)
        self.assertEqual((c["date_y"], c["date_m"], c["date_d"]), (y, m, d), text)
        self.assertEqual(c["date_approx"], approx, text)
        return c

    def test_forms(self):
        self.check("1950", "1950", 1950)
        self.check("Mar 1950", "MAR 1950", 1950, 3)
        self.check("12 March 1950", "12 MAR 1950", 1950, 3, 12)
        self.check("12 MAR 1950", "12 MAR 1950", 1950, 3, 12)
        self.check("1950-03-12", "12 MAR 1950", 1950, 3, 12)
        self.check("September 1901", "SEP 1901", 1901, 9)
        self.check("Sept 1901", "SEP 1901", 1901, 9)

    def test_yearless(self):
        c = self.check("12 MAR", "12 MAR", None, 3, 12)
        self.assertIsNone(c["sort_key"])
        self.check("March 12", "12 MAR", None, 3, 12)
        self.check("29 Feb", "29 FEB", None, 2, 29)

    def test_qualifiers(self):
        self.check("ABT 1950", "ABT 1950", 1950, approx=1)
        self.check("about 1950", "ABT 1950", 1950, approx=1)
        self.check("circa 1950", "ABT 1950", 1950, approx=1)
        self.check("EST 1950", "ABT 1950", 1950, approx=1)
        self.check("BEF 1920", "BEF 1920", 1920, approx=1)
        self.check("before 1920", "BEF 1920", 1920, approx=1)
        self.check("AFT 3 JUN 1900", "AFT 3 JUN 1900", 1900, 6, 3, approx=1)
        self.check("after 1900", "AFT 1900", 1900, approx=1)

    def test_between(self):
        c = self.check("BET 1900 AND 1910", "BET 1900 AND 1910", 1900, approx=1)
        self.assertEqual(dates.display(c), "between 1900 and 1910")
        self.check("between 1900 and 1910", "BET 1900 AND 1910", 1900, approx=1)
        self.check("from 1900 to 1910", "BET 1900 AND 1910", 1900, approx=1)
        self.check("BET MAR 1900 AND 12 APR 1901", "BET MAR 1900 AND 12 APR 1901", 1900, 3, approx=1)

    def test_lone_small_number_is_a_year(self):
        self.check("45", "45", 45)

    def test_empty(self):
        self.assertIsNone(dates.parse_text(None))
        self.assertIsNone(dates.parse_text("   "))

    def test_rejected(self):
        for t in ("March", "29 FEB 1900", "between 1900", "BET 1910 AND 1900", "ABT", "hello 1900",
                  "MAR APR 1900", "1900 1901", "about 12 MAR", "31 APR"):
            with self.subTest(t=t):
                with self.assertRaises(DateError):
                    dates.parse_text(t)

    def test_round_trip_through_text(self):
        for parts in ({"y": 1950}, {"m": 3, "y": 1950}, {"d": 12, "m": 3, "y": 1950}, {"d": 12, "m": 3},
                      {"qual": "about", "y": 1950}, {"qual": "before", "m": 1, "y": 1920},
                      {"qual": "after", "d": 2, "m": 2, "y": 1902},
                      {"qual": "between", "y": 1900, "m2": 5, "y2": 1910}):
            with self.subTest(parts=parts):
                c = dates.from_parts(parts)
                again = dates.parse_text(c["date_text"])
                self.assertEqual(again, c)


class AgesAndLiving(unittest.TestCase):
    T = date(2026, 9, 25)

    def b(self, y=None, m=None, d=None):
        return {"date_y": y, "date_m": m, "date_d": d}

    def test_age_on(self):
        self.assertEqual(dates.age_on(self.b(1950, 3, 12), self.T), 76)
        self.assertEqual(dates.age_on(self.b(1950, 12, 1), self.T), 75)       # birthday not yet this year
        self.assertEqual(dates.age_on(self.b(1950, 9, 25), self.T), 76)       # birthday today
        self.assertEqual(dates.age_on(self.b(1950, 9, 26), self.T), 75)
        self.assertEqual(dates.age_on(self.b(1950, 10), self.T), 75)          # month known, day not
        self.assertEqual(dates.age_on(self.b(1950), self.T), 76)              # year only
        self.assertIsNone(dates.age_on(self.b(None, 3, 12), self.T))          # year-less birthday
        self.assertIsNone(dates.age_on(None, self.T))
        self.assertEqual(dates.age_on(self.b(2030), self.T), 0)               # never negative

    def test_age_between(self):
        self.assertEqual(dates.age_between(self.b(1900, 5, 1), self.b(1980, 4, 30)), 79)
        self.assertEqual(dates.age_between(self.b(1900, 5, 1), self.b(1980, 5, 1)), 80)
        self.assertIsNone(dates.age_between(self.b(1900), self.b(None, 3, 3)))
        self.assertIsNone(dates.age_between(self.b(1900), None))

    def test_living_rule(self):
        T = self.T
        self.assertTrue(dates.is_living(False, self.b(1990), None, T))
        self.assertTrue(dates.is_living(False, None, None, T))                          # no birth at all
        self.assertTrue(dates.is_living(False, self.b(T.year - 110), None, T))
        self.assertFalse(dates.is_living(False, self.b(T.year - 111), None, T))         # > 110 years
        self.assertFalse(dates.is_living(False, self.b(1850, 1, 1), None, T))
        self.assertTrue(dates.is_living(False, self.b(None, 1, 1), None, T))            # year-less: never too old
        self.assertFalse(dates.is_living(True, self.b(1990), None, T))                  # deceased flag
        self.assertFalse(dates.is_living(False, self.b(1990), self.b(2020), T))         # death event
        self.assertFalse(dates.is_living(False, None, self.b(None, None, None), T))     # undated death event

    def test_living_rule_uses_the_full_birth_date(self):
        # §4: living unless "born more than 110 years ago". Born 1 Jan 1916 → on 25 Sep 2026
        # they were born 110 years and 9 months ago.
        self.assertEqual(dates.age_on(self.b(1916, 1, 1), self.T), 110)
        self.assertFalse(dates.is_living(False, self.b(1916, 1, 1), None, self.T))
        # born later in the year: not yet 110 → living
        self.assertTrue(dates.is_living(False, self.b(1916, 12, 31), None, self.T))

    def test_years_label(self):
        self.assertEqual(dates.years_label(self.b(1950), None, True), "b. 1950")
        self.assertEqual(dates.years_label(None, None, True), "")
        self.assertEqual(dates.years_label(self.b(1900), self.b(1980), False), "1900–1980")
        self.assertEqual(dates.years_label(None, self.b(1980), False), "?–1980")
        self.assertEqual(dates.years_label(self.b(1900), None, False), "1900–?")
        self.assertEqual(dates.years_label(None, None, False), "")


if __name__ == "__main__":
    unittest.main()
