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
        # Load more redraws the list (the month headers' totals take in the new rows) through ledgerItems
        load_more = self.js[self.js.index("async function loadMoreLedger"):]
        load_more = load_more[:load_more.index("\n}\n")]
        self.assertIn("renderExpenseList()", load_more)
        render = self.js[self.js.index("function renderExpenseList"):]
        render = render[:render.index("\n}\n")]
        self.assertIn("ledgerItems(g.expenses)", render)
        items = self.js[self.js.index("function ledgerItems"):]
        items = items[:items.index("\n}\n")]
        self.assertIn("out.push(ledgerRow(e))", items)

    def test_running_balances_asked_for_only_while_ticked(self):
        # the server works the balances out only for balances=1; every ledger request goes through ledgerQuery
        self.assertIn("state.showRunning ? '&balances=1' : ''", self.js)
        self.assertEqual(len(re.findall(r"/ledger\?\$\{ledgerQuery\(", self.js)), 2)    # Load more and the top-up
        self.assertNotIn("?limit=${ledgerLimit", self.js)

    def test_reload_keeps_every_loaded_entry_past_the_largest_page(self):
        # after a change the list reloads as many entries as were showing, in pages of at most LEDGER_MAX
        top_up = self.js[self.js.index("async function topUpLedger"):]
        top_up = top_up[:top_up.index("\n}\n")]
        self.assertIn("while (g.nextCursor && g.expenses.length < want)", top_up)
        self.assertIn("Math.min(LEDGER_MAX, limit)", self.js)
        self.assertIn("return Math.max(LEDGER_PAGE, shown);", self.js)

    def test_answers_for_a_group_left_meanwhile_are_dropped(self):
        for name in ("async function refreshGroup", "async function changeGroup"):
            body = self.js[self.js.index(name):]
            body = body[:body.index("\n}\n")]
            self.assertIn("if (state.currentGroupId !== id) return", body, name)
        settle = self.js[self.js.index("function recordPayment"):]
        settle = settle[:settle.index("\n}\n")]
        self.assertIn("await changeGroup(", settle)
        self.assertNotIn("state.currentGroup = await", settle)

    def test_default_toggle_reports_errors_and_counts_agree(self):
        toggle = self.js[self.js.index("async function toggleGroupDefault"):]
        toggle = toggle[:toggle.index("\n}\n")]
        self.assertIn("catch (err)", toggle)
        self.assertIn("toast(err.message, true)", toggle)
        self.assertIn("${entryCounts(g)}", self.js)                       # the Groups list
        delete = self.js[self.js.index("$('#groupDeleteBtn').addEventListener"):]
        self.assertIn("entryCounts(g)", delete[:delete.index("\n});\n")])   # and the delete confirmation

    def test_settle_row_wraps_on_phones(self):
        block = self.css[self.css.index(".settle-row {"):]
        self.assertIn("flex-wrap: wrap", block[:block.index("}")])
        self.assertRegex(self.css, r"@media \(max-width: 760px\) \{\s*\.settle-row \.settle-btn \{ flex: 1 0 100%")


if __name__ == "__main__":
    unittest.main()
