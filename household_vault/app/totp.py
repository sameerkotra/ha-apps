"""TOTP codes (RFC 6238) from KeePassXC's `otp` field (`otpauth://totp/...`) or
the older `TOTP Seed` / `TOTP Settings` fields (read-only)."""
import base64
import hashlib
import hmac
import struct
import time
import urllib.parse

ALGS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class BadOtp(ValueError):
    pass


def _b32(secret: str) -> bytes:
    s = secret.replace(" ", "").replace("-", "").upper()
    s += "=" * (-len(s) % 8)
    try:
        key = base64.b32decode(s)
    except Exception:
        raise BadOtp("That secret isn't valid base32.")
    if not key:
        raise BadOtp("The secret is empty.")
    return key


def parse(value: str) -> dict:
    """otpauth URI or a bare base32 secret → {secret, digits, period, algorithm, issuer, account}."""
    v = (value or "").strip()
    if v.lower().startswith("otpauth://"):
        u = urllib.parse.urlparse(v)
        if u.netloc.lower() != "totp":
            raise BadOtp("Only time-based codes (otpauth://totp/…) are supported.")
        q = {k.lower(): vals[-1] for k, vals in urllib.parse.parse_qs(u.query).items()}
        label = urllib.parse.unquote(u.path.lstrip("/"))
        issuer, _, account = label.partition(":") if ":" in label else ("", "", label)
        out = {"secret": q.get("secret", ""), "digits": int(q.get("digits", 6)), "period": int(q.get("period", 30)),
               "algorithm": q.get("algorithm", "SHA1").upper(), "issuer": q.get("issuer", issuer), "account": account}
    else:
        out = {"secret": v, "digits": 6, "period": 30, "algorithm": "SHA1", "issuer": "", "account": ""}
    if out["algorithm"] not in ALGS:
        raise BadOtp("Unsupported algorithm.")
    if not 6 <= out["digits"] <= 8 or not 1 <= out["period"] <= 300:
        raise BadOtp("Unsupported digits or period.")
    _b32(out["secret"])
    return out


def from_legacy(seed: str, settings: str | None) -> dict:
    """KeePassXC's old `TOTP Seed` + `TOTP Settings` ("30;6")."""
    period, digits = 30, 6
    if settings:
        parts = settings.split(";")
        try:
            period = int(parts[0])
            digits = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 6
        except ValueError:
            pass
    return {"secret": seed, "digits": digits, "period": period, "algorithm": "SHA1", "issuer": "", "account": ""}


def to_uri(p: dict, label: str = "account") -> str:
    q = {"secret": p["secret"].replace(" ", "").upper(), "period": p["period"], "digits": p["digits"],
         "algorithm": p["algorithm"]}
    if p.get("issuer"):
        q["issuer"] = p["issuer"]
    return f"otpauth://totp/{urllib.parse.quote(label)}?{urllib.parse.urlencode(q)}"


# ---------- KeePass 2.47+ (its own TOTP fields, next to KeePassXC's `otp`) ----------
KEEPASS_SECRET = "TimeOtp-Secret-Base32"
KEEPASS_FIELDS = (KEEPASS_SECRET, "TimeOtp-Length", "TimeOtp-Period", "TimeOtp-Algorithm")
KEEPASS_OTHER_SECRETS = ("TimeOtp-Secret", "TimeOtp-Secret-Hex", "TimeOtp-Secret-Base64")
_KP_ALGS = {"SHA1": "HMAC-SHA-1", "SHA256": "HMAC-SHA-256", "SHA512": "HMAC-SHA-512"}


def keepass_fields(p: dict) -> dict:
    """The fields KeePass (2.47+) reads a TOTP from: `TimeOtp-Secret-Base32`, plus length, period and
    algorithm only when they aren't KeePass's defaults (6 digits, 30 s, HMAC-SHA-1)."""
    out = {KEEPASS_SECRET: p["secret"].replace(" ", "").upper().rstrip("=")}
    if p["digits"] != 6:
        out["TimeOtp-Length"] = str(p["digits"])
    if p["period"] != 30:
        out["TimeOtp-Period"] = str(p["period"])
    if p["algorithm"] != "SHA1":
        out["TimeOtp-Algorithm"] = _KP_ALGS[p["algorithm"]]
    return out


def from_keepass(fields: dict) -> dict | None:
    """A TOTP written by KeePass itself (only the Base32 secret form is read)."""
    secret = (fields.get(KEEPASS_SECRET) or "").strip()
    if not secret:
        return None
    alg = {v: k for k, v in _KP_ALGS.items()}.get((fields.get("TimeOtp-Algorithm") or "HMAC-SHA-1").strip().upper(), None)
    if alg is None:
        raise BadOtp("Unsupported algorithm.")
    try:
        p = {"secret": secret, "digits": int(fields.get("TimeOtp-Length") or 6), "period": int(fields.get("TimeOtp-Period") or 30),
             "algorithm": alg, "issuer": "", "account": ""}
    except ValueError:
        raise BadOtp("Unsupported digits or period.")
    if not 6 <= p["digits"] <= 8 or not 1 <= p["period"] <= 300:
        raise BadOtp("Unsupported digits or period.")
    _b32(p["secret"])
    return p


def code(p: dict, at: float | None = None) -> dict:
    now = time.time() if at is None else at
    counter = int(now // p["period"])
    mac = hmac.new(_b32(p["secret"]), struct.pack(">Q", counter), ALGS[p["algorithm"]]).digest()
    off = mac[-1] & 0x0F
    num = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** p["digits"])
    return {"code": str(num).zfill(p["digits"]), "period": p["period"],
            "remaining": p["period"] - int(now % p["period"])}


# ---------- Google Authenticator export (otpauth-migration://offline?data=…) ----------
_MIG_ALGS = {0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512"}   # 4 = MD5: not supported
_MIG_DIGITS = {0: 6, 1: 6, 2: 8}


def _varint(buf: bytes, i: int) -> tuple:
    shift = result = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise BadOtp("That export code is damaged.")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _varint(buf, i)
        elif wt == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise BadOtp("That export code is damaged.")
            val, i = buf[i:i + n], i + n
        elif wt == 1:
            val, i = buf[i:i + 8], i + 8
        elif wt == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise BadOtp("That export code is damaged.")
        yield num, val


def parse_migration(uri: str) -> dict:
    """Decode Google Authenticator's export QR → {entries: [...], batch: (index, size)}.
    Each entry: {issuer, account, uri, ok, reason}. HOTP and MD5 entries are listed but not usable."""
    u = urllib.parse.urlparse(uri.strip())
    if u.scheme.lower() != "otpauth-migration":
        raise BadOtp("That isn't an authenticator export code.")
    raw = None
    for part in u.query.split("&"):
        k, _, v = part.partition("=")
        if k == "data":
            raw = urllib.parse.unquote(v).replace(" ", "+")     # '+' must stay '+', not a space
    if not raw:
        raise BadOtp("That export code has no data.")
    try:
        payload = base64.b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        raise BadOtp("That export code is damaged.")
    entries, batch_index, batch_size = [], 0, 1
    for num, val in _fields(payload):
        if num == 1 and isinstance(val, bytes):
            p = {"secret": b"", "name": "", "issuer": "", "alg": 0, "digits": 0, "type": 0}
            for n2, v2 in _fields(val):
                if n2 == 1:
                    p["secret"] = v2
                elif n2 == 2:
                    p["name"] = v2.decode("utf-8", "replace")
                elif n2 == 3:
                    p["issuer"] = v2.decode("utf-8", "replace")
                elif n2 == 4:
                    p["alg"] = v2
                elif n2 == 5:
                    p["digits"] = v2
                elif n2 == 6:
                    p["type"] = v2
            name = p["name"]
            issuer = p["issuer"]
            if ":" in name and not issuer:
                issuer, _, name = name.partition(":")
            elif issuer and name.startswith(issuer + ":"):
                name = name[len(issuer) + 1:]
            e = {"issuer": issuer.strip(), "account": name.strip(), "uri": None, "ok": True, "reason": None}
            if p["type"] == 1:
                e.update(ok=False, reason="Counter-based (HOTP) codes aren't supported.")
            elif p["alg"] not in _MIG_ALGS:
                e.update(ok=False, reason="Unsupported algorithm.")
            elif not p["secret"]:
                e.update(ok=False, reason="No secret.")
            else:
                params = {"secret": base64.b32encode(p["secret"]).decode().rstrip("="), "period": 30,
                          "digits": _MIG_DIGITS.get(p["digits"], 6), "algorithm": _MIG_ALGS[p["alg"]],
                          "issuer": e["issuer"]}
                label = f"{e['issuer']}:{e['account']}" if e["issuer"] else (e["account"] or "account")
                e["uri"] = to_uri(params, label)
            entries.append(e)
        elif num == 3 and isinstance(val, int):
            batch_size = val or 1
        elif num == 4 and isinstance(val, int):
            batch_index = val
    if not entries:
        raise BadOtp("That export code has no accounts in it.")
    return {"entries": entries, "batch": [batch_index + 1, batch_size]}


def describe(text: str) -> dict:
    """What a scanned or pasted QR text is: a TOTP link, an export batch, or not usable."""
    t = (text or "").strip()
    low = t.lower()
    if low.startswith("otpauth-migration://"):
        return dict(parse_migration(t), kind="migration")
    if low.startswith("otpauth://hotp"):
        raise BadOtp("That's a counter-based (HOTP) code — only time-based codes are supported.")
    if low.startswith("otpauth://"):
        p = parse(t)
        return {"kind": "totp", "uri": t, "issuer": p["issuer"], "account": p["account"]}
    if "://" in low or low.startswith("wifi:"):
        raise BadOtp("That QR code isn't a 2FA setup code.")
    try:
        p = parse(t)                                   # a bare base32 secret (spaces allowed)
    except BadOtp:
        raise BadOtp("That isn't a 2FA setup code or key.")
    return {"kind": "totp", "uri": to_uri(p, "account"), "issuer": "", "account": ""}
