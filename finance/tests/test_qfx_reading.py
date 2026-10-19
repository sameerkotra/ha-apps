"""Reading QFX / OFX files (SPEC.md section 4a): both shapes, encodings, quirks, refusals, dates, amounts, signs."""
from decimal import Decimal

import pytest

import qfx_samples as qs


def _parse(raw):
    from app import ofx
    return ofx.parse(raw)


def test_ofx_1x_sgml_with_unclosed_tags_windows_1252():
    f = _parse(qs.checking())
    assert (f.version, f.encoding, f.org) == ("1.x", "cp1252", "Chase")
    [b] = f.blocks
    assert (b.kind, b.account_type, b.last4, b.currency) == ("bank", "checking", "6789", "USD")
    assert (b.start, b.end, b.balance) == ("2026-03-03", "2026-06-01", Decimal("1000.00"))
    assert [(t.date, t.amount, t.fitid, t.description) for t in b.transactions] == [
        ("2026-03-04", Decimal("-4.50"), "C1", "CAFE & CO"),
        ("2026-03-15", Decimal("2500.00"), "C2", "PAYROLL ACME"),
        ("2026-03-20", Decimal("-120.00"), "C3", "Check 1234"),
    ]


def test_windows_1252_and_utf8_text_in_both_shapes():
    cafe = qs.sgml_trn("DEBIT", "20260305", "-4.50", "E1", "CAFÉ MÜNCHEN")
    for raw, enc in ((qs.sgml_file(qs.bank_block([cafe])), "cp1252"),
                     (qs.sgml_file(qs.bank_block([cafe]), header=qs.SGML_UTF8_HEADER, encoding="utf-8"), "utf-8")):
        f = _parse(raw)
        assert f.encoding == enc and f.blocks[0].transactions[0].description == "CAFÉ MÜNCHEN"
    for enc in ("UTF-8", "windows-1252"):
        f = _parse(qs.xml_file(enc))
        assert f.version == "2.x" and f.org == "Crédit Agricole"
        [b] = f.blocks
        assert (b.account_type, b.last4, b.balance) == ("savings", "4444", Decimal("500.00"))
        assert [(t.description, t.amount) for t in b.transactions] == [("Café Zoë", Decimal("-10.25")),
                                                                       ("Interest", Decimal("1.05"))]
    # undeclared text: UTF-8 first, else Windows-1252
    plain = qs.sgml_file(qs.bank_block([cafe]), header="OFXHEADER:100\nDATA:OFXSGML\n\n", encoding="cp1252")
    assert _parse(plain).blocks[0].transactions[0].description == "CAFÉ MÜNCHEN"


def test_real_world_quirks():
    name32 = "AMAZON MKTPLACE PMTS AMZN.COM/BI"          # OFX 1.x cuts NAME at 32 characters
    body = (qs.SGML_HEADER.replace("\r\n", "\n") + "\n\n<ofx>\n<!-- exported by OnlineBanking 7.2 -->\n\n"
            "<bankmsgsrsv1><stmttrnrs><stmtrs><curdef>USD\n<bankacctfrom><bankid>1\n<acctid>12345678\n"
            "<accttype>checking\n</bankacctfrom>\n\n<banktranlist><dtstart>20260301<dtend>20260331\n"
            "<stmttrn><trntype>DEBIT<dtposted>20260302<trnamt>-19.99<fitid>Q1"
            f"<name>{name32}<memo>LL WA</stmttrn>\n"
            "<stmttrn><trntype>DEBIT<dtposted>20260303<trnamt>-3.00<fitid>Q2<name>AT&amp;T WIRELESS"
            "<memo>AT&amp;T</stmttrn>\n"
            "<stmttrn><trntype>DEBIT<dtposted>20260304<trnamt>-7.00<fitid>Q3<name>SHORT NAME"
            "<memo>SHORT NAME STORE #42</stmttrn>\n"
            "</banktranlist><ledgerbal><balamt>10.00<dtasof>20260331</ledgerbal></stmtrs></stmttrnrs>"
            "</bankmsgsrsv1></ofx>\n").replace("\n", "\r\n")
    [b] = _parse(body.encode("cp1252")).blocks
    assert [t.description for t in b.transactions] == [
        f"{name32} · LL WA", "AT&T WIRELESS", "SHORT NAME STORE #42"]
    assert b.last4 == "5678" and b.account_type == "checking"


def test_dates_dtuser_over_dtposted_and_time_zones_ignored():
    from app import ofx
    assert ofx.ofx_date("20260304233000[-5:EST]") == "2026-03-04"
    assert ofx.ofx_date("20260305000000.000[+10:AEST]") == "2026-03-05"
    assert ofx.ofx_date("20260230") is None and ofx.ofx_date("") is None
    [b] = _parse(qs.checking()).blocks
    first = b.transactions[0]
    assert (first.date, first.date_from, first.posted) == ("2026-03-04", "DTUSER", "2026-03-05")
    assert b.transactions[1].date_from == "DTPOSTED"


def test_amounts_with_comma_decimals():
    from app import ofx
    assert ofx.ofx_amount("-4,50") == Decimal("-4.50")
    assert ofx.ofx_amount("1.234,56") == Decimal("1234.56")
    assert ofx.ofx_amount("1,234.56") == Decimal("1234.56")
    assert ofx.ofx_amount("+12") == Decimal("12.00")
    assert ofx.ofx_amount("abc") is None and ofx.ofx_amount("") is None and ofx.ofx_amount("NaN") is None


def test_rows_that_cant_be_used_are_left_out():
    rows = [qs.sgml_trn("DEBIT", "", "-1.00", "L1", "NO DATE"),
            qs.sgml_trn("DEBIT", "20260302", "", "L2", "NO AMOUNT"),
            qs.sgml_trn("DEBIT", "20260303", "twelve", "L3", "NOT A NUMBER"),
            qs.sgml_trn("DEBIT", "20260304", "-2.00", "L4", "FINE")]
    [b] = _parse(qs.sgml_file(qs.bank_block(rows))).blocks
    assert [t.description for t in b.usable] == ["FINE"]
    assert [t.problem for t in b.left_out] == ["no date", "no amount", "the amount isn't a number (twelve)"]


def test_card_signs_flipped_unless_the_bank_already_uses_finances_convention():
    from app import ofx
    [b] = _parse(qs.card()).blocks
    assert b.account_type == "credit_card" and b.last4 == "9876"
    assert ofx.card_sign_rule(b)[0] is True
    already = [qs.sgml_trn("DEBIT", "20260305", "25.00", "W1", "GROCER"),
               qs.sgml_trn("DEBIT", "20260306", "10.00", "W2", "CAFE"),
               qs.sgml_trn("CREDIT", "20260320", "-35.00", "W3", "PAYMENT")]
    [b2] = _parse(qs.sgml_file(qs.card_block(already))).blocks
    flip, rule = ofx.card_sign_rule(b2)
    assert flip is False and "read as written" in rule


def test_several_blocks_and_investment_blocks():
    f = _parse(qs.two_accounts())
    assert [(b.account_type, b.last4) for b in f.blocks] == [("checking", "6789"), ("savings", "4321")]
    inv = ("<INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS><DTASOF>20260601<CURDEF>USD<INVACCTFROM><BROKERID>x"
           "<ACCTID>777</INVACCTFROM><INVTRANLIST><DTSTART>20260101<DTEND>20260601</INVTRANLIST></INVSTMTRS>"
           "</INVSTMTTRNRS></INVSTMTMSGSRSV1>")
    mixed = _parse(qs.sgml_file(qs.bank_block(qs.CHECKING_ROWS), inv))
    assert len(mixed.blocks) == 1 and "Only bank and credit card QFX files are supported." in mixed.skipped[0]
    from app import ofx
    with pytest.raises(ofx.Refused, match="Only bank and credit card QFX files are supported."):
        _parse(qs.sgml_file(inv))


@pytest.mark.parametrize("raw, message", [
    (b"<!DOCTYPE html><html><head><title>Session expired</title></head><body>Log in</body></html>",
     "looks like a web page"),
    (b"", "empty"),
    (b"   \r\n ", "empty"),
    (b"Date,Description,Amount\n2026-01-01,x,1\n", "isn't a QFX/OFX file"),
])
def test_refused_files(raw, message):
    from app import ofx
    with pytest.raises(ofx.Refused, match=message):
        _parse(raw)


def test_a_file_with_no_transactions_is_refused():
    from app import ofx
    with pytest.raises(ofx.Refused, match="no transactions"):
        _parse(qs.sgml_file(qs.bank_block([])))
    with pytest.raises(ofx.Refused, match="no transactions"):
        _parse(qs.sgml_file(""))


def test_entity_tricks_are_never_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE OFX [ <!ENTITY xxe SYSTEM "file://' + str(secret) + '"> '
            '<!ENTITY lol "lol"> <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;"> ]>\n'
            '<?OFX OFXHEADER="200" VERSION="220"?>\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD</CURDEF>'
            '<BANKACCTFROM><ACCTID>1111</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>'
            '<BANKTRANLIST><DTSTART>20260101</DTSTART><DTEND>20260131</DTEND>'
            '<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20260102</DTPOSTED><TRNAMT>-1.00</TRNAMT><FITID>E1</FITID>'
            '<NAME>&xxe; &lol2; &#65;&amp;</NAME></STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>')
    [b] = _parse(body.encode()).blocks
    desc = b.transactions[0].description
    assert desc == "&xxe; &lol2; A&" and "SECRET" not in desc


def test_period_label():
    from app import ofx
    assert ofx.period_label("2026-03-03", "2026-06-01") == "Mar 3 – Jun 1, 2026"
    assert ofx.period_label("2025-12-03", "2026-02-01") == "Dec 3, 2025 – Feb 1, 2026"
    assert ofx.period_label(None, None) == ""
