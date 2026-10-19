"""QFX / OFX statement files (SPEC.md section 4a): a small tolerant reader, no dependency and no XML parser.

OFX comes in two shapes and both are read the same way:

  - OFX 1.x (most QFX downloads): an `OFXHEADER:100` header of `KEY:VALUE` lines, then SGML whose leaf tags are
    often never closed (`<TRNAMT>-4.50`);
  - OFX 2.x: `<?xml …?><?OFX OFXHEADER="200" …?>`, then XML with every tag closed.

The reader walks `<TAG>value` pairs and the block structure, closing a leaf at the next `<`. Markup declarations
(`<!DOCTYPE …>`, `<!ENTITY …>`), comments and processing instructions are skipped, and the only entities resolved
are `&amp; &lt; &gt; &quot; &apos;` and numeric `&#NN;`, so nothing in a file can pull in another file or expand
itself ("billion laughs"): `&anything;` else stays as written.

Only what Finance uses is kept: each bank (`STMTRS`) or credit-card (`CCSTMTRS`) block's account type and the
**last 4 digits** of its account number (the full ACCTID is never kept, shown or logged), currency, date range,
closing balance and transactions. `parse()` raises `Refused` with a message for the person when the file can't be
used at all.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

SNIFF_BYTES = 1024
MAX_DESCRIPTION = 500           # the transactions.description bound every import uses

HTML_REFUSED = "That isn't a QFX/OFX file — it looks like a web page. Download it again from your bank."
NOT_OFX = "That isn't a QFX/OFX file. Download it again from your bank (Quicken / QFX, or Money / OFX)."
EMPTY = "That QFX/OFX file is empty."
NO_TRANSACTIONS = "That QFX/OFX file has no transactions in it."
ONLY_BANK_AND_CARD = "Only bank and credit card QFX files are supported."

# Leaves read (or commonly seen) that may be empty in SGML: never treated as the start of a block.
_LEAVES = {
    "TRNTYPE", "DTPOSTED", "DTUSER", "DTAVAIL", "TRNAMT", "FITID", "CORRECTFITID", "CORRECTACTION", "SRVRTID",
    "CHECKNUM", "REFNUM", "SIC", "PAYEEID", "NAME", "EXTDNAME", "MEMO", "CURDEF", "BANKID", "BRANCHID", "ACCTID",
    "ACCTTYPE", "ACCTKEY", "DTSTART", "DTEND", "BALAMT", "DTASOF", "ORG", "FID", "CODE", "SEVERITY", "MESSAGE",
    "DTSERVER", "LANGUAGE", "TRNUID", "INTU.BID", "INTU.USERID", "DTPROFUP", "DTACCTUP", "MKTGINFO", "CURRATE",
    "CURSYM",
}

_BANK_TYPES = {"CHECKING": "checking", "SAVINGS": "savings", "MONEYMRKT": "savings", "CD": "savings",
               "CREDITLINE": "checking"}
TYPE_LABELS = {"checking": "Checking", "savings": "Savings", "credit_card": "Credit card"}


class Refused(ValueError):
    """The file can't be imported at all; the message is for the person."""


# ------------------------------------------------------------------ recognising and decoding

def looks_like_ofx(head: bytes) -> bool:
    """A file counts as OFX when its first 1 KB has OFXHEADER or <OFX> (any case)."""
    h = head[:SNIFF_BYTES].lower()
    return b"ofxheader" in h or b"<ofx>" in h


def looks_like_html(head: bytes) -> bool:
    h = head[:SNIFF_BYTES].lower().lstrip()
    return h.startswith(b"<!doctype html") or b"<html" in h or b"<head" in h or b"<body" in h


def _encoding(head: str) -> str | None:
    """'utf-8', 'cp1252' or None (not said: try UTF-8, then Windows-1252), from the 1.x header or the XML header."""
    h = head.upper()
    m = re.search(r'ENCODING\s*[:=]\s*"?([A-Z0-9_-]+)', h)
    enc = m.group(1) if m else ""
    m = re.search(r'CHARSET\s*[:=]\s*"?([A-Z0-9_-]+)', h)
    charset = m.group(1) if m else ""
    if enc.replace("-", "").replace("_", "") in ("UTF8",):
        return "utf-8"
    if charset in ("1252", "WINDOWS-1252", "CP1252") or enc == "USASCII":
        return "cp1252"
    if charset.replace("-", "") in ("UTF8",):
        return "utf-8"
    return None


def decode(raw: bytes) -> tuple[str, str]:
    """(text, the encoding used): as the header says, falling back UTF-8 → Windows-1252."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    said = _encoding(raw[:SNIFF_BYTES].decode("latin-1"))
    for enc in ([said] if said else []) + ["utf-8", "cp1252"]:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace"), "cp1252"


# ------------------------------------------------------------------ the tag reader

_ENTITY = re.compile(r"&(amp|lt|gt|quot|apos|#\d{1,7}|#x[0-9A-Fa-f]{1,6});")
_NAMED = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def unescape(text: str) -> str:
    """The five XML entities and numeric &#NN; / &#xNN; only. Anything else (&nbsp;, &xxe;) stays as written."""
    def one(m):
        name = m.group(1)
        if name in _NAMED:
            return _NAMED[name]
        try:
            code = int(name[2:], 16) if name[1:2] in ("x", "X") else int(name[1:])
            return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else m.group(0)
        except ValueError:
            return m.group(0)
    return _ENTITY.sub(one, text)


@dataclass
class Node:
    name: str
    value: str | None = None             # a leaf's text (None for a block)
    children: list = field(default_factory=list)

    def child(self, name: str) -> "Node | None":
        for c in self.children:
            if c.name == name:
                return c
        return None

    def find(self, name: str) -> "Node | None":
        """The first node of that name below this one (depth first)."""
        for c in self.children:
            if c.name == name:
                return c
            hit = c.find(name)
            if hit is not None:
                return hit
        return None

    def find_all(self, name: str) -> list:
        out = []
        for c in self.children:
            if c.name == name:
                out.append(c)
            else:
                out.extend(c.find_all(name))
        return out

    def text(self, name: str) -> str:
        """A leaf's value below this node ("" when absent). A leaf read as an empty block counts as ""."""
        n = self.find(name)
        return (n.value or "").strip() if n is not None else ""


_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z0-9_.:-]+)[^>]*?(/?)\s*>")


def read_tree(text: str) -> Node:
    """The OFX body as a tree under a root node. Tolerant: unclosed leaves end at the next tag, a closing tag closes
    every block opened since its own opening tag, stray closing tags are ignored."""
    root = Node("#root")
    stack = [root]
    i, n = 0, len(text)
    while i < n:
        lt = text.find("<", i)
        if lt < 0:
            break
        if text.startswith("<!--", lt):
            end = text.find("-->", lt + 4)
            i = n if end < 0 else end + 3
            continue
        if text.startswith("<!", lt) or text.startswith("<?", lt):
            # <!DOCTYPE …>, <!ENTITY …> (a DOCTYPE's [ … ] internal subset included), <?xml …?>: skipped, never used
            end = _declaration_end(text, lt)
            i = n if end < 0 else end + 1
            continue
        m = _TAG.match(text, lt)
        if m is None:
            i = lt + 1
            continue
        closing, name, selfclose = m.group(1), m.group(2).upper(), m.group(3)
        i = m.end()
        if closing:
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].name == name:
                    del stack[depth:]
                    break
            continue
        if selfclose:
            stack[-1].children.append(Node(name, ""))
            continue
        nxt = text.find("<", i)
        raw_value = text[i:] if nxt < 0 else text[i:nxt]
        value = raw_value.strip()
        if value or name in _LEAVES:
            stack[-1].children.append(Node(name, unescape(value)))
            i = len(text) if nxt < 0 else nxt
            # an XML leaf's own closing tag right after it is consumed here
            m2 = _TAG.match(text, i)
            if m2 and m2.group(1) and m2.group(2).upper() == name:
                i = m2.end()
        else:
            node = Node(name)
            stack[-1].children.append(node)
            stack.append(node)
    return root


def _declaration_end(text: str, start: int) -> int:
    """The index of the '>' that ends a <!…> or <?…?> starting at `start` (a DOCTYPE's [ … ] subset is skipped)."""
    depth = 0
    for j in range(start + 2, len(text)):
        c = text[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth = max(0, depth - 1)
        elif c == ">" and depth == 0:
            return j
    return -1


# ------------------------------------------------------------------ values

_DATE = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})")


def ofx_date(value: str) -> str | None:
    """YYYYMMDD[HHMMSS[.XXX]][[±H:TZ]] → 'YYYY-MM-DD'. The time and the time zone are ignored on purpose: a 23:30
    −5 purchase is still that day for the person."""
    m = _DATE.match(value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def ofx_amount(value: str) -> Decimal | None:
    """TRNAMT/BALAMT to a Decimal: `-4.50`, `+4.50`, `1,234.56`, and comma decimals (`-4,50`, `1.234,56`)."""
    v = (value or "").strip().replace(" ", "").replace(" ", "")
    if not v:
        return None
    if "," in v and "." in v:
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")      # 1.234,56
        else:
            v = v.replace(",", "")                        # 1,234.56
    elif "," in v:
        head, _, tail = v.rpartition(",")
        v = (head.replace(",", "") + "." + tail) if len(tail) != 3 or head.lstrip("+-") in ("", "0") else v.replace(",", "")
    try:
        d = Decimal(v)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return d.quantize(Decimal("0.01"))


def last4(acctid: str) -> str | None:
    """The account number's last 4 digits (None when it has none). The full number goes no further than this."""
    digits = "".join(ch for ch in (acctid or "") if ch.isdigit())
    return digits[-4:] or None


def describe(name: str, memo: str, checknum: str, trntype: str) -> str:
    """NAME, with MEMO added after " · " when it isn't already part of NAME (OFX 1.x cuts NAME at 32 characters and
    the rest is often in MEMO; a MEMO that repeats a cut NAME in full replaces it). "Check 1234" for a check with no
    name. Cut to the column's length."""
    name, memo = " ".join(name.split()), " ".join(memo.split())
    if name and memo:
        if memo.lower() in name.lower():
            out = name
        elif memo.lower().startswith(name.lower()):
            out = memo
        else:
            out = f"{name} · {memo}"
    elif name:
        out = name
    elif checknum:
        out = f"Check {checknum}" + (f" · {memo}" if memo else "")
    elif memo:
        out = memo
    else:
        out = (trntype or "Transaction").capitalize()
    return out[:MAX_DESCRIPTION]


# ------------------------------------------------------------------ the statement blocks

@dataclass
class Txn:
    line: int                       # its place in the block (1 = first)
    trntype: str
    date: str | None
    date_from: str                  # "DTUSER" or "DTPOSTED"
    posted: str | None              # DTPOSTED's date
    raw_amount: str
    amount: Decimal | None          # as written in the file, before any sign rule
    fitid: str
    description: str
    problem: str | None = None      # why the row can't be used (left out)


@dataclass
class Block:
    index: int                      # 0-based, in file order
    kind: str                       # "bank" or "card"
    account_type: str               # checking / savings / credit_card
    last4: str | None
    currency: str
    start: str | None
    end: str | None
    balance: Decimal | None         # LEDGERBAL/BALAMT as written
    balance_date: str | None
    transactions: list = field(default_factory=list)

    @property
    def usable(self) -> list:
        return [t for t in self.transactions if t.problem is None]

    @property
    def left_out(self) -> list:
        return [t for t in self.transactions if t.problem is not None]

    @property
    def type_label(self) -> str:
        return TYPE_LABELS[self.account_type]


@dataclass
class OfxFile:
    version: str                    # "1.x" or "2.x"
    encoding: str
    org: str                        # <FI><ORG>, "" when absent
    blocks: list
    skipped: list                   # what was left out of the file, for the person ("1 investment statement …")


def _row(line: int, t: Node) -> Txn:
    trntype = t.text("TRNTYPE").upper()
    posted = ofx_date(t.text("DTPOSTED"))
    user = ofx_date(t.text("DTUSER"))
    raw = t.text("TRNAMT")
    amount = ofx_amount(raw)
    txn = Txn(line=line, trntype=trntype, date=user or posted, date_from="DTUSER" if user else "DTPOSTED",
              posted=posted, raw_amount=raw, amount=amount, fitid=t.text("FITID")[:255],
              description=describe(t.text("NAME"), t.text("MEMO"), t.text("CHECKNUM"), trntype))
    if txn.date is None:
        txn.problem = "no date" if not (t.text("DTUSER") or t.text("DTPOSTED")) else "the date can't be read"
    elif not raw:
        txn.problem = "no amount"
    elif amount is None:
        txn.problem = f"the amount isn't a number ({raw[:20]})"
    elif amount == 0:
        txn.problem = "the amount is 0.00"
    return txn


def _block(index: int, rs: Node, kind: str) -> Block:
    if kind == "card":
        acct = rs.find("CCACCTFROM")
        account_type = "credit_card"
    else:
        acct = rs.find("BANKACCTFROM")
        account_type = _BANK_TYPES.get((acct.text("ACCTTYPE") if acct else "").upper(), "checking")
    tranlist = rs.find("BANKTRANLIST")
    ledger = rs.find("LEDGERBAL")
    b = Block(
        index=index, kind=kind, account_type=account_type,
        last4=last4(acct.text("ACCTID")) if acct else None,
        currency=rs.text("CURDEF").upper()[:3],
        start=ofx_date(tranlist.text("DTSTART")) if tranlist else None,
        end=ofx_date(tranlist.text("DTEND")) if tranlist else None,
        balance=ofx_amount(ledger.text("BALAMT")) if ledger else None,
        balance_date=ofx_date(ledger.text("DTASOF")) if ledger else None,
    )
    for line, t in enumerate((tranlist.find_all("STMTTRN") if tranlist else rs.find_all("STMTTRN")), 1):
        b.transactions.append(_row(line, t))
    return b


def parse(raw: bytes) -> OfxFile:
    """Read a QFX/OFX file. Raises Refused for an empty file, a web page, something that isn't OFX, a file with no
    bank or card statement (investment only, say) or with no transactions at all."""
    if not raw or not raw.strip():
        raise Refused(EMPTY)
    if not looks_like_ofx(raw[:SNIFF_BYTES]):
        raise Refused(HTML_REFUSED if looks_like_html(raw[:SNIFF_BYTES]) else NOT_OFX)
    text, encoding = decode(raw)
    version = "2.x" if re.search(r"<\?\s*OFX\b", text[:SNIFF_BYTES * 4], re.IGNORECASE) else "1.x"
    start = re.search(r"<\s*OFX\s*>", text, re.IGNORECASE)
    if start is None:
        raise Refused(NOT_OFX)
    tree = read_tree(text[start.start():])
    ofx = tree.find("OFX") or tree
    fi = ofx.find("FI")
    org = (fi.text("ORG") if fi else "") or ""
    blocks, skipped = [], []
    found = []
    for node in _statement_blocks(ofx):
        if node.name == "STMTRS":
            found.append((node, "bank"))
        elif node.name == "CCSTMTRS":
            found.append((node, "card"))
        else:
            skipped.append(node.name)
    for node, kind in found:
        blocks.append(_block(len(blocks), node, kind))
    if not blocks:
        raise Refused(ONLY_BANK_AND_CARD if skipped else NO_TRANSACTIONS)
    if not any(b.transactions for b in blocks):
        raise Refused(NO_TRANSACTIONS)
    notes = []
    if skipped:
        n = len(skipped)
        notes.append(f"{n} investment or loan statement{'s' if n != 1 else ''} left out. {ONLY_BANK_AND_CARD}")
    return OfxFile(version=version, encoding=encoding, org=org[:100], blocks=blocks, skipped=notes)


_OTHER_STATEMENTS = {"INVSTMTRS", "LOANSTMTRS", "LOANSTMTENDRS"}


def _statement_blocks(node: Node):
    """Every STMTRS / CCSTMTRS (and the investment and loan ones, to say they were skipped), in file order. A
    statement block's own inside is not searched further."""
    for c in node.children:
        if c.name in ("STMTRS", "CCSTMTRS") or c.name in _OTHER_STATEMENTS:
            yield c
        elif c.value is None:
            yield from _statement_blocks(c)


# ------------------------------------------------------------------ signs and labels

def card_sign_rule(block: Block) -> tuple[bool, str]:
    """(flip, the rule in words) for a credit card's rows. OFX writes a purchase as negative while Finance stores it
    positive (SPEC.md section 8), so a card's signs are flipped — unless most TRNTYPE=DEBIT rows are already positive,
    which is a bank that uses Finance's convention: then they're read as they are."""
    debits = [t for t in block.usable if t.trntype == "DEBIT"]
    positive = sum(1 for t in debits if t.amount > 0)
    if debits and positive * 2 > len(debits):
        return False, (f"Credit card, read as written: {positive} of {len(debits)} DEBIT rows are already positive "
                       "(this bank writes purchases the way Finance stores them).")
    return True, "Credit card: every sign flipped (OFX writes a purchase as negative; Finance stores it as positive)."


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def period_label(start: str | None, end: str | None) -> str:
    """'Mar 3 – Jun 1, 2026' (the year once when both ends share it), 'Dec 3, 2025 – Feb 1, 2026', or '' ."""
    def parts(d):
        try:
            y, m, dd = (int(x) for x in (d or "").split("-"))
            return y, _MONTHS[m - 1], dd
        except (ValueError, IndexError):
            return None
    s, e = parts(start), parts(end)
    if s and e:
        if s[0] == e[0]:
            return f"{s[1]} {s[2]} – {e[1]} {e[2]}, {e[0]}"
        return f"{s[1]} {s[2]}, {s[0]} – {e[1]} {e[2]}, {e[0]}"
    one = s or e
    return f"{one[1]} {one[2]}, {one[0]}" if one else ""


def day_before(iso: str) -> str | None:
    try:
        return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None
