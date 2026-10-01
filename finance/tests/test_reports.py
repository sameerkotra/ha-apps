"""Storage → Query tab and Reports (SPEC.md section 19)."""
import re
import time
from datetime import date

import pytest

OTHER = {"x-remote-user-id": "other", "x-remote-user-name": "Other"}


def run(env, sql, **form):
    return env.post("query-run", data={"sql": sql, "per_page": "100", **form})


def seed_two_users(env):
    a = env.account("Mine")
    b = env.account("Theirs", user="other")
    env.txn(a, "2026-09-01", -4.5, "COFFEE", category="Dining")
    env.txn(b, "2026-09-01", -99.0, "SECRET", user="other", category="Dining")
    gone = env.txn(a, "2026-09-02", -7.0, "DELETED ROW")
    with env.db() as c:
        c.execute("UPDATE transactions SET deleted_at = datetime('now') WHERE id = ?", (gone,))
    old = env.account("Closed")
    env.txn(old, "2026-09-03", -8.0, "OF A DELETED ACCOUNT")
    with env.db() as c:
        c.execute("UPDATE accounts SET deleted_at = datetime('now') WHERE id = ?", (old,))
    return a, b


# ---- access ----------------------------------------------------------------------------------

def test_query_tab_is_admin_only_and_reports_are_for_everyone(env):
    assert env.get("admin-storage?tab=query").status_code == 200
    assert 'class="subtab active">Storage' in env.get("admin-storage").text.replace("\n", "")  or \
        "subtab active" in env.get("admin-storage").text
    for method, url in [("get", "admin-storage?tab=query"), ("post", "query-run"), ("post", "query-vars"),
                        ("post", "query-view"), ("get", "query-csv"), ("post", "query-save"),
                        ("post", "report-delete?id=1"), ("post", "report-duplicate?id=1"), ("post", "reports-check")]:
        r = getattr(env, method)(url, headers=OTHER)
        assert r.status_code == 403, url
    page = env.get("reports", headers=OTHER).text
    assert 'href="reports' in page and "admin-storage" not in page
    assert "Spending by category" in page and ">Edit<" not in page and "Check reports" not in page
    r = env.get("report?id=1", headers=OTHER)
    assert r.status_code == 200 and "q-table" in r.text or "No rows" in r.text
    assert "SELECT" not in r.text          # users never see a report's SQL


def test_storage_tab_is_default(env):
    page = env.get("admin-storage").text
    assert re.search(r'class="subtab active"[^>]*>Storage<', page) or re.search(r'subtab active">Storage', page)


# ---- scoping and refusals ----------------------------------------------------------------------

def test_runs_see_only_the_acting_users_live_rows(env):
    seed_two_users(env)
    r = run(env, "SELECT description FROM transactions ORDER BY id").text
    assert "COFFEE" in r and "SECRET" not in r and "DELETED ROW" not in r and "OF A DELETED ACCOUNT" not in r
    r = env.post("query-run?as_user=other", data={"sql": "SELECT description FROM transactions", "per_page": "100"}).text
    assert "SECRET" in r and "COFFEE" not in r
    r = run(env, "SELECT * FROM transactions").text
    assert "user_id" not in r and "deleted_at" not in r
    r = run(env, "SELECT name FROM accounts").text
    assert "Mine" in r and "Theirs" not in r and "Closed" not in r


def test_non_admin_report_run_ignores_as_user(env):
    seed_two_users(env)
    env.post("query-save", data={"sql": "SELECT description FROM transactions", "name": "All rows"})
    with env.db() as c:
        rid = c.execute("SELECT id FROM saved_reports WHERE name = 'All rows'").fetchone()[0]
    r = env.get(f"report?id={rid}&as_user=tester", headers=OTHER).text
    assert "SECRET" in r and "COFFEE" not in r


def test_meter_details_scoped_through_their_bill(env):
    mine = env.insert("utility_bills", user_id="tester", utility_type="electric", provider="Xcel Energy", status="complete")
    theirs = env.insert("utility_bills", user_id="other", utility_type="electric", provider="Xcel Energy", status="complete")
    env.insert("electric_meter_details", utility_bill_id=mine, net_total_kwh=111)
    env.insert("electric_meter_details", utility_bill_id=theirs, net_total_kwh=999)
    r = run(env, "SELECT net_total_kwh FROM electric_meter_details").text
    assert "111" in r and "999" not in r


@pytest.mark.parametrize("sql", [
    "DELETE FROM transactions", "UPDATE transactions SET amount = 0", "INSERT INTO accounts (name) VALUES ('x')",
    "PRAGMA table_info(transactions)", "ATTACH DATABASE 'x.db' AS x", "CREATE TABLE t (a)", "DROP TABLE transactions",
    "SELECT 1; SELECT 2", "SELECT * FROM sqlite_master", "SELECT * FROM known_users", "SELECT * FROM saved_reports",
    "SELECT load_extension('x')", "SELECT * FROM pragma_table_info('transactions')",
])
def test_refused(env, sql):
    seed_two_users(env)
    r = run(env, sql).text
    assert 'class="q-error"' in r


def test_cte_named_like_a_table_cannot_reach_other_users(env):
    seed_two_users(env)
    r = run(env, "WITH transactions AS (SELECT * FROM main.transactions) SELECT description FROM transactions").text
    assert "SECRET" not in r


def test_variable_values_are_bound_not_pasted(env):
    seed_two_users(env)
    r = run(env, "SELECT description FROM transactions WHERE description = :merchant",
            v_merchant="x' OR '1'='1", var_names="merchant", d_merchant_type="text").text
    assert "COFFEE" not in r and "No rows" in r


def test_every_table_is_registered_or_hidden(env):
    from app import query_engine as qe
    with env.db() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables - set(qe.REGISTRY) - qe.HIDDEN_TABLES == set()


def test_time_limit_stops_a_runaway_query(env, monkeypatch):
    from app import query_engine as qe
    monkeypatch.setattr(qe, "TIME_LIMIT_SECONDS", 0.3)
    r = run(env, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n").text
    assert "was stopped" in r


def test_row_cap(env, monkeypatch):
    from app import query_engine as qe
    monkeypatch.setattr(qe, "MAX_ROWS", 50)
    r = run(env, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 80) SELECT i FROM n").text
    assert "Cut at 50 rows" in r


# ---- results: paging, sorting, totals, long text, CSV --------------------------------------------

def _numbers_sql(n):
    return f"WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < {n}) SELECT i AS id, i * 1.5 AS amount FROM n"


def test_pagination_sort_and_totals_cover_the_whole_result(env):
    r = run(env, _numbers_sql(653)).text
    assert "Page 1 of 7" in r and "rows 1&ndash;100 of 653" in r
    rid = re.search(r'&#34;rid&#34;: &#34;([\w-]+)&#34;', r).group(1)
    r2 = env.post("query-view", data={"rid": rid, "page": "7", "per_page": "100", "sort": "", "dir": "asc"}).text
    assert "rows 601&ndash;653 of 653" in r2
    total = round(sum(i * 1.5 for i in range(1, 654)), 2)
    assert f"{total:,.2f}" in r and f"{total:,.2f}" in r2      # same totals on every page
    r3 = env.post("query-view", data={"rid": rid, "page": "1", "per_page": "250", "sort": "1", "dir": "desc"}).text
    assert "Page 1 of 3" in r3 and r3.index("979.50") < r3.index("978.00")
    r4 = env.post("query-view", data={"rid": rid, "page": "1", "per_page": "all", "sort": "", "dir": "asc"}).text
    assert "Page 1 of 1" in r4 and "rows 1&ndash;653 of 653" in r4


def test_totals_skip_ids_and_text_and_round_to_cents(env):
    r = run(env, "SELECT 1 AS id, 'a' AS name, 0.1 AS amount UNION ALL SELECT 2, 'b', 0.2").text
    foot = r[r.index("<tfoot>"):]
    assert "0.30" in foot and ">3<" not in foot and "Total" in foot


def test_long_text_is_cut_on_screen_but_full_in_csv(env):
    r = run(env, "SELECT printf('%.300c', 'x') AS long").text
    assert "q-long" in r
    rid = re.search(r'query-csv\?rid=([\w-]+)', r).group(1)
    csv = env.get(f"query-csv?rid={rid}").text
    assert "x" * 300 in csv


def test_csv_escapes_formulas_but_not_numbers(env):
    r = run(env, "SELECT '=HYPERLINK(1)' AS a, '+1' AS b, -4.5 AS amount").text
    rid = re.search(r'query-csv\?rid=([\w-]+)', r).group(1)
    csv = env.get(f"query-csv?rid={rid}").text
    assert "'=HYPERLINK(1)" in csv and "'+1" in csv and ",-4.5" in csv


def test_kept_results_are_per_person(env):
    r = run(env, "SELECT 42 AS answer").text
    rid = re.search(r'query-csv\?rid=([\w-]+)', r).group(1)
    assert env.get(f"query-csv?rid={rid}&as_user=other").status_code == 410


# ---- variables -----------------------------------------------------------------------------------

def test_period_presets(env):
    from app.report_vars import period_range
    today = date(2026, 3, 15)
    assert period_range("month", today) == ("2026-03-01", "2026-03-31")
    assert period_range("last_month", today) == ("2026-02-01", "2026-02-28")
    assert period_range("year", today) == ("2026-01-01", "2026-12-31")
    assert period_range("last_year", today) == ("2025-01-01", "2025-12-31")
    assert period_range("last_12", today) == ("2025-04-01", "2026-03-31")
    assert period_range("all", today) == (None, None)
    assert period_range("custom", today, "2026-01-05", "") == ("2026-01-05", None)


def test_variables_are_detected_with_suggested_types(env):
    r = env.post("query-vars", data={"sql": "SELECT :account, :period_start, :period_end, :category, :min_amount, :since, :merchant"}).text
    for name, t in [("account", "account"), ("period", "period"), ("category", "category"), ("min_amount", "number"),
                    ("since", "date"), ("merchant", "text")]:
        assert re.search(rf'name="d_{name}_type".*?value="{t}" selected', r, re.S), name


def test_all_binds_null_and_values_filter(env):
    a, _b = seed_two_users(env)
    other = env.account("Second")
    env.txn(other, "2026-09-05", -1.0, "SECOND ACCOUNT")
    sql = "SELECT description FROM transactions WHERE (:account IS NULL OR account_id = :account)"
    r = run(env, sql, v_account="all", var_names="account", d_account_type="account", d_account_all="1").text
    assert "COFFEE" in r and "SECOND ACCOUNT" in r
    r = run(env, sql, v_account=str(other), var_names="account", d_account_type="account", d_account_all="1").text
    assert "COFFEE" not in r and "SECOND ACCOUNT" in r


def test_account_variable_refuses_someone_elses_account(env):
    _a, b = seed_two_users(env)
    sql = "SELECT description FROM transactions WHERE (:account IS NULL OR account_id = :account)"
    r = run(env, sql, v_account=str(b), var_names="account", d_account_type="account").text
    assert "Pick one of your accounts" in r


# ---- saved reports -------------------------------------------------------------------------------

def test_save_edit_duplicate_delete(env):
    r = env.post("query-save", data={"sql": "SELECT 1 AS one", "name": "Mine one"}).text
    assert "Saved." in r
    rid = int(re.search(r'report\?id=(\d+)', r).group(1))
    r = env.post("query-save", data={"sql": "SELECT 2 AS two", "name": "Mine one", "report_id": str(rid)}).text
    assert "Saved." in r
    assert env.post("query-save", data={"sql": "SELECT 1", "name": "mine ONE"}).text.count("already exists") == 1
    r = env.post(f"report-duplicate?id={rid}")
    assert r.status_code == 303 and "report=" in r.headers["location"]
    assert "Mine one (copy)" in env.get("reports").text
    assert env.post(f"report-delete?id={rid}").status_code == 303
    assert "Mine one<" not in env.get("reports").text


def test_starter_reports_run_empty_and_with_data(env):
    for rid in range(1, 5):
        r = env.get(f"report?id={rid}")
        assert r.status_code == 200 and 'class="q-error"' not in r.text
    a = env.account()
    env.txn(a, "2020-01-01", -10.0, "SHOP", category="Shopping")
    env.txn(a, date.today().isoformat(), -12.0, "SHOP", category="Shopping")
    env.insert("utility_bills", user_id="tester", utility_type="electric", provider="Xcel Energy", status="complete",
               period_end_date=date.today().isoformat(), cost=80.5, usage_amount=500, usage_unit="kWh")
    r = env.post("reports-check").text
    assert r.count("Runs") == 4 and "Fails" not in r
    assert "12.00" in env.get("report?id=1").text
    assert "q-chart" in env.get("report?id=2").text


def test_check_reports_flags_a_broken_report(env):
    env.post("query-save", data={"sql": "SELECT no_such_column FROM transactions", "name": "Broken"})
    r = env.post("reports-check").text
    assert "Fails" in r and "no_such_column" in r


def test_report_errors_are_plain_for_users(env):
    env.post("query-save", data={"sql": "SELECT no_such_column FROM transactions", "name": "Broken"})
    with env.db() as c:
        rid = c.execute("SELECT id FROM saved_reports WHERE name = 'Broken'").fetchone()[0]
    r = env.get(f"report?id={rid}", headers=OTHER).text
    assert "ask the admin" in r and "no_such_column" not in r
    assert "no_such_column" in env.get(f"report?id={rid}").text


def test_report_links_keep_values_and_page(env):
    env.post("query-save", data={"sql": _numbers_sql(300), "name": "Numbers"})
    with env.db() as c:
        rid = c.execute("SELECT id FROM saved_reports WHERE name = 'Numbers'").fetchone()[0]
    r = env.get(f"report?id={rid}&page=2&per_page=100").text
    assert "rows 101&ndash;200 of 300" in r
    assert "report-csv?" in r


# ---- charts --------------------------------------------------------------------------------------

def test_chart_grouped_bars_and_positive_by_default(env):
    from app.query_engine import Result
    from app.report_charts import render
    res = Result(["month", "money_in", "money_out"], [("2026-01", 10.0, -5.0), ("2026-02", 12.0, -7.0)], False, 0)
    svg, note = render(res, {"type": "bar", "x": "month", "y": ["money_in", "money_out"], "keep_signs": False})
    assert note is None and svg.count('class="chart-bar chart-cat-2"') == 2 and "Drawn as positive" in svg
    assert "-5" not in re.sub(r'd="[^"]*"', "", svg.split("<svg", 1)[1]).split("data-tip")[0]
    svg2, _ = render(res, {"type": "bar", "x": "month", "y": ["money_out"], "keep_signs": True})
    assert "-" in "".join(re.findall(r'<text class="chart-axis"[^>]*>([^<]*)<', svg2))
    line, _ = render(res, {"type": "line", "x": "month", "y": ["money_in"]})
    assert "polyline" in line
    many = Result(["x", "y"], [(i, i) for i in range(501)], False, 0)
    assert render(many, {"type": "bar", "x": "x", "y": ["y"]})[1].startswith("Chart skipped")


# ---- no time limit -------------------------------------------------------------------------------

def _wait_long(env, run_id, url="query-long-status", data=None, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = env.post(f"{url}?run={run_id}", data=data or {"sql": "", "per_page": "100"})
        if "Running" not in r.text:
            return r
        time.sleep(0.1)
    raise AssertionError("long run did not finish")


def test_no_time_limit_runs_in_background_and_can_be_cancelled(env, monkeypatch):
    from app import long_runs, query_engine as qe
    long_runs.reset()
    monkeypatch.setattr(qe, "TIME_LIMIT_SECONDS", 0.2)
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3000000) SELECT count(*) AS c FROM n"
    r = run(env, slow, no_time_limit="1").text
    assert "Running" in r
    run_id = re.search(r'run=([\w-]+)', r).group(1)
    # one at a time: someone else's report is refused while it runs
    busy = run(env, "SELECT 1 AS other_query", no_time_limit="1").text
    assert "Cancel it first" in busy or "Running" in busy
    done = _wait_long(env, run_id, timeout=30).text
    assert "3000000" in done.replace(",", "")
    # cancel
    forever = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    r = run(env, forever, no_time_limit="1").text
    run_id = re.search(r'run=([\w-]+)', r).group(1)
    assert "Cancelled" in env.post(f"long-run-cancel?run={run_id}").text
    assert "cancelled" in _wait_long(env, run_id).text.lower()
    long_runs.reset()


def test_saved_no_time_limit_report_waits_for_run(env):
    from app import long_runs
    long_runs.reset()
    env.post("query-save", data={"sql": "SELECT 7 AS seven", "name": "Slow one", "no_time_limit": "1"})
    with env.db() as c:
        rid = c.execute("SELECT id, no_time_limit FROM saved_reports WHERE name = 'Slow one'").fetchone()
    assert rid[1] == 1
    page = env.get(f"report?id={rid[0]}").text
    assert "waits for you to press Run" in page and "Can take a while" in page
    page = env.get(f"report?id={rid[0]}&run=1").text
    m = re.search(r'report-long-status\?run=([\w-]+)&amp;back=([^"]+)"', page)
    assert m
    deadline = time.time() + 10
    while time.time() < deadline:
        r = env.get(f"report-long-status?run={m.group(1)}&back=" + m.group(2).replace("&amp;", "&"))
        if "HX-Redirect" in r.headers or "hx-redirect" in r.headers:
            break
        time.sleep(0.1)
    target = r.headers["hx-redirect"]
    assert "rid=" in target and ">7<" in env.get(target).text
    long_runs.reset()


def test_reference_box_lists_registered_tables_and_its_snippets_run(env):
    import html
    from app import query_engine as qe
    env.account()
    page = env.get("admin-storage?tab=query").text
    listed = re.findall(r'class="q-ref-table" data-search="(\w+) ', page)
    assert listed == qe.visible_names()
    for name, spec in qe.REGISTRY.items():
        assert html.escape(spec.description, quote=False) in page or spec.description in html.unescape(page)
    snippets = [html.unescape(s) for s in re.findall(r'class="btn-secondary q-insert" data-insert="([^"]+)">Insert<', page)]
    assert len(snippets) >= 15
    for code in snippets:
        forms = [code, f"SELECT {code} FROM transactions", f"SELECT 1 FROM transactions WHERE {code}",
                 f"SELECT {code} FROM toll_transactions"]
        ok = any('class="q-error"' not in run(env, sql).text for sql in forms)
        assert ok, code


# ---- review fixes ------------------------------------------------------------------------------

def _save(env, sql, name, **extra):
    env.post("query-save", data={"sql": sql, "name": name, **extra})
    with env.db() as c:
        return c.execute("SELECT id FROM saved_reports WHERE name = ?", (name,)).fetchone()[0]


def test_no_time_limit_report_never_runs_inline(env):
    from app import long_runs
    long_runs.reset()
    rid = _save(env, "SELECT 7 AS seven", "Slow two", no_time_limit="1")
    r = env.get(f"report?id={rid}&rid=junk", headers=OTHER).text
    assert "expired" in r and ">7<" not in r and long_runs.active() is None
    r = env.get(f"report-csv?id={rid}&rid=junk", headers=OTHER)
    assert r.status_code == 410
    r = env.get(f"report?id={rid}&run=1", headers={**OTHER, "sec-fetch-site": "cross-site"}).text
    assert "report-long-status" not in r and long_runs.active() is None


def test_infinite_numbers_do_not_break_the_page(env):
    r = run(env, "SELECT 'a' AS label, 1e999 AS amount", chart_type="bar", chart_x="label", chart_y="amount")
    assert r.status_code == 200 and "q-table" in r.text


def test_period_pair_next_to_a_plain_variable_of_the_same_base(env):
    from app.report_vars import detect
    names = [(v.name, v.type) for v in detect("SELECT :p, :p_start, :p_end")[0]]
    assert ("p", "text") in names and ("p_start", "date") in names and ("p_end", "date") in names


def test_users_see_input_errors_but_not_sql_errors(env):
    rid = _save(env, "SELECT :min_amount AS m", "Needs number")
    r = env.get(f"report?id={rid}&v_min_amount=abc", headers=OTHER).text
    assert "must be a number" in r


def test_result_byte_cap(env, monkeypatch):
    from app import query_engine as qe
    monkeypatch.setattr(qe, "MAX_RESULT_BYTES", 10_000)
    r = run(env, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 100) "
                 "SELECT printf('%.500c', 'x') FROM n").text
    assert "larger than" in r


def test_admin_can_cancel_someone_elses_long_run_from_the_query_tab(env):
    from app import long_runs
    long_runs.reset()
    forever = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) AS c FROM n"
    rid = _save(env, forever, "Forever", no_time_limit="1")
    env.get(f"report?id={rid}&run=1", headers=OTHER)
    assert long_runs.active() is not None
    r = run(env, "SELECT 1", no_time_limit="1").text
    assert "Another long report is running" in r and "Cancel that run" in r
    run_id = long_runs.active().id
    assert "Cancelled" in env.post(f"long-run-cancel?run={run_id}").text
    long_runs.reset()
