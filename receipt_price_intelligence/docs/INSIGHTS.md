# Insights

## Categories and budgets

Every item has an optional category. A new item gets a starting guess from its name (`app/services/categories.py`: whole-word matching, longest phrase wins, so "peanut butter" is Pantry and "orange juice" is Beverages); **Fill in automatically** on the Categories tab does the same for existing items, and you can change any item's category from its detail. Spend per category is the sum of line totals of priced lines, compared with the period before.

A **budget** is a monthly limit for one category, or for all spending (which uses receipt totals, including tax). For the current month it shows spent, remaining, percent used and the month-end projection at the current pace. Status is `over` once spent exceeds the budget; `warn` from 80% used, or (after the first week) when the pace would overshoot by more than 10%. An alert is raised once per budget, per month, at 80% and again when it is exceeded.

## Sales and coupons

Savings are the `discount_amount` recorded on lines (a sale, coupon or member price attached to the item above it) plus any line of its own with a negative total. The rate is the saving as a share of what those lines would have cost without it; the share of spend compares it with total spend plus savings.

## Personal price index

How much the things you keep buying cost now compared with a year ago. Only items bought in at least three different months count (and at least three such items are needed). For each month, each item's average price (in one consistent unit, after pack sizes are put on a per-size basis) is compared with the month before over the items present in both, weighted by how much you spend on each; the monthly changes are chained into an index starting at 100. It follows your own basket, so it reflects your prices, not a national average, and it can move because you switched stores.

## Learning from corrections

When you fix how a line was read (for example "GV 2PCT MLK" to "GV 2% MILK 1GAL") and save the receipt, the fix is remembered for that store chain, keyed on the normalized original text. The next receipt from that store with the same misread line is corrected automatically, and the line shows what it was originally read as. If you later change a line back to what was read, the remembered fix is dropped.

## Tags and export

Receipts can carry tags (business, reimbursable, warranty, or your own; up to 12 of 30 characters). Tags can be changed on saved receipts without reopening them, and the saved list can be filtered by tag. **Export** on the Insights page downloads CSV for receipts (one row each) or every line, optionally between two dates and for one tag. Cells that start with `=`, `+`, `-` or `@` are prefixed with an apostrophe so a spreadsheet never runs them as formulas.
