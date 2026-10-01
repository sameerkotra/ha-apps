"""Link this on the Transfers page (SPEC.md section 8)."""
import re


def _ambiguous(env):
    chk = env.account("Checking")
    sav = env.account("Savings", "savings")
    card = env.account("Card", "credit_card")
    out = env.txn(chk, "2026-08-01", -100.0, "XFER OUT")
    to_sav = env.txn(sav, "2026-08-02", 100.0, "XFER IN")
    to_card = env.txn(card, "2026-08-02", -100.0, "PAYMENT THANK YOU")
    return out, to_sav, to_card


def _forms(page):
    return re.findall(r'<form method="post" action="(transfer-link[^"]*)">(.*?)</form>', page, re.S)


def _fields(body):
    return dict(re.findall(r'name="(\w+)" value="([^"]*)"', body))


def test_link_this_links_and_keeps_the_view(env):
    out, to_sav, to_card = _ambiguous(env)
    page = env.get("transfers?start_date=&end_date=").text      # "show all": August isn't this month
    forms = _forms(page)
    assert len(forms) == 2
    action, body = forms[1]
    data = _fields(body)
    assert data["inflow_id"] == str(to_card) and data["dates_sent"] == "1"
    r = env.post(action, data=data)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("transfers?") and "start_date=&end_date=" in loc and f"linked={out}" in loc
    with env.db() as c:
        assert c.execute("SELECT transfer_pair_id FROM transactions WHERE id=?", (out,)).fetchone()[0] == to_card
        assert c.execute("SELECT transfer_pair_id FROM transactions WHERE id=?", (to_sav,)).fetchone()[0] is None
    page = env.get(loc).text
    assert "Linked: Checking 2026-08-01" in page and "Couldn" not in page
    assert not _forms(page)                                    # nothing left to confirm


def test_link_this_explains_a_refusal(env):
    out, to_sav, to_card = _ambiguous(env)
    with env.db() as c:
        c.execute("UPDATE transactions SET review_status = 'pending_review' WHERE id = ?", (to_card,))
    r = env.post("transfer-link", data={"outflow_id": to_sav, "inflow_id": out})       # sides swapped
    assert "must+be+money+out" in r.headers["location"]
    # another user's view never links these ids
    r = env.post("transfer-link", data={"outflow_id": out, "inflow_id": to_sav},
                 headers={"x-remote-user-id": "other", "x-remote-user-name": "Other"})
    assert "link_error=" in r.headers["location"]
    with env.db() as c:
        assert c.execute("SELECT COUNT(*) FROM transactions WHERE transfer_pair_id IS NOT NULL").fetchone()[0] == 0
    r = env.post("transfer-link", data={"outflow_id": out, "inflow_id": to_card})
    assert r.status_code == 303 and "link_error=" in r.headers["location"]
    page = env.get(r.headers["location"]).text
    assert "link that pair: The receiving side (Card 2026-08-02) is still pending review." in page
