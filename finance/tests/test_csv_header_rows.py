"""CSV import with lines above the header (SPEC.md section 10): many bank exports start with a few lines about
the account and the period ("Name:", "Card:", "Time period of report:") before the real header row. The header is
found among the top rows by itself; the person can also say which row is the header and where the data starts,
and when it can't be found they're shown the first rows to choose from."""
import io

BANK = ("Name:,name,,,\n"
        "Card:,card_number,,,\n"
        ",,,,\n"
        "Time period of report:,\"Jan. 01, 2026 (09:00 AM) to Oct. 19, 2026 (09:00 AM)\",,,\n"
        ",,,,\n"
        "Date,Description,Debit,Credit,Category\n"
        "01/05/2026,COFFEE SHOP,4.50,,Food\n"
        "01/06/2026,PAYMENT THANK YOU,,100.00,\n")


def _post(env, acct, text, **fields):
    data = {"default_account_id": str(acct), **{k: str(v) for k, v in fields.items()}}
    r = env.post("transactions-import-csv", data=data, headers={"accept": "application/json"},
                 files={"files": ("bank.csv", io.BytesIO(text.encode()), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def _rows(env, acct):
    with env.db() as c:
        return [(r["date"], r["amount"], r["description"]) for r in c.execute(
            "SELECT date, amount, description FROM transactions WHERE account_id = ? AND deleted_at IS NULL "
            "ORDER BY date", (acct,))]


def test_the_header_is_found_below_the_banks_own_lines(env):
    card = env.account("Visa", "credit_card")
    r = _post(env, card, BANK)
    assert (r["fatal_error"], r["imported"], r["error_count"], r["needs_rows"]) == (None, 2, 0, False)
    assert _rows(env, card) == [("2026-01-05", 4.5, "COFFEE SHOP"), ("2026-01-06", -100.0, "PAYMENT THANK YOU")]


def test_chosen_header_and_data_rows(env):
    card = env.account("Visa", "credit_card")
    # data from row 8: the coffee on row 7 is left out on purpose
    r = _post(env, card, BANK, header_row=6, data_row=8)
    assert (r["fatal_error"], r["imported"]) == (None, 1)
    assert _rows(env, card) == [("2026-01-06", -100.0, "PAYMENT THANK YOU")]


def test_row_numbers_in_errors_are_the_files_own(env):
    card = env.account("Visa", "credit_card")
    r = _post(env, card, BANK + "01/07/2026,BOTH,1.00,2.00,\n")
    assert r["error_count"] == 1
    with env.db() as c:
        errors = c.execute("SELECT row_errors FROM csv_imports WHERE id = ?", (r["csv_import_id"],)).fetchone()[0]
    assert "Row 9:" in errors


def test_a_wrong_header_row_shows_the_first_rows_to_choose_from(env):
    card = env.account("Visa", "credit_card")
    r = _post(env, card, BANK, header_row=2)
    assert r["imported"] == 0 and r["needs_rows"]
    assert "row 2" in r["fatal_error"]
    assert [p["row"] for p in r["preview"]][:6] == [1, 2, 3, 4, 5, 6]
    assert r["preview"][5]["cells"] == ["Date", "Description", "Debit", "Credit", "Category"]
    # then the same file with the right rows
    r = _post(env, card, BANK, header_row=6, data_row=7)
    assert r["imported"] == 2


def test_no_header_anywhere_asks_for_the_rows(env):
    acct = env.account("Checking")
    r = _post(env, acct, "Report\nfoo,bar\n1,2\n")
    assert r["needs_rows"] and r["imported"] == 0 and r["preview"][0] == {"row": 1, "cells": ["Report"]}


def test_impossible_rows_are_refused_plainly(env):
    acct = env.account("Checking")
    r = _post(env, acct, BANK, header_row=40)
    assert "only" in r["fatal_error"] and r["imported"] == 0
    r = _post(env, acct, BANK, header_row=6, data_row=5)
    assert "below the header" in r["fatal_error"] and r["imported"] == 0
    # junk in the fields is "work it out"
    assert _post(env, acct, BANK, header_row="x", data_row="")["imported"] == 2


def test_an_ordinary_file_is_unchanged(env):
    acct = env.account("Checking")
    r = _post(env, acct, "Date,Description,Amount\n2026-08-01,LUNCH,-9.00\n\n2026-08-02,DINNER,-12.00\n")
    assert (r["fatal_error"], r["imported"], r["needs_rows"]) == (None, 2, False)


def test_the_page_has_the_row_fields(env):
    page = env.get("uploads").text
    for needle in ('name="header_row"', 'name="data_row"', 'id="csv-rows-show"'):
        assert needle in page
