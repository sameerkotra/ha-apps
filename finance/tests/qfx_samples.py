"""Small QFX / OFX files for the QFX tests (SPEC.md section 4a), written the way banks write them."""

SGML_HEADER = ("OFXHEADER:100\r\nDATA:OFXSGML\r\nVERSION:102\r\nSECURITY:NONE\r\nENCODING:USASCII\r\n"
               "CHARSET:1252\r\nCOMPRESSION:NONE\r\nOLDFILEUID:NONE\r\nNEWFILEUID:NONE\r\n\r\n")
SGML_UTF8_HEADER = SGML_HEADER.replace("ENCODING:USASCII", "ENCODING:UTF-8").replace("CHARSET:1252", "CHARSET:NONE")


def signon(org="Chase", fid="10898"):
    return ("<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20260602120000[-5:EST]"
            f"<LANGUAGE>ENG<FI><ORG>{org}<FID>{fid}</FI><INTU.BID>{fid}</SONRS></SIGNONMSGSRSV1>\r\n")


def sgml_trn(trntype, posted, amount, fitid, name="", memo=None, user=None, checknum=None):
    """One <STMTTRN> with unclosed leaves (OFX 1.x)."""
    parts = [f"<STMTTRN>\r\n<TRNTYPE>{trntype}\r\n<DTPOSTED>{posted}\r\n"]
    if user:
        parts.append(f"<DTUSER>{user}\r\n")
    parts.append(f"<TRNAMT>{amount}\r\n")
    if fitid is not None:
        parts.append(f"<FITID>{fitid}\r\n")
    if checknum:
        parts.append(f"<CHECKNUM>{checknum}\r\n")
    if name:
        parts.append(f"<NAME>{name}\r\n")
    if memo is not None:
        parts.append(f"<MEMO>{memo}\r\n")
    parts.append("</STMTTRN>\r\n")
    return "".join(parts)


def bank_block(rows, acctid="000123456789", accttype="CHECKING", start="20260303", end="20260601120000[-5:EST]",
               balance="1000.00", currency="USD"):
    return ("<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>\r\n"
            f"<STMTRS><CURDEF>{currency}<BANKACCTFROM><BANKID>021000021<ACCTID>{acctid}<ACCTTYPE>{accttype}"
            "</BANKACCTFROM>\r\n"
            f"<BANKTRANLIST><DTSTART>{start}<DTEND>{end}\r\n" + "".join(rows) + "</BANKTRANLIST>\r\n"
            f"<LEDGERBAL><BALAMT>{balance}<DTASOF>{end}</LEDGERBAL>\r\n"
            "<AVAILBAL><BALAMT>999.99<DTASOF>20260601</AVAILBAL>\r\n"
            "</STMTRS></STMTTRNRS></BANKMSGSRSV1>\r\n")


def card_block(rows, acctid="4111111111119876", start="20260301", end="20260331", balance="-125.50"):
    return ("<CREDITCARDMSGSRSV1><CCSTMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>\r\n"
            f"<CCSTMTRS><CURDEF>USD<CCACCTFROM><ACCTID>{acctid}</CCACCTFROM>\r\n"
            f"<BANKTRANLIST><DTSTART>{start}<DTEND>{end}\r\n" + "".join(rows) + "</BANKTRANLIST>\r\n"
            f"<LEDGERBAL><BALAMT>{balance}<DTASOF>{end}</LEDGERBAL>\r\n"
            "</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>\r\n")


def sgml_file(*blocks, org="Chase", header=SGML_HEADER, encoding="cp1252") -> bytes:
    return (header + "<OFX>\r\n" + signon(org) + "".join(blocks) + "</OFX>\r\n").encode(encoding)


# A checking account, OFX 1.x: 3 rows, Mar 3 – Jun 1, 2026, closing balance 1000.00.
CHECKING_ROWS = [
    sgml_trn("DEBIT", "20260305", "-4.50", "C1", "CAFE &amp; CO", user="20260304233000[-5:EST]"),
    sgml_trn("CREDIT", "20260315", "2500.00", "C2", "PAYROLL ACME", memo=""),
    sgml_trn("CHECK", "20260320", "-120.00", "C3", checknum="1234"),
]


def checking(**kw) -> bytes:
    return sgml_file(bank_block(CHECKING_ROWS, **kw))


# A credit card, OFX 1.x: purchases negative and the payment positive, as OFX writes them.
CARD_ROWS = [
    sgml_trn("DEBIT", "20260305", "-25.00", "V1", "GROCER"),
    sgml_trn("DEBIT", "20260310", "-100.50", "V2", "HARDWARE STORE"),
    sgml_trn("CREDIT", "20260320", "200.00", "V3", "PAYMENT THANK YOU"),
]


def card(**kw) -> bytes:
    return sgml_file(card_block(CARD_ROWS, **kw), org="Visa Bank")


# OFX 2.x XML, UTF-8.
XML_FILE = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
  <SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
    <DTSERVER>20260602</DTSERVER><LANGUAGE>ENG</LANGUAGE><FI><ORG>Crédit Agricole</ORG><FID>1</FID></FI></SONRS>
  </SIGNONMSGSRSV1>
  <BANKMSGSRSV1><STMTTRNRS><TRNUID>1</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
    <STMTRS><CURDEF>USD</CURDEF>
      <BANKACCTFROM><BANKID>1</BANKID><ACCTID>55554444</ACCTID><ACCTTYPE>SAVINGS</ACCTTYPE></BANKACCTFROM>
      <BANKTRANLIST><DTSTART>20260401</DTSTART><DTEND>20260430</DTEND>
        <STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20260402</DTPOSTED><TRNAMT>-10,25</TRNAMT><FITID>X1</FITID>
          <NAME>Café Zoë</NAME><MEMO></MEMO></STMTTRN>
        <STMTTRN><TRNTYPE>INT</TRNTYPE><DTPOSTED>20260430</DTPOSTED><TRNAMT>1.05</TRNAMT><FITID>X2</FITID>
          <NAME>Interest</NAME></STMTTRN>
      </BANKTRANLIST>
      <LEDGERBAL><BALAMT>500.00</BALAMT><DTASOF>20260430</DTASOF></LEDGERBAL>
    </STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""


def xml_file(encoding="UTF-8") -> bytes:
    text = XML_FILE.replace('encoding="UTF-8"', f'encoding="{encoding}"')
    return text.encode("utf-8" if encoding.upper() == "UTF-8" else "cp1252")


def two_accounts() -> bytes:
    """Checking ending 6789 and savings ending 4321 in one file (some banks put them together)."""
    return sgml_file(bank_block(CHECKING_ROWS)
                     .replace("</STMTRS></STMTTRNRS></BANKMSGSRSV1>\r\n", "</STMTRS></STMTTRNRS>\r\n")
                     + "<STMTTRNRS><TRNUID>2<STATUS><CODE>0<SEVERITY>INFO</STATUS>"
                     "<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>1<ACCTID>99994321<ACCTTYPE>SAVINGS</BANKACCTFROM>"
                     "<BANKTRANLIST><DTSTART>20260303<DTEND>20260601"
                     + sgml_trn("CREDIT", "20260310", "50.00", "S1", "TRANSFER IN")
                     + "</BANKTRANLIST><LEDGERBAL><BALAMT>5050.00<DTASOF>20260601</LEDGERBAL></STMTRS></STMTTRNRS>"
                     "</BANKMSGSRSV1>\r\n")


def overlapping() -> bytes:
    """The next download of the checking account: Apr 1 – Jun 30, overlapping the first (C2 and C3 again)."""
    rows = [CHECKING_ROWS[1], CHECKING_ROWS[2],
            sgml_trn("DEBIT", "20260610", "-60.00", "C4", "ELECTRIC CO"),
            sgml_trn("DEBIT", "20260620", "-15.00", "C5", "BOOKSHOP")]
    return sgml_file(bank_block(rows, start="20260401", end="20260630", balance="925.00"))
