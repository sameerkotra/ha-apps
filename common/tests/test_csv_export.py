"""Tests for the shared CSV writer (common/python/csv_export.py)."""
import csv
import io
import unittest

from app.common import csv_export


class CsvExportTests(unittest.TestCase):
    def test_guard_only_touches_text_that_starts_a_formula(self):
        for value in ("=SUM(A1)", "+1 555", "-x", "@me", "\tx", "\rx"):
            self.assertEqual(csv_export.guard(value), "'" + value)
        for value in ("plain", " =space first", "", "'quoted"):
            self.assertEqual(csv_export.guard(value), value)
        for value in (-12.5, 3, None, 0.0):
            self.assertIs(csv_export.guard(value), value)       # numbers stay numbers
        self.assertEqual(csv_export.text(None), "")
        self.assertEqual(csv_export.text(-3), "'-3")            # text() makes text first: use it for text only

    def test_file_matches_csv_writer(self):
        header = ["Date", "Name, with comma", 'Quote "q"']
        rows = [["2026-09-21", "Ünïcödé", -12.5], ["", None, "line\nbreak"]]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        self.assertEqual(csv_export.to_text(header, rows), buf.getvalue())
        self.assertEqual(csv_export.to_text(header, rows, bom=True), "﻿" + buf.getvalue())
        self.assertEqual(csv_export.to_bytes(header, rows, bom=True), buf.getvalue().encode("utf-8-sig"))
        self.assertEqual(list(csv_export.stream(header, rows))[1], "2026-09-21,Ünïcödé,-12.5\r\n")
        self.assertEqual(csv_export.to_text(None, [], bom=True), "﻿")
        self.assertEqual(csv_export.to_text(None, iter([["a"]])), "a\r\n")


if __name__ == "__main__":
    unittest.main()
