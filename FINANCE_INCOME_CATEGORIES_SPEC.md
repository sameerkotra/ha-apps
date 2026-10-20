# Finance Dashboard — income categories

Status: **spec only, nothing built; decisions made (§9). The redesigned Categories page (§10) is part of it.** Money coming in gets its own categories, such as Salary, Interest or
Refunds, alongside the spending ones. People can add their own, and Home, Compare, reports and the Household
Assistant break income down by them. The Categories page is redesigned around both kinds (§10). The decisions are in §9. §10 is the redesigned Categories page, ideas A–G, all in scope.

---

## 1. How it is now

- **One flat list.** Categories are a single list: the built-in `DISPLAY_CATEGORIES` plus each person's own
  (`custom_categories`). "Income" is just one name in it, next to Groceries and Dining.
- **What counts as income.** Home counts as income any clean money **in** on a checking or savings account,
  leaving out transfers and excluded rows. It does this whatever the row's category is, and shows one total
  with no breakdown.
- **The pickers.** A paycheck, an interest payment and a refund all end up as "Income", "Other" or
  "Uncategorized". The category menu offers spending names like Groceries for a deposit.

## 2. What changes

Every category gets a **kind**: **spending** or **income**. Kinds are fixed for the built-in categories, and
chosen by the person for their own.

| | Built-in, spending | Built-in, income (new) |
|---|---|---|
| Names | Groceries, Dining, Transportation, Shopping, Subscriptions, Utilities, Gas, Health, Entertainment, Travel, Housing, Other | **Salary**, **Interest**, **Dividends**, **Refunds**, **Reimbursements**, **Gifts received**, **Other income** |
| Neither kind | Payments (card payments), Uncategorized |

- **The old "Income" category** becomes **Other income**. Every existing row with category `Income` is renamed in
  the migration, along with the rules that set it, so nothing is lost and old totals don't change.
- **Payments** stays as it is (a card payment, neither spending nor income). **Uncategorized** stays the
  "not decided" state for both kinds.
- **Splitting income from spending doesn't depend on the category.** Whether a row counts as income or spending
  is still decided by direction and account, as today. The kind only decides which **list** a row's category
  comes from, and how the income is broken down. A row with a spending category on money in, or the other way
  round, is allowed, because a person may want that; the pickers just don't suggest it (§4).

## 3. Adding your own (Setup → Categories)

- **Add a category** gets a **Kind** choice: **Spending** (the default, as today) or **Income**. Examples of
  income: "Rental income", "Freelance", "Child benefit".
- **The Categories page** shows two lists, **Spending** and **Income**, each with its built-ins and the person's
  own. A custom category can be deleted as today, and its kind can be changed. Its rows keep the name; only the
  list it shows under changes.
- **Duplicates:** the same name can't be both kinds, and the case-insensitive duplicate check covers both lists,
  so "Salary" can't be added again as a spending category.
- **Rules** (the "add a rule" form) work for income too, for example "ACME PAYROLL" → Salary. The category menu
  in that form shows both lists, grouped under **Spending** and **Income**.

## 4. Choosing a category (every category menu)

- **Grouped menus:** each menu (a transaction's category, bulk categorize, Review, Add by hand, the rule form) is
  grouped into **Spending** and **Income**.
- **Order by direction:** for money in on a checking or savings account, **Income comes first** and Spending
  second. For everything else (money out, and every card row), Spending comes first, then Income.
- **Card refunds:** money back on a card counts as **spending going down**, as today. Its menu still lists
  Refunds under Income, but the dashboards keep treating it as a negative spend (§6).
- **Bulk categorize:** a selection mixing directions shows Spending first.

## 5. Automatic categorization

- **Rules first:** a person's rules, then built-in rules, now including a few for income:
  - `PAYROLL`, `DIRECT DEP`, `DIR DEP`, `SALARY` → **Salary**
  - `INTEREST PAID`, `INTEREST EARNED`, `INT PAID` → **Interest**
  - `DIVIDEND` → **Dividends**
  - `REFUND` → **Refunds**
  - money in containing `ZELLE FROM` / `VENMO FROM` is left for the AI, since it could be a gift, a
    reimbursement or rent.
- **The AI**, for rows no rule matched, is offered **only the list for the row's direction**: money in on
  checking/savings gets the income categories, everything else the spending ones. That gives better guesses and
  a shorter prompt.
- **Cards:** money in on a card is a payment ("Payments"), as today, or a refund. Card refunds get **Refunds**
  when a rule matches; the AI is offered the spending list plus Refunds.
- **Rows already stored are not re-categorized automatically.** The Categories page gets a one-time **"Sort my
  income"** button. It runs rules and then the AI over money-in rows that are "Other income", "Income" or
  "Uncategorized", lists what it would change, and asks before changing anything.

## 6. Where income is shown

- **Home (Overview):** the **Income** total gains a breakdown, the same shape as By category for spending: each
  income category with its amount and share, largest first. Tapping one opens Transactions filtered to it.
- **Compare:** income by category, month against month, beside spending.
- **Transactions:** the category filter is grouped Spending / Income. A new quick filter **All income** shows
  money in on checking and savings.
- **Reports / Query tab:** `categories` (the merged list) gains a `kind` column, so reports can group by it. The
  query description says what it means.
- **Recurring charges:** unchanged; money out only.
- **Household Assistant:**
  - `finance.summary` gains the month's **top income categories** after the spending ones, for example "Income
    5,200.00: Salary 4,800.00, Interest 12.40, Refunds 387.60".
  - A new read-only **`finance.income`** (args `month`, `category`, `person`) answers "How much did I earn in
    September?" and "How much interest did we get this year?". It uses the same rules as the other Finance
    tools.
  - The quick words for `finance.income` are chosen so no existing phrase changes its answer (checked against
    the Assistant's phrase corpus, as in 1.11.0).

## 7. Data

Migration **0032**, assuming 0031 is the latest:

- `custom_categories.kind` TEXT NOT NULL DEFAULT `'spending'` CHECK (`'spending'`, `'income'`).
- The built-in kinds live in code (`categorize.INCOME_CATEGORIES`), not the database.
- `UPDATE transactions SET category = 'Other income' WHERE category = 'Income'`. The same for `category_rules`,
  `skipped_duplicates.category`, recurring overrides and any other column that stores a category name (search for
  them all).
- `list_all_categories(conn, user)` keeps its signature for anything that just needs names. A new
  `categories_by_kind(conn, user)` returns `{"spending": [...], "income": [...]}` for the grouped menus.
- **One person's data:** `kind` travels with `custom_categories`. The export schema goes up by one. A file from
  before this change imports with every custom category as spending, and "Income" rows renamed to "Other income".

## 8. Tests

**Migration:**
- "Income" rows, rules and skipped rows become "Other income";
- totals on Home are the same before and after;
- custom categories default to spending.

**Categories page:**
- add a category of each kind, and change a kind;
- the duplicate check works across both kinds, case-insensitively.

**Menus:**
- grouped Spending / Income everywhere;
- Income comes first for money in on checking or savings, Spending first for everything else, including card
  rows.

**Categorizing:**
- the new built-in income rules;
- the AI is offered only the right list, which a test checks in the prompt;
- card refunds get Refunds and still count as negative spend;
- "Sort my income" lists before it changes anything, and changes only what's confirmed.

**Home and Compare:**
- the income breakdown adds up to the income total;
- a spending category on a deposit still counts as income;
- Compare shows income by category.

**Assistant:**
- `finance.summary`'s income line;
- `finance.income` with a month and with a category;
- sharing and the switches;
- no existing quick phrase changes.

**Data:** person-data export and import with `kind`; an older file imports correctly.

## 9. Decided

1. **Categories only:** one per transaction, as spending works today. No tags.
2. **The built-in income list** stays as in §2: Salary, Interest, Dividends, Refunds, Reimbursements, Gifts
   received, Other income. People add more themselves (§3), and **rules work for income exactly as they do for
   spending**: the same form, the same matching, the same "also re-categorize existing transactions".
3. **"Income" is renamed "Other income"**, with its rows and rules (§2, §7).
4. **"Sort my income" uses the rules and then the AI** for what no rule matched (§5). It always lists the
   changes and asks first.

## 10. The redesigned Categories page (all of A–G)

How it is now (`categories.html`, one long page):
- a paragraph about how categorizing works;
- Your categories: an add form and a table of added ones;
- Add a rule;
- Your rules, grouped by category;
- Built-in rules, as reference.

There's no way to rename or edit a category or a rule, nothing shows how much each category is used, and with
income added it would get longer still. **All of A–G below are built as part of this spec.** §3's "Add a
category" and its two lists become tab A's lists and B's Add button.

**A. Tabs: Spending · Income · Rules**
- **Spending** and **Income** each list that kind's categories, built-in and the person's own together.
- **Rules** holds every rule, the person's and the built-ins (§D).
- The "how categorizing works" paragraph becomes one line with a "How this works" link opening the detail.

**B. A category list with what matters per category**
- One row per category: its name, a colour dot, **this month's total**, **transactions in the last 12 months**,
  **rules pointing to it**, and a ⋯ menu.
- Sort by name, by this month's total or by use. A built-in category says "Built in" instead of a delete button.
- Tapping a row opens Transactions filtered to that category.
- **Add a category** is a button at the top of each tab, opening a small form. The kind follows the tab, with a
  way to switch.

**C. Rename, merge, move and hide** (the ⋯ menu)
- **Rename:** the person's own categories; every row and rule using it follows.
- **Merge into…:** move all its rows and rules into another category, then delete it. This works for built-ins
  too, into a category of the same kind.
- **Move to Income / Spending:** change the kind of the person's own category.
- **Hide from menus:** for built-ins someone never uses (Gas, Travel). Hidden categories stay valid on old rows,
  but the menus stop offering them. **Show hidden** brings them back.
- **Delete:** the person's own, as today, now asking where its rows should go (Uncategorized by default).

**D. One rules table you can edit**
- Every rule in one searchable table: pattern, match type (exact / starts with / contains), category, kind,
  **how many transactions it matches**, and when it last matched.
- **Filter chips:** Mine · Built in · Spending · Income · one category.
- **Edit in place:** change the pattern, match type or category without deleting and re-adding.
- **Test before saving:** typing a pattern shows the first 10 transactions it would match and how many would
  change category. "Also re-categorize existing transactions" shows that number too ("will change 14").
- **Built-in rules** are listed with a "Built in" badge. **Use my own instead** copies one into the person's rules
  to change, and the person's rule then wins, as today.

**E. Suggestions from your own corrections**
- When someone sets the same merchant to the same category by hand two or three times, the page offers a rule:
  "You set AMAZON MKTPL to Shopping 3 times — make it a rule?", with **Make rule** and **Dismiss**.
- Rows still **Uncategorized** show as a count at the top ("23 uncategorized → sort them"), linking to the
  Transactions filter. For income, the count links to **Sort my income** (§5).

**F. Colours, used by the charts**
- Each category can have a colour, from a small fixed palette that reads well in every theme. Home's and
  Compare's charts use it, so "Dining" is the same colour everywhere.
- Built-ins start with sensible colours; spending and income have separate ranges.

**G. Phone**
- The tabs stay at the top. Category and rule rows become cards (the app's existing phone table pattern).
- Add and Edit open as full-width sheets with big touch targets, and the ⋯ menu becomes a bottom list.
- No sideways scrolling at 390 px.

**Build order:** A + B + D, then C, then E, F and G, all in the same release. G partly comes with A.

**Data the redesign needs** (in the same migration as §7):
- `custom_categories` gains `colour`;
- a per-person `category_prefs` table: hidden built-ins and built-in colours;
- `category_rules.last_matched_at`, updated when a rule categorizes a row;
- suggestions are worked out from `category_source = 'manual'` rows, never stored ahead of time.

Rename, merge and kind changes update every column that stores a category name, the same list as §7's migration,
in one transaction.

**Pages and addresses:**
- `categories?tab=spending|income|rules` (default spending). Without the script the tabs are plain links, and
  every action is a plain form that redirects back to the same tab.
- **New routes:**
  - `category-rename`, `category-merge`, `category-kind`, `category-hide` / `category-show`, `category-colour`;
  - `category-rule-edit`;
  - `category-rule-test`, which returns the first 10 matches and the count;
  - `category-rule-copy` ("Use my own instead");
  - `category-suggestion-make` / `category-suggestion-dismiss`.
  
  Dismissed suggestions are stored per person, keyed by merchant and category.
- **Today's routes keep working:** `custom-categories`, `custom-category-delete`, `category-rules`,
  `category-rule-delete`.

## 11. Tests for the redesigned page

**A. Tabs:**
- each tab lists the right categories and rules;
- the tabs and every form work without the script;
- "How this works" opens.

**B. The category list:**
- this month's total, the 12-month count and the rules count are right;
- the three sorts;
- a row opens Transactions filtered to that category;
- Add on each tab gets the kind right.

**C. The ⋯ menu:**
- **Rename:** rows, rules, skipped rows and recurring overrides all follow.
- **Merge:** into the same kind only; a built-in can be merged away.
- **Move:** changing a kind moves the category to the other list without touching its rows.
- **Hide:**
  - a hidden built-in is gone from every menu but stays on old rows;
  - Show hidden brings it back;
  - a rule that points to a hidden category still works.
- **Delete:** asks where the rows go; Uncategorized is the default.

**D. The rules table:**
- the search and filter chips;
- editing a rule in place changes only that rule;
- **Test** returns the right 10 and the right count, and saves nothing;
- the "will change N" figure matches what saving does;
- **Use my own instead** copies a built-in rule, and the copy wins;
- `last_matched_at` updates when a rule categorizes a row.

**E. Suggestions:**
- a suggestion appears after 3 manual changes of one merchant to one category, and not after 2;
- **Make rule** creates the rule;
- **Dismiss** hides it for good for that merchant and category;
- the Uncategorized count, and its link to **Sort my income** on the Income tab.

**F. Colours:**
- a colour is saved and used by Home's and Compare's charts;
- every colour in the palette reads well against both themes, which a test checks by contrast;
- built-ins have their default colours.

**G. Phone:** at 390 px the rows are cards, nothing scrolls sideways, and add and edit open as full-width
sheets. I'll check this in a browser as well.

**Data:**
- the migration adds `colour`, `category_prefs`, `last_matched_at` and the dismissed-suggestions table;
- one person's data export and import carry colours, hidden built-ins and dismissed suggestions.
