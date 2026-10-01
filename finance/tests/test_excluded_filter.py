"""Transactions → All: the Excluded filter (SPEC.md section 13)."""


def _rows(env):
    acct = env.account()
    kept = env.txn(acct, "2026-09-01", -10.0, "KEPT COFFEE")
    gone = env.txn(acct, "2026-09-02", -25.0, "EXCLUDED REFUNDED", is_excluded=1)
    gone_in = env.txn(acct, "2026-09-03", 40.0, "EXCLUDED DEPOSIT", is_excluded=1)
    return kept, gone, gone_in


def test_excluded_only_and_hide(env):
    _rows(env)
    page = env.get("transactions?excluded=only").text
    assert "EXCLUDED REFUNDED" in page and "EXCLUDED DEPOSIT" in page and "KEPT COFFEE" not in page
    assert '<option value="only" selected>' in page
    page = env.get("transactions?excluded=hide").text
    assert "KEPT COFFEE" in page and "EXCLUDED" not in page.split("txn-filters")[-1].split("</form>", 1)[-1]
    page = env.get("transactions?searched=1").text
    assert "KEPT COFFEE" in page and "EXCLUDED REFUNDED" in page
    # combined with Money out: the excluded spending, not nothing
    page = env.get("transactions?excluded=only&flow=expense").text
    assert "EXCLUDED REFUNDED" in page and "EXCLUDED DEPOSIT" not in page and "KEPT COFFEE" not in page
    # a junk value is ignored
    assert "KEPT COFFEE" in env.get("transactions?excluded=zzz&searched=1").text


def test_export_follows_the_excluded_filter(env):
    _rows(env)
    csv = env.get("transactions-export?excluded=only").text
    assert "EXCLUDED REFUNDED" in csv and "KEPT COFFEE" not in csv
