"""GEDCOM (§14): the export as a .ged file (the same filtered data as the website), and importing a .ged — from this
app or another program (notes by reference, CONC/CONT, UTF-16, PEDI and _FREL, ABT/INT/year-less dates, OCCU,
maiden names) — through Admin → Import's preview and apply: one undoable batch; a newer file from the same place
only adds what's new. All people here are invented."""
from base import ADMIN, sql

import unittest
import unittest.mock

from test_import import ImportCase
from app import gedcom, tree_data

OTHER_PROGRAM = """0 HEAD
1 SOUR SOMEPROG
2 NAME Some Genealogy Program
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
1 SUBM @S1@
0 @S1@ SUBM
1 NAME Meena Iyer
0 @P1@ INDI
1 NAME Raman /Iyer/
2 GIVN Raman
2 SURN Iyer
1 SEX M
1 BIRT
2 DATE ABT 1931
2 PLAC Thanjavur, Tamil Nadu, India
1 OCCU Railway engineer
1 DEAT Y
1 NOTE @N1@
1 FAMS @F1@
0 @P2@ INDI
1 NAME Kamala /Iyer/
1 NAME Kamala /Subramanian/
2 TYPE maiden
1 SEX F
1 BIRT
2 DATE 12 MAR
1 FAMS @F1@
0 @P3@ INDI
1 NAME Lakshmi /Iyer/
2 NICK Lucky
1 SEX F
1 BIRT
2 DATE INT 1960 (around the time of the floods)
1 FAMC @F1@
0 @P4@ INDI
1 NAME Suresh /Iyer/
1 SEX M
1 FAMC @F1@
2 PEDI adopted
0 @P5@ INDI
1 NAME Gopi /Iyer/
1 SEX M
1 BIRT
2 DATE someday
0 @F1@ FAM
1 HUSB @P1@
1 WIFE @P2@
1 CHIL @P3@
1 CHIL @P4@
1 CHIL @P5@
2 _FREL Step
1 MARR
2 DATE 4 JUN 1955
2 PLAC Madurai
0 @N1@ NOTE Loved trains and
1 CONC  long walks.
1 CONT Grew mangoes.
0 TRLR
"""


class TestGedcom(ImportCase):
    def export_ged(self, options=None) -> str:
        self.a.load()
        r = self.post("/api/export/gedcom", {"format": "gedcom", "options": options or {"living": "full"}}, user=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('filename="family-tree-', r.headers["content-disposition"])
        self.a.save()
        return r.content.decode("utf-8")

    def test_export_is_gedcom_with_the_same_choices(self):
        text = self.export_ged()
        self.assertTrue(text.startswith("0 HEAD\n1 SOUR FAMILY_TREE"))
        self.assertIn("2 VERS 5.5.1", text)
        self.assertIn("1 NAME Venkat /Sharma/", text)
        self.assertIn("1 NOTE Taught maths.", text)
        self.assertIn("2 DATE 1920", text)
        self.assertTrue(text.rstrip().endswith("0 TRLR"))
        for line in text.splitlines():
            self.assertLessEqual(len(line), 255)
        data = tree_data.validate(gedcom.to_tree_data(text.encode()))
        people = {p["given_names"]: p for p in data["people"]}
        self.assertEqual(set(people), {"Gopal", "Gita", "Venkat", "Ravi", "Arjun"})
        self.assertEqual(people["Venkat"]["biography"], "Taught maths.")
        self.assertTrue(people["Gopal"]["deceased"])
        self.assertEqual(len(data["families"]), 2)
        # what the choices leave out isn't in the file either
        limited = self.export_ged({"living": "limited"})
        self.assertNotIn("1980", limited)                             # Arjun is living: name only
        self.assertIn("Arjun /Sharma/", limited)

    def test_round_trip_into_another_tree(self):
        text = self.export_ged()
        prev = self.upload(text.encode("utf-8"), name="family.ged")
        self.assertEqual((prev["counts"]["newPeople"], prev["counts"]["newFamilies"]), (5, 2))
        stats = self.apply(prev["token"])
        self.assertEqual((stats["people"], stats["families"], stats["children"]), (5, 2, 3))
        ids = {r["given_names"]: r["id"] for r in sql("SELECT id, given_names FROM people")}
        self.assertEqual(self.label(ids["Arjun"], ids["Gopal"]), "grandfather")
        prev = self.upload(text.encode("utf-8"), name="family.ged")         # the same file again: nothing new
        self.assertEqual((prev["counts"]["newPeople"], prev["counts"]["newFamilies"]), (0, 0))

    def test_a_file_from_another_program(self):
        for raw in (OTHER_PROGRAM.encode("utf-8"), OTHER_PROGRAM.replace("\n", "\r\n").encode("utf-16")):
            prev = self.upload(raw, name="iyer.ged")
            self.assertEqual((prev["counts"]["newPeople"], prev["counts"]["newFamilies"]), (5, 1), prev)
        stats = self.apply(prev["token"])
        people = {r["given_names"]: r for r in sql("SELECT * FROM people WHERE deleted_at IS NULL")}
        self.assertEqual(people["Kamala"]["birth_surname"], "Subramanian")
        self.assertEqual(people["Lakshmi"]["nickname"], "Lucky")
        self.assertEqual(people["Raman"]["biography"], "Loved trains and long walks.\nGrew mangoes.")
        self.assertTrue(people["Raman"]["deceased"])
        ev = {(r["person_id"], r["type"]): r for r in sql("SELECT * FROM events WHERE person_id IS NOT NULL")}
        raman = people["Raman"]["id"]
        self.assertEqual((ev[(raman, "birth")]["date_text"], ev[(raman, "birth")]["place"]), ("ABT 1931", "Thanjavur, Tamil Nadu, India"))
        self.assertEqual(ev[(raman, "occupation")]["title"], "Railway engineer")
        kamala = ev[(people["Kamala"]["id"], "birth")]
        self.assertEqual((kamala["date_d"], kamala["date_m"], kamala["date_y"]), (12, 3, None))
        self.assertEqual(ev[(people["Lakshmi"]["id"], "birth")]["date_y"], 1960)
        self.assertIn("Date: someday", ev[(people["Gopi"]["id"], "birth")]["description"])
        rel = {r["child_id"]: r["relation"] for r in sql("SELECT * FROM family_children")}
        self.assertEqual((rel[people["Lakshmi"]["id"]], rel[people["Suresh"]["id"]], rel[people["Gopi"]["id"]]),
                         ("birth", "adopted", "step"))
        fam = sql("SELECT * FROM families WHERE deleted_at IS NULL")[0]
        self.assertEqual(fam["kind"], "married")
        self.assertEqual(sql("SELECT date_y, place FROM events WHERE family_id = ? AND type = 'marriage'", (fam["id"],))[0]["place"], "Madurai")
        self.b.load()
        self.ok(self.post(f"/api/history/{stats['batchId']}/undo", user=ADMIN))      # one batch: all of it goes
        self.assertEqual(self.names_b(), [])

    def test_not_gedcom(self):
        self.assertFalse(gedcom.looks_like(b"hello"))
        self.assertTrue(gedcom.looks_like("﻿0 HEAD\n".encode("utf-8")))
        self.assertTrue(gedcom.looks_like("0 HEAD\r\n".encode("utf-16")))
        bad = self.upload(b"0 HEAD\n1 CHAR UTF-8\n0 TRLR\n", name="empty.ged", status=422)
        self.assertIn("nobody in it", bad["detail"])
        bad = self.upload(b"0 HEAD\n1 SOUR X\n3 NAME skipped a level\n0 TRLR\n", name="bad.ged", status=422)
        self.assertIn("wrong level", bad["detail"])


class GedcomText(unittest.TestCase):
    """Line breaks and long values, without a tree (gedcom.write / to_tree_data on their own)."""

    def tree(self, bio):
        return {"source": {"id": "src", "title": "T"}, "families": [],
                "people": [{"id": "p1", "given_names": "Ann", "surname": "Rao", "gender": "female", "deceased": False,
                            "biography": bio, "events": [], "stories": [], "other_names": [], "nickname": None,
                            "birth_surname": None, "photo": None, "limited": False}]}

    def round_trip(self, bio):
        text = gedcom.write(self.tree(bio))
        for line in text.split("\n"):
            self.assertLessEqual(len(line), 255)
        back = tree_data.validate(gedcom.to_tree_data(text.encode()))
        return back["people"][0]["biography"]

    def test_odd_line_breaks_survive_a_round_trip(self):
        bio = "First line second third\x0bfourth\x0cfifth\x1csixth\x85seventh"
        self.assertEqual(self.round_trip(bio), "First line\nsecond\nthird\nfourth\nfifth\nsixth\nseventh")

    def test_reader_splits_only_at_cr_and_lf(self):
        raw = ("0 HEAD\r\n1 CHAR UTF-8\r\n0 @I1@ INDI\r\n1 NAME Ann /Rao/\r\n"
               "1 NOTE one still one\x85and still\r2 CONT two\n0 TRLR\n").encode()
        p = tree_data.validate(gedcom.to_tree_data(raw))["people"][0]
        self.assertEqual(p["biography"], "one still one\x85and still\ntwo")

    def test_long_values_join_in_linear_time(self):
        import time
        lines = ["0 HEAD", "1 CHAR UTF-8", "0 @I1@ INDI", "1 NAME Ann /Rao/", "1 NOTE start"]
        lines += ["2 CONC " + "x" * 4 for _ in range(150_000)]
        lines.append("0 TRLR")
        t0 = time.monotonic()
        records = gedcom.parse("\n".join(lines))
        self.assertLess(time.monotonic() - t0, 5)
        note = records[1].first("NOTE").value
        self.assertEqual(len(note), len("start") + 4 * 150_000)

    def test_caps(self):
        too_long = "0 HEAD\n0 @I1@ INDI\n1 NOTE a\n" + "2 CONC " + "y" * 200 + "\n"
        with self.assertRaises(gedcom.GedcomError):
            gedcom.parse(too_long + ("2 CONC " + "y" * 200 + "\n") * (gedcom.MAX_VALUE // 200 + 2))
        with unittest.mock.patch.object(gedcom, "MAX_LINES", 10):
            with self.assertRaises(gedcom.GedcomError) as e:
                gedcom.parse("0 HEAD\n" + "1 NOTE x\n" * 20)
            self.assertIn("too long", str(e.exception))
        with unittest.mock.patch.object(tree_data, "MAX_PEOPLE", 3):
            with self.assertRaises(gedcom.GedcomError) as e:
                gedcom.parse("0 HEAD\n" + "".join(f"0 @I{i}@ INDI\n1 NAME P{i}\n" for i in range(5)))
            self.assertIn("at most 3 people", str(e.exception))
