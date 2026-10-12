"""The ledger page's script and styles, read as text (the behaviour itself was checked in a browser):
every path that draws ledger rows adds the running balances, and the settle-up row fits a phone."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


class LedgerUI(unittest.TestCase):
    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_every_ledger_row_goes_through_ledger_row(self):
        # Load more once drew bare expenseRow()s, so its rows had no balances under them.
        calls = re.findall(r"\bexpenseRow\(", self.js)
        self.assertEqual(len(calls), 2, "expenseRow should be defined once and called only from ledgerRow")
        self.assertIn("const row = expenseRow(e);", self.js)
        load_more = self.js[self.js.index("async function loadMoreLedger"):]
        load_more = load_more[:load_more.index("\n}\n")]
        self.assertIn("ledgerRow(e)", load_more)

    def test_settle_row_wraps_on_phones(self):
        block = self.css[self.css.index(".settle-row {"):]
        self.assertIn("flex-wrap: wrap", block[:block.index("}")])
        self.assertRegex(self.css, r"@media \(max-width: 640px\) \{\s*\.settle-row \.settle-btn \{ flex: 1 0 100%")


if __name__ == "__main__":
    unittest.main()
