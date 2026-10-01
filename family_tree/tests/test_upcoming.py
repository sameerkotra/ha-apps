"""Upcoming birthdays, anniversaries and remembrance days (§9, §11; upcoming.py
and GET /api/upcoming). "Today" is patched through config.today."""
from base import ApiTestCase
import _env  # noqa: F401

import unittest
from datetime import date
from unittest import mock

from app import config, upcoming


class UpcomingCase(ApiTestCase):
    TODAY = date(2026, 9, 25)

    def setUp(self):
        super().setUp()
        self.set_today(self.TODAY)

    def set_today(self, d):
        p = mock.patch.object(config, "today", return_value=d)
        p.start()
        self.addCleanup(p.stop)

    def up(self, days=60, scope=None, remembrance=None, user=None):
        qs = f"days={days}"
        if scope:
            qs += f"&scope={scope}"
        if remembrance is not None:
            qs += f"&remembrance={'true' if remembrance else 'false'}"
        return self.ok(self.get(f"/api/upcoming?{qs}", user=user))

    def items(self, **kw):
        return self.up(**kw)["items"]

    def titles(self, **kw):
        return [i["title"] for i in self.items(**kw)]

    def married(self, a, b, d=None, m=None, y=None, **extra):
        body = {"partner1Id": a, "partner2Id": b, "kind": "married"}
        if d or m or y:
            body["marriage"] = {"date": {"d": d, "m": m, "y": y}}
        body.update(extra)
        return self.ok(self.post("/api/families", body), 201)["family"]["id"]


class Birthdays(UpcomingCase):
    def test_with_and_without_year(self):
        ann = self.person("Ann", "Smith", born={"d": 1, "m": 10, "y": 1960})
        bob = self.person("Bob", "Jones", born={"d": 3, "m": 10})
        r = self.up()
        self.assertEqual(r["today"], "2026-09-25")
        self.assertFalse(r["meSet"])
        self.assertEqual(r["scope"], "all")
        a, b = r["items"]
        self.assertEqual((a["date"], a["daysAway"], a["kind"], a["sub"], a["title"], a["years"]),
                         ("2026-10-01", 6, "birthday", "birth", "Ann Smith turns 66", 66))
        self.assertEqual([p["id"] for p in a["people"]], [ann])
        self.assertIsNone(a["people"][0]["relationship"])
        self.assertEqual((b["date"], b["title"], b["years"]), ("2026-10-03", "Bob Jones's birthday", None))
        self.assertEqual(b["people"][0]["id"], bob)

    def test_today_counts_and_passed_ones_come_next_year(self):
        self.person("Today", born={"d": 25, "m": 9, "y": 2000})
        self.person("Yesterday", born={"d": 24, "m": 9, "y": 2000})
        items = self.items(days=366)
        self.assertEqual([(i["date"], i["daysAway"], i["years"]) for i in items],
                         [("2026-09-25", 0, 26), ("2027-09-24", 364, 27)])

    def test_days_window_is_inclusive(self):
        self.person("Six", born={"d": 1, "m": 10, "y": 1990})
        self.assertEqual(self.items(days=5), [])
        self.assertEqual(len(self.items(days=6)), 1)

    def test_needs_day_and_month(self):
        self.person("YearOnly", born={"y": 1960})
        self.person("MonthYear", born={"m": 10, "y": 1960})
        self.person("NoBirth")
        self.assertEqual(self.items(days=366), [])

    def test_deceased_not_a_birthday(self):
        self.person("Gone", born={"d": 1, "m": 10, "y": 1930}, died={"y": 2000})
        self.person("Flagged", born={"d": 1, "m": 10, "y": 1950}, deceased=True)
        self.person("Ancient", born={"d": 1, "m": 10, "y": 1900})                # over 110: not living
        self.assertEqual(self.items(), [])

    def test_no_zeroth_or_negative_birthdays(self):
        # born today, or a date still to come (a due date typed in early): nothing to celebrate yet
        self.person("Newborn", born={"d": 25, "m": 9, "y": 2026})
        self.person("Due", born={"d": 1, "m": 10, "y": 2026})
        self.person("Due next year", born={"d": 2, "m": 10, "y": 2027})
        self.person("Yesterday", born={"d": 24, "m": 9, "y": 2026})
        items = self.items(days=366)
        self.assertEqual([(i["title"], i["years"]) for i in items], [("Yesterday turns 1", 1)])

    def test_bad_params(self):
        for qs in ("days=0", "days=367", "scope=nearby", "days=abc"):
            with self.subTest(qs=qs):
                self.assertEqual(self.get(f"/api/upcoming?{qs}").status_code, 422)
        self.assertEqual(self.get("/api/upcoming").status_code, 200)       # defaults


class LeapDay(UpcomingCase):
    def setUp(self):
        super().setUp()
        self.pid = self.person("Leap", born={"d": 29, "m": 2, "y": 2000})
        self.noyear = self.person("Leapy", born={"d": 29, "m": 2})

    def when(self, today, days=60):
        self.set_today(today)
        return [(i["title"], i["date"], i["daysAway"]) for i in self.items(days=days)]

    def test_non_leap_year_falls_on_28_feb(self):
        self.assertEqual(self.when(date(2027, 2, 1)), [("Leap turns 27", "2027-02-28", 27), ("Leapy's birthday", "2027-02-28", 27)])

    def test_leap_year_is_29_feb(self):
        self.assertEqual(self.when(date(2028, 2, 1)), [("Leap turns 28", "2028-02-29", 28), ("Leapy's birthday", "2028-02-29", 28)])

    def test_on_28_feb_of_a_non_leap_year_it_is_today(self):
        self.assertEqual(self.when(date(2027, 2, 28), days=1), [("Leap turns 27", "2027-02-28", 0), ("Leapy's birthday", "2027-02-28", 0)])

    def test_after_28_feb_the_next_one_is_the_leap_day(self):
        self.assertEqual(self.when(date(2027, 3, 1), days=366),
                         [("Leap turns 28", "2028-02-29", 365), ("Leapy's birthday", "2028-02-29", 365)])
        self.assertEqual(self.when(date(2027, 3, 1), days=364), [])

    def test_next_date(self):
        self.assertEqual(upcoming.next_date(2, 29, date(2027, 1, 1)), date(2027, 2, 28))
        self.assertEqual(upcoming.next_date(2, 29, date(2028, 1, 1)), date(2028, 2, 29))
        self.assertEqual(upcoming.next_date(2, 29, date(2028, 3, 1)), date(2029, 2, 28))
        self.assertEqual(upcoming.next_date(12, 31, date(2026, 12, 31)), date(2026, 12, 31))
        self.assertEqual(upcoming.next_date(1, 1, date(2026, 12, 31)), date(2027, 1, 1))


class Anniversaries(UpcomingCase):
    def setUp(self):
        super().setUp()
        self.ravi = self.person("Ravi", "Rao", gender="male", born={"y": 1970})
        self.priya = self.person("Priya", "Rao", gender="female", born={"y": 1972})

    def test_both_living(self):
        fid = self.married(self.ravi, self.priya, 10, 10, 2001)
        items = self.items()
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["kind"], it["sub"], it["date"], it["years"], it["familyId"]),
                         ("anniversary", "marriage", "2026-10-10", 25, fid))
        self.assertIn(it["title"], ("Ravi & Priya — 25th anniversary", "Priya & Ravi — 25th anniversary"))
        self.assertEqual(sorted(p["id"] for p in it["people"]), sorted([self.ravi, self.priya]))

    def test_without_year(self):
        self.married(self.ravi, self.priya, 10, 10)
        it = self.items()[0]
        self.assertIsNone(it["years"])
        self.assertIn(it["title"], ("Ravi & Priya's anniversary", "Priya & Ravi's anniversary"))

    def test_divorced_or_separated_not_shown(self):
        for ended in ("divorced", "separated"):
            with self.subTest(ended=ended):
                fid = self.married(self.ravi, self.priya, 10, 10, 2001)
                self.ok(self.patch(f"/api/families/{fid}", {"ended": ended}))
                self.assertEqual(self.items(), [])
                self.ok(self.delete(f"/api/families/{fid}"))

    def test_one_partner_deceased_not_shown(self):
        self.married(self.ravi, self.priya, 10, 10, 2001)
        self.ok(self.patch(f"/api/people/{self.priya}", {"deceased": True}))
        self.assertEqual(self.items(), [])
        self.assertEqual([i["kind"] for i in self.items(remembrance=True)], [])

    def test_single_partner_or_no_day_not_shown(self):
        solo = self.person("Solo")
        self.ok(self.post("/api/families", {"partner1Id": solo, "marriage": {"date": {"d": 10, "m": 10, "y": 2001}}}), 201)
        self.married(self.ravi, self.priya, None, 10, 2001)
        self.assertEqual(self.items(), [])

    def test_trashed_family_not_shown(self):
        fid = self.married(self.ravi, self.priya, 10, 10, 2001)
        self.ok(self.delete(f"/api/families/{fid}"))
        self.assertEqual(self.items(), [])

    def test_wedding_day_itself_is_not_an_anniversary(self):
        self.married(self.ravi, self.priya, 25, 9, 2026)
        self.assertEqual(self.items(), [])
        self.set_today(date(2027, 9, 20))
        self.assertEqual([i["years"] for i in self.items()], [1])

    def test_ordinals(self):
        for n, s in ((1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"),
                     (21, "21st"), (22, "22nd"), (23, "23rd"), (50, "50th"), (101, "101st"), (111, "111th"), (112, "112th")):
            self.assertEqual(upcoming.ordinal(n), s)


class Remembrance(UpcomingCase):
    def setUp(self):
        super().setUp()
        self.gp = self.person("Venkat", "Rao", born={"d": 5, "m": 10, "y": 1920}, died={"d": 20, "m": 10, "y": 1990})

    def test_off_by_default(self):
        self.assertEqual(self.items(), [])
        self.assertEqual(self.items(remembrance=False), [])

    def test_on(self):
        items = self.items(remembrance=True)
        self.assertEqual([(i["kind"], i["sub"], i["date"], i["years"], i["title"]) for i in items], [
            ("remembrance", "birth", "2026-10-05", 106, "Venkat Rao — born 106 years ago"),
            ("remembrance", "death", "2026-10-20", 36, "Remembering Venkat Rao — 36 years"),
        ])

    def test_without_years(self):
        pid = self.person("Old", born={"d": 1, "m": 10}, died={"d": 2, "m": 10})
        items = [i for i in self.items(remembrance=True) if i["people"][0]["id"] == pid]
        self.assertEqual([(i["title"], i["years"]) for i in items], [("Old's birthday", None), ("Remembering Old", None)])

    def test_died_today_is_not_a_remembrance_day_yet(self):
        pid = self.person("Recent", born={"d": 1, "m": 1, "y": 1940}, died={"d": 25, "m": 9, "y": 2026})
        self.assertEqual([i for i in self.items(remembrance=True) if i["people"][0]["id"] == pid and i["sub"] == "death"], [])

    def test_living_people_have_no_remembrance(self):
        self.person("Alive", born={"d": 1, "m": 10, "y": 1990})
        kinds = sorted(i["kind"] for i in self.items(remembrance=True))
        self.assertEqual(kinds, ["birthday", "remembrance", "remembrance"])


class Scope(UpcomingCase):
    """me → parent → grandparent → great-grandparent (3 steps) → great-great (4)."""

    def setUp(self):
        super().setUp()
        b = lambda d: {"d": d, "m": 10, "y": 1950}         # noqa: E731
        self.me = self.person("Me", gender="female", born={"d": 1, "m": 10, "y": 1990})
        self.mum = self.person("Mum", gender="female", born=b(2))
        self.gran = self.person("Gran", gender="female", born=b(3))
        self.ggran = self.person("Ggran", gender="female", born=b(4))
        self.gggran = self.person("Gggran", gender="female", born=b(5))
        self.stranger = self.person("Stranger", born=b(6))
        self.family(self.mum, None, children=[self.me])
        self.family(self.gran, None, children=[self.mum])
        self.family(self.ggran, None, children=[self.gran])
        self.family(self.gggran, None, children=[self.ggran])

    def names(self, **kw):
        return [i["people"][0]["id"] for i in self.items(**kw)]

    def test_me_unset_shows_everyone(self):
        r = self.up(scope="close")
        self.assertFalse(r["meSet"])
        self.assertEqual(r["scope"], "all")
        self.assertEqual(len(r["items"]), 6)

    def test_close_family_within_three_steps(self):
        self.set_me(self.me)
        r = self.up()
        self.assertTrue(r["meSet"])
        self.assertEqual(r["scope"], "close")
        self.assertEqual(self.names(), [self.me, self.mum, self.gran, self.ggran])
        rels = {i["people"][0]["id"]: i["people"][0]["relationship"] for i in r["items"]}
        self.assertIsNone(rels[self.me])
        self.assertEqual(rels[self.mum], "mother")
        self.assertEqual(rels[self.gran], "grandmother")

    def test_scope_all(self):
        self.set_me(self.me)
        r = self.up(scope="all")
        self.assertEqual(r["scope"], "all")
        self.assertEqual(self.names(scope="all"), [self.me, self.mum, self.gran, self.ggran, self.gggran, self.stranger])
        rels = {i["people"][0]["id"]: i["people"][0]["relationship"] for i in r["items"]}
        self.assertIsNone(rels[self.stranger])            # "not related" isn't a label

    def test_partners_and_siblings_count_as_steps(self):
        self.set_me(self.me)
        sis = self.person("Sis", born={"d": 7, "m": 10, "y": 1992})
        mum_fam = sql_family_of(self, self.mum)
        self.ok(self.post(f"/api/families/{mum_fam}/children", {"existingId": sis, "relation": "birth"}), 201)
        husband = self.person("Husband", gender="male", born={"d": 8, "m": 10, "y": 1989})
        self.married(self.me, husband, 9, 10, 2015)
        ids = self.names()
        self.assertIn(sis, ids)
        self.assertIn(husband, ids)
        ann = [i for i in self.items() if i["kind"] == "anniversary"]
        self.assertEqual(len(ann), 1)
        self.assertEqual(ann[0]["years"], 11)

    def test_anniversary_in_close_scope_needs_a_close_partner(self):
        self.set_me(self.me)
        a, b = self.person("A", born={"y": 1960}), self.person("B", born={"y": 1960})
        self.married(a, b, 10, 10, 2000)
        self.assertEqual([i for i in self.items() if i["kind"] == "anniversary"], [])
        self.assertEqual(len([i for i in self.items(scope="all") if i["kind"] == "anniversary"]), 1)

    def test_me_trashed_counts_as_unset(self):
        self.set_me(self.stranger)
        self.ok(self.delete(f"/api/people/{self.stranger}"))
        r = self.up()
        self.assertFalse(r["meSet"])
        self.assertEqual(len(r["items"]), 5)

    def test_per_user(self):
        self.set_me(self.me)
        self.assertEqual(len(self.items()), 4)
        from base import BOB
        self.assertEqual(len(self.items(user=BOB)), 6)


def sql_family_of(case, parent):
    from base import sql
    return sql("SELECT id FROM families WHERE partner1_id = ? OR partner2_id = ?", (parent, parent))[0]["id"]


class Ordering(UpcomingCase):
    def test_by_date_then_kind_then_title(self):
        self.person("Zed", born={"d": 1, "m": 10, "y": 1990})
        self.person("Amy", born={"d": 1, "m": 10, "y": 1991})
        self.person("Early", born={"d": 26, "m": 9, "y": 1991})
        self.person("Gone", born={"d": 1, "m": 10, "y": 1920}, died={"y": 1990})
        x, y = self.person("X", born={"y": 1960}), self.person("Y", born={"y": 1960})
        self.married(x, y, 1, 10, 2000)
        got = [(i["date"], i["kind"], i["title"]) for i in self.items(remembrance=True)]
        self.assertEqual(got, sorted(got))
        self.assertEqual([k for _d, k, _t in got], ["birthday", "anniversary", "birthday", "birthday", "remembrance"])
        self.assertEqual(got[0][2], "Early turns 35")
        self.assertEqual([t for _d, k, t in got if k == "birthday"][1:], ["Amy turns 35", "Zed turns 36"])


if __name__ == "__main__":
    unittest.main()
