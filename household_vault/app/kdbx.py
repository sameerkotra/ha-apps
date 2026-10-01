"""KeePass KDBX files: read KDBX 3.1 and 4.x, write KDBX 4.0.

Built on well-known primitives only — `cryptography` (AES-256-CBC/ECB,
ChaCha20, HMAC-SHA256, Argon2id), `hashlib` (SHA-256/512) and, for Argon2d,
`argon2-cffi` when installed (pure-Python `argon2py` otherwise). The file
format follows the KeePass/KeePassXC documentation of KDBX 4.

The XML document is kept as the source of truth (an ElementTree), so
everything another KeePass app wrote — fields, icons, custom data, history,
attachments — is written back untouched. `Database` adds a small API for
groups, entries and fields on top of it. Protected values are held decrypted
in memory (the app is the trusted side, SPEC §4) and re-encrypted with a
fresh inner-stream key on every save.
"""
import base64
import gzip
import hashlib
import hmac
import io
import os
import struct
import uuid as uuidlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import argon2py

SIG1, SIG2 = 0x9AA2D903, 0xB54BFB67
CIPHER_AES256 = bytes.fromhex("31c1f2e6bf714350be5805216afc5aff")
CIPHER_CHACHA20 = bytes.fromhex("d6038a2b8b6f4cb5a524339a31dbb59a")
CIPHER_TWOFISH = bytes.fromhex("ad68f29f576f4bb9a36ad47af965346c")
KDF_AES = bytes.fromhex("c9d9f39a628a4460bf740d08c18a4fea")
KDF_ARGON2D = bytes.fromhex("ef636ddf8c29444b91f7a9a403e30a0c")
KDF_ARGON2ID = bytes.fromhex("9e298b1956db4773b23dfc3ec6f0a1e6")
SALSA20_IV = bytes.fromhex("e830094b97205d2a")
STREAM_SALSA20, STREAM_CHACHA20 = 2, 3
EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)
ZERO_UUID = base64.b64encode(b"\0" * 16).decode()
STANDARD_FIELDS = ("Title", "UserName", "Password", "URL", "Notes")

# Personal vaults / vaults with a person-chosen password (SPEC §5.1)
STRONG_KDF = {"memory": 64 * 1024 * 1024, "iterations": 3, "parallelism": 2}
# Vaults locked with a random 32-byte password: a slow KDF adds nothing
LIGHT_KDF = {"memory": 1024 * 1024, "iterations": 1, "parallelism": 1}
MIN_ITERATIONS = 3


class KdbxError(Exception):
    """A file that can't be read (not KDBX, damaged, or an unsupported feature)."""


class WrongKey(KdbxError):
    """The password (or key file) doesn't open this file."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def fmt_time(dt: datetime) -> str:
    """KDBX 4 time: base64 of int64 seconds since 0001-01-01 UTC."""
    secs = int((dt - EPOCH).total_seconds())
    return base64.b64encode(struct.pack("<q", secs)).decode()


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    t = text.strip()
    if "-" in t and ":" in t:                                 # KDBX 3 ISO text
        try:
            return datetime.strptime(t.rstrip("Z")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        raw = base64.b64decode(t)
        if len(raw) == 8:
            from datetime import timedelta
            return EPOCH + timedelta(seconds=struct.unpack("<q", raw)[0])
    except Exception:
        return None
    return None


def new_uuid() -> str:
    return base64.b64encode(uuidlib.uuid4().bytes).decode()


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def _chacha(key: bytes, nonce12: bytes):
    return Cipher(algorithms.ChaCha20(key, b"\0\0\0\0" + nonce12), mode=None).encryptor()


class _Salsa20:
    """Salsa20/20 keystream (KDBX 3 inner stream only)."""

    def __init__(self, key: bytes, nonce: bytes):
        c = b"expand 32-byte k"
        k = struct.unpack("<8I", key)
        n = struct.unpack("<2I", nonce)
        s = struct.unpack("<4I", c)
        self.state = [s[0], k[0], k[1], k[2], k[3], s[1], n[0], n[1], 0, 0, s[2], k[4], k[5], k[6], k[7], s[3]]
        self.buf = b""

    @staticmethod
    def _rotl(v, c):
        return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))

    def _block(self):
        x = self.state[:]
        r = self._rotl
        for _ in range(10):
            for a, b, c, d in ((0, 4, 8, 12), (5, 9, 13, 1), (10, 14, 2, 6), (15, 3, 7, 11),
                               (0, 1, 2, 3), (5, 6, 7, 4), (10, 11, 8, 9), (15, 12, 13, 14)):
                x[b] ^= r((x[a] + x[d]) & 0xFFFFFFFF, 7)
                x[c] ^= r((x[b] + x[a]) & 0xFFFFFFFF, 9)
                x[d] ^= r((x[c] + x[b]) & 0xFFFFFFFF, 13)
                x[a] ^= r((x[d] + x[c]) & 0xFFFFFFFF, 18)
        out = struct.pack("<16I", *[(x[i] + self.state[i]) & 0xFFFFFFFF for i in range(16)])
        self.state[8] = (self.state[8] + 1) & 0xFFFFFFFF
        if self.state[8] == 0:
            self.state[9] = (self.state[9] + 1) & 0xFFFFFFFF
        return out

    def update(self, data: bytes) -> bytes:
        while len(self.buf) < len(data):
            self.buf += self._block()
        ks, self.buf = self.buf[:len(data)], self.buf[len(data):]
        return bytes(a ^ b for a, b in zip(data, ks))


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def keyfile_hash(data: bytes) -> bytes:
    """The 32-byte key from a key file: KeePass XML (v1 base64 / v2 hex), 32 raw bytes, 64 hex chars, or SHA-256."""
    try:
        root = ET.fromstring(data)
        if root.tag == "KeyFile":
            ver = (root.findtext("Meta/Version") or "1.0").strip()
            node = root.find("Key/Data")
            if node is not None and node.text:
                if ver.startswith("2"):
                    return bytes.fromhex("".join(node.text.split()))
                return base64.b64decode(node.text.strip())
    except ET.ParseError:
        pass
    if len(data) == 32:
        return data
    if len(data) == 64:
        try:
            return bytes.fromhex(data.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            pass
    return hashlib.sha256(data).digest()


def composite_key(password: str | None, keyfile: bytes | None = None) -> bytes:
    parts = b""
    if password is not None:
        parts += hashlib.sha256(password.encode("utf-8")).digest()
    if keyfile is not None:
        parts += keyfile_hash(keyfile)
    return hashlib.sha256(parts).digest()


def argon2_raw(secret: bytes, salt: bytes, iterations: int, memory_bytes: int, parallelism: int,
               variant_d: bool, version: int = 0x13) -> bytes:
    mem_kib = memory_bytes // 1024
    try:                                                   # the Docker image installs argon2-cffi
        from argon2.low_level import Type, hash_secret_raw
        return hash_secret_raw(secret, salt, iterations, mem_kib, parallelism, 32,
                               Type.D if variant_d else Type.ID, version)
    except ImportError:
        pass
    if not variant_d and version == 0x13:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        return Argon2id(salt=salt, length=32, iterations=iterations, lanes=parallelism,
                        memory_cost=mem_kib).derive(secret)
    return argon2py.argon2(secret, salt, iterations, mem_kib, parallelism, 32,
                           argon2py.TYPE_D if variant_d else argon2py.TYPE_ID, version=version)


def aes_kdf(key: bytes, seed: bytes, rounds: int) -> bytes:
    enc = Cipher(algorithms.AES(seed), modes.ECB()).encryptor()
    data = key
    for _ in range(rounds):
        data = enc.update(data)
    return hashlib.sha256(data).digest()


def transform_key(composite: bytes, kdf: dict) -> bytes:
    kid = kdf.get("$UUID")
    if kid == KDF_AES:
        return aes_kdf(composite, kdf["S"], kdf["R"])
    if kid in (KDF_ARGON2D, KDF_ARGON2ID):
        return argon2_raw(composite, kdf["S"], kdf["I"], kdf["M"], kdf["P"], kid == KDF_ARGON2D, kdf.get("V", 0x13))
    raise KdbxError("This file uses a key derivation function Household Vault doesn't support.")


# ---------------------------------------------------------------------------
# Variant dictionary (KDBX 4 KDF parameters, public custom data)
# ---------------------------------------------------------------------------

def vd_parse(data: bytes) -> dict:
    out, pos = {}, 2
    if len(data) < 2 or data[1] != 1:
        raise KdbxError("Unsupported KDF parameters.")
    while pos < len(data):
        t = data[pos]
        pos += 1
        if t == 0:
            break
        (klen,) = struct.unpack_from("<i", data, pos); pos += 4
        key = data[pos:pos + klen].decode("utf-8"); pos += klen
        (vlen,) = struct.unpack_from("<i", data, pos); pos += 4
        raw = data[pos:pos + vlen]; pos += vlen
        if t == 0x04:
            out[key] = struct.unpack("<I", raw)[0]
        elif t == 0x05:
            out[key] = struct.unpack("<Q", raw)[0]
        elif t == 0x08:
            out[key] = raw != b"\0"
        elif t == 0x0C:
            out[key] = struct.unpack("<i", raw)[0]
        elif t == 0x0D:
            out[key] = struct.unpack("<q", raw)[0]
        elif t == 0x18:
            out[key] = raw.decode("utf-8")
        else:
            out[key] = bytes(raw)
    return out


def vd_build(items: list) -> bytes:
    """items: [(key, type, value)] in order."""
    out = bytearray(b"\x00\x01")
    for key, t, value in items:
        if t == 0x04:
            raw = struct.pack("<I", value)
        elif t == 0x05:
            raw = struct.pack("<Q", value)
        elif t == 0x08:
            raw = b"\x01" if value else b"\x00"
        elif t == 0x18:
            raw = value.encode("utf-8")
        else:
            raw = bytes(value)
        k = key.encode("utf-8")
        out += bytes([t]) + struct.pack("<i", len(k)) + k + struct.pack("<i", len(raw)) + raw
    out += b"\x00"
    return bytes(out)


def kdf_params(kind: dict) -> dict:
    return {"$UUID": KDF_ARGON2ID, "S": os.urandom(32), "I": kind["iterations"], "M": kind["memory"],
            "P": kind["parallelism"], "V": 0x13}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def is_kdbx(data: bytes) -> bool:
    return len(data) >= 12 and struct.unpack_from("<II", data, 0) == (SIG1, SIG2)


def header_info(data: bytes) -> dict:
    """The unencrypted outer header: {major, minor, cipher, kdf{...}} (for checks without the key)."""
    if not is_kdbx(data):
        raise KdbxError("That isn't a KeePass (.kdbx) file.")
    minor, major = struct.unpack_from("<HH", data, 8)
    fields, end = _read_outer_header(data, major)
    info = {"major": major, "minor": minor, "cipher": fields.get(2), "kdf": None}
    if major >= 4 and 11 in fields:
        info["kdf"] = vd_parse(fields[11])
    elif 6 in fields:
        info["kdf"] = {"$UUID": KDF_AES, "R": struct.unpack("<Q", fields[6])[0]}
    return info


def _read_outer_header(data: bytes, major: int):
    pos, fields = 12, {}
    size_fmt, size_len = ("<i", 4) if major >= 4 else ("<H", 2)
    while True:
        if pos + 1 + size_len > len(data):
            raise KdbxError("The file is damaged (header).")
        fid = data[pos]; pos += 1
        (size,) = struct.unpack_from(size_fmt, data, pos); pos += size_len
        value = data[pos:pos + size]; pos += size
        if fid == 0:
            return fields, pos
        fields[fid] = value


def _hmac_block_key(base: bytes, index: int) -> bytes:
    return hashlib.sha512(struct.pack("<Q", index) + base).digest()


def _decrypt(cipher_id: bytes, key: bytes, iv: bytes, data: bytes) -> bytes:
    if cipher_id == CIPHER_AES256:
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        raw = dec.update(data) + dec.finalize()
        unpad = padding.PKCS7(128).unpadder()
        try:
            return unpad.update(raw) + unpad.finalize()
        except ValueError:
            raise WrongKey("Wrong password.")
    if cipher_id == CIPHER_CHACHA20:
        return _chacha(key, iv).update(data)
    if cipher_id == CIPHER_TWOFISH:
        raise KdbxError("This file is encrypted with Twofish, which Household Vault doesn't support. "
                        "Change the cipher to AES-256 or ChaCha20 in KeePassXC first.")
    raise KdbxError("This file uses an unknown cipher.")


def _encrypt(cipher_id: bytes, key: bytes, iv: bytes, data: bytes) -> bytes:
    if cipher_id == CIPHER_AES256:
        pad = padding.PKCS7(128).padder()
        padded = pad.update(data) + pad.finalize()
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return enc.update(padded) + enc.finalize()
    if cipher_id == CIPHER_CHACHA20:
        return _chacha(key, iv).update(data)
    raise KdbxError("Unsupported cipher.")


def open_bytes(data: bytes, password: str | None, keyfile: bytes | None = None) -> "Database":
    return open_with_composite(data, composite_key(password, keyfile))


def open_with_composite(data: bytes, composite: bytes) -> "Database":
    if not is_kdbx(data):
        raise KdbxError("That isn't a KeePass (.kdbx) file.")
    minor, major = struct.unpack_from("<HH", data, 8)
    if major == 4:
        return _open4(data, composite)
    if major == 3:
        return _open3(data, composite)
    raise KdbxError(f"KDBX version {major}.{minor} isn't supported (KDBX 3.1 and 4.x are).")


def _open4(data: bytes, composite: bytes) -> "Database":
    fields, pos = _read_outer_header(data, 4)
    header = data[:pos]
    if data[pos:pos + 32] != hashlib.sha256(header).digest():
        raise KdbxError("The file is damaged (header checksum).")
    kdf = vd_parse(fields[11])
    transformed = transform_key(composite, kdf)
    seed = fields[4]
    base = hashlib.sha512(seed + transformed + b"\x01").digest()
    hdr_mac = hmac.new(_hmac_block_key(base, 0xFFFFFFFFFFFFFFFF), header, hashlib.sha256).digest()
    if not hmac.compare_digest(hdr_mac, data[pos + 32:pos + 64]):
        raise WrongKey("Wrong password.")
    pos += 64
    blocks, index = [], 0
    while True:
        mac = data[pos:pos + 32]
        (size,) = struct.unpack_from("<i", data, pos + 32)
        block = data[pos + 36:pos + 36 + size]
        pos += 36 + size
        expect = hmac.new(_hmac_block_key(base, index), struct.pack("<Qi", index, size) + block, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expect):
            raise KdbxError("The file is damaged (block checksum).")
        if size == 0:
            break
        blocks.append(block)
        index += 1
    key = hashlib.sha256(seed + transformed).digest()
    plain = _decrypt(fields[2], key, fields[7], b"".join(blocks))
    if struct.unpack("<I", fields.get(3, b"\0\0\0\0"))[0] == 1:
        plain = gzip.decompress(plain)
    # inner header
    ipos, stream_id, stream_key, binaries = 0, None, None, []
    while True:
        fid = plain[ipos]
        (size,) = struct.unpack_from("<i", plain, ipos + 1)
        val = plain[ipos + 5:ipos + 5 + size]
        ipos += 5 + size
        if fid == 0:
            break
        if fid == 1:
            stream_id = struct.unpack("<I", val)[0]
        elif fid == 2:
            stream_key = val
        elif fid == 3:
            binaries.append((val[0] & 1 == 1, val[1:]))
    stream = _inner_stream(stream_id, stream_key)
    root = _parse_xml(plain[ipos:])
    _unprotect(root, stream)
    db = Database(root)
    db.binaries = binaries
    db.kdf = kdf
    db.cipher = fields[2]
    db.source_version = (4, struct.unpack_from("<H", data, 8)[0])
    db.transformed = transformed
    db.composite = composite
    return db


def _open3(data: bytes, composite: bytes) -> "Database":
    fields, pos = _read_outer_header(data, 3)
    kdf = {"$UUID": KDF_AES, "S": fields[5], "R": struct.unpack("<Q", fields[6])[0]}
    transformed = transform_key(composite, kdf)
    key = hashlib.sha256(fields[4] + transformed).digest()
    plain = _decrypt(fields[2], key, fields[7], data[pos:])
    if plain[:32] != fields[9]:
        raise WrongKey("Wrong password.")
    pos, chunks = 32, []
    while True:
        idx, = struct.unpack_from("<I", plain, pos)
        digest = plain[pos + 4:pos + 36]
        (size,) = struct.unpack_from("<i", plain, pos + 36)
        block = plain[pos + 40:pos + 40 + size]
        pos += 40 + size
        if size == 0:
            break
        if hashlib.sha256(block).digest() != digest:
            raise KdbxError("The file is damaged (block hash).")
        chunks.append(block)
    body = b"".join(chunks)
    if struct.unpack("<I", fields.get(3, b"\0\0\0\0"))[0] == 1:
        body = gzip.decompress(body)
    stream = _inner_stream(struct.unpack("<I", fields[10])[0], fields[8])
    root = _parse_xml(body)
    _unprotect(root, stream)
    db = Database(root)
    # KDBX 3 keeps attachments in Meta/Binaries: move them to the KDBX 4 pool
    pool, idmap = [], {}
    meta_bins = root.find("Meta/Binaries")
    if meta_bins is not None:
        for b in list(meta_bins):
            raw = base64.b64decode(b.text or "")
            if b.get("Compressed", "").lower() == "true":
                raw = gzip.decompress(raw)
            idmap[b.get("ID")] = len(pool)
            pool.append((False, raw))
        root.find("Meta").remove(meta_bins)
    for v in root.iter("Value"):
        if v.get("Ref") is not None and v.get("Ref") in idmap:
            v.set("Ref", str(idmap[v.get("Ref")]))
    for t in root.iter():
        if t.tag.endswith("Time") and t.text and "-" in t.text:
            dt = parse_time(t.text)
            if dt:
                t.text = fmt_time(dt)
    hh = root.find("Meta/HeaderHash")
    if hh is not None:
        root.find("Meta").remove(hh)
    db.binaries = pool
    db.kdf = kdf
    db.cipher = fields[2]
    db.source_version = (3, 1)
    return db


def _inner_stream(stream_id, key):
    if stream_id == STREAM_CHACHA20:
        h = hashlib.sha512(key).digest()
        return _chacha(h[:32], h[32:44])
    if stream_id == STREAM_SALSA20:
        return _Salsa20(hashlib.sha256(key).digest(), SALSA20_IV)
    if stream_id in (None, 0):
        return None
    raise KdbxError("Unsupported inner stream cipher.")


def _parse_xml(raw: bytes):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise KdbxError(f"The file's contents couldn't be read ({e}).")


def _unprotect(root, stream) -> None:
    for el in root.iter():
        if el.tag == "Value" and el.get("Protected", "").lower() == "true":
            raw = base64.b64decode(el.text or "")
            el.text = stream.update(raw).decode("utf-8", errors="replace") if stream else ""


# ---------------------------------------------------------------------------
# Writing (always KDBX 4.0)
# ---------------------------------------------------------------------------

def save_bytes(db: "Database", password: str | None = None, keyfile: bytes | None = None,
               composite: bytes | None = None, kdf: dict | None = None, new_salt: bool = False) -> bytes:
    """Serialise as KDBX 4 with a fresh master seed, IV and inner-stream key.

    `kdf`: new KDF parameters (always with a new salt). Otherwise the database's
    own KDF settings are kept — with the **same salt** unless `new_salt`, so the
    transformed key cached from the last open or save (`db.transformed`) can be
    reused and saving doesn't cost an Argon2 run. The master seed is new on every
    save, so the encryption key is still new each time. Pass `new_salt=True` (or a
    `kdf`) when the password changes. AES-KDF from an old file becomes Argon2id."""
    if composite is None:
        composite = composite_key(password, keyfile)
    if kdf:
        params = dict(kdf)
        params["S"] = os.urandom(32)
    else:
        params = dict(db.kdf or kdf_params(STRONG_KDF))
        if params.get("$UUID") not in (KDF_ARGON2ID, KDF_ARGON2D):
            params = kdf_params(STRONG_KDF)
        if new_salt or "S" not in params:
            params["S"] = os.urandom(32)
    params.setdefault("V", 0x13)
    cipher = db.cipher if db.cipher in (CIPHER_AES256, CIPHER_CHACHA20) else CIPHER_AES256
    same = (db.transformed is not None and db.kdf is not None and db.kdf.get("S") == params["S"]
            and db.kdf.get("$UUID") == params["$UUID"] and db.kdf.get("I") == params["I"]
            and db.kdf.get("M") == params["M"] and db.kdf.get("P") == params["P"]
            and db.composite == composite)
    transformed = db.transformed if same else transform_key(composite, params)
    seed = os.urandom(32)
    iv = os.urandom(16 if cipher == CIPHER_AES256 else 12)
    kdf_items = [("$UUID", 0x42, params["$UUID"]), ("S", 0x42, params["S"]), ("P", 0x04, params["P"]),
                 ("M", 0x05, params["M"]), ("I", 0x05, params["I"]), ("V", 0x04, params["V"])]

    def field(fid, value):
        return bytes([fid]) + struct.pack("<i", len(value)) + value
    minor = 1 if db.source_version >= (4, 1) else 0          # 4.1 files may hold 4.1-only XML
    header = (struct.pack("<IIHH", SIG1, SIG2, minor, 4) + field(2, cipher) + field(3, struct.pack("<I", 1))
              + field(4, seed) + field(7, iv) + field(11, vd_build(kdf_items)) + field(0, b"\r\n\r\n"))
    base = hashlib.sha512(seed + transformed + b"\x01").digest()
    out = io.BytesIO()
    out.write(header)
    out.write(hashlib.sha256(header).digest())
    out.write(hmac.new(_hmac_block_key(base, 0xFFFFFFFFFFFFFFFF), header, hashlib.sha256).digest())
    # inner header + protected XML
    stream_key = os.urandom(64)
    h = hashlib.sha512(stream_key).digest()
    stream = _chacha(h[:32], h[32:44])
    inner = bytearray()
    inner += field(1, struct.pack("<I", STREAM_CHACHA20)) + field(2, stream_key)
    for protected, raw in db.binaries:
        inner += field(3, bytes([1 if protected else 0]) + raw)
    inner += field(0, b"")
    xml = db.to_xml(stream)
    payload = gzip.compress(bytes(inner) + xml, compresslevel=6)
    key = hashlib.sha256(seed + transformed).digest()
    enc = _encrypt(cipher, key, iv, payload)
    index, pos, block_size = 0, 0, 1024 * 1024
    while True:
        block = enc[pos:pos + block_size]
        pos += len(block)
        mac = hmac.new(_hmac_block_key(base, index), struct.pack("<Qi", index, len(block)) + block, hashlib.sha256).digest()
        out.write(mac + struct.pack("<i", len(block)) + block)
        index += 1
        if not block:
            break
    db.kdf = params
    db.cipher = cipher
    db.transformed = transformed
    db.composite = composite
    return out.getvalue()


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------

def _group_fields(g, name: str, icon: str) -> None:
    """The child elements of a new KeePass group (as KeePassXC writes them)."""
    _sub(g, "UUID", new_uuid())
    _sub(g, "Name", name)
    _sub(g, "Notes", "")
    _sub(g, "IconID", icon)
    _times(g)
    _sub(g, "IsExpanded", "True")
    _sub(g, "DefaultAutoTypeSequence", "")
    _sub(g, "EnableAutoType", "null")
    _sub(g, "EnableSearching", "null")
    _sub(g, "LastTopVisibleEntry", ZERO_UUID)


def _times(parent, when: datetime | None = None):
    t = when or now_utc()
    times = _sub(parent, "Times")
    for tag in ("CreationTime", "LastModificationTime", "LastAccessTime"):
        _sub(times, tag, fmt_time(t))
    _sub(times, "ExpiryTime", fmt_time(t))
    _sub(times, "Expires", "False")
    _sub(times, "UsageCount", "0")
    _sub(times, "LocationChanged", fmt_time(t))
    return times


class Database:
    def __init__(self, root):
        self.root = root
        self.binaries = []
        self.kdf = None
        self.cipher = CIPHER_AES256
        self.source_version = (4, 0)
        self.transformed = None          # cached KDF output for the current composite key and salt
        self.composite = None
        self._index()

    # ---------- creation ----------
    @classmethod
    def create(cls, name: str, kdf: dict | None = None) -> "Database":
        root = ET.Element("KeePassFile")
        meta = _sub(root, "Meta")
        t = fmt_time(now_utc())
        _sub(meta, "Generator", "HouseholdVault")
        _sub(meta, "DatabaseName", name)
        _sub(meta, "DatabaseNameChanged", t)
        _sub(meta, "DatabaseDescription", "")
        _sub(meta, "DatabaseDescriptionChanged", t)
        _sub(meta, "DefaultUserName", "")
        _sub(meta, "DefaultUserNameChanged", t)
        _sub(meta, "MaintenanceHistoryDays", "365")
        _sub(meta, "Color", "")
        _sub(meta, "MasterKeyChanged", t)
        _sub(meta, "MasterKeyChangeRec", "-1")
        _sub(meta, "MasterKeyChangeForce", "-1")
        mp = _sub(meta, "MemoryProtection")
        for tag, v in (("ProtectTitle", "False"), ("ProtectUserName", "False"), ("ProtectPassword", "True"),
                       ("ProtectURL", "False"), ("ProtectNotes", "False")):
            _sub(mp, tag, v)
        _sub(meta, "RecycleBinEnabled", "True")
        _sub(meta, "RecycleBinUUID", ZERO_UUID)
        _sub(meta, "RecycleBinChanged", t)
        _sub(meta, "EntryTemplatesGroup", ZERO_UUID)
        _sub(meta, "EntryTemplatesGroupChanged", t)
        _sub(meta, "HistoryMaxItems", "10")
        _sub(meta, "HistoryMaxSize", "6291456")
        _sub(meta, "LastSelectedGroup", ZERO_UUID)
        _sub(meta, "LastTopVisibleGroup", ZERO_UUID)
        _sub(meta, "SettingsChanged", t)
        rt = _sub(root, "Root")
        g = _sub(rt, "Group")
        _group_fields(g, name, "48")
        _sub(rt, "DeletedObjects")
        db = cls(root)
        db.kdf = kdf_params(kdf or STRONG_KDF)
        return db

    # ---------- indexes ----------
    def _index(self):
        self.parent = {}
        self.groups = {}
        self.entries = {}
        root_group = self.root.find("Root/Group")
        if root_group is None:
            raise KdbxError("The file has no root group.")
        self.root_group = root_group
        stack = [root_group]
        while stack:
            g = stack.pop()
            self.groups[g.findtext("UUID")] = g
            for child in g:
                if child.tag == "Group":
                    self.parent[child] = g
                    stack.append(child)
                elif child.tag == "Entry":
                    self.parent[child] = g
                    self.entries[child.findtext("UUID")] = child

    def reindex(self):
        self._index()

    # ---------- meta ----------
    @property
    def name(self) -> str:
        return self.root.findtext("Meta/DatabaseName") or ""

    def set_name(self, name: str):
        self._meta_set("DatabaseName", name)
        self._meta_set("DatabaseNameChanged", fmt_time(now_utc()))
        self.root_group.find("Name").text = name

    def _meta_set(self, tag, text):
        meta = self.root.find("Meta")
        el = meta.find(tag)
        if el is None:
            el = _sub(meta, tag)
        el.text = text

    def history_max_items(self) -> int:
        try:
            return int(self.root.findtext("Meta/HistoryMaxItems") or 10)
        except ValueError:
            return 10

    # ---------- recycle bin ----------
    def recycle_bin(self, create: bool = False):
        enabled = (self.root.findtext("Meta/RecycleBinEnabled") or "True").lower() == "true"
        uid = self.root.findtext("Meta/RecycleBinUUID") or ZERO_UUID
        g = self.groups.get(uid)
        if g is not None or not create or not enabled:
            return g
        g = self.add_group(self.root_group, "Recycle Bin", icon="43")
        es = g.find("EnableSearching")
        es.text = "false"
        self._meta_set("RecycleBinUUID", g.findtext("UUID"))
        self._meta_set("RecycleBinChanged", fmt_time(now_utc()))
        return g

    def in_recycle_bin(self, el) -> bool:
        rb = self.recycle_bin()
        if rb is None:
            return False
        node = el
        while node is not None:
            if node is rb:
                return True
            node = self.parent.get(node)
        return False

    # ---------- groups ----------
    def group_path(self, g) -> list:
        out = []
        node = g
        while node is not None and node is not self.root_group:
            out.append(node.findtext("Name") or "")
            node = self.parent.get(node)
        return list(reversed(out))

    def add_group(self, parent, name: str, icon: str = "48"):
        g = ET.Element("Group")
        _group_fields(g, name, icon)
        # groups go after entries' siblings: KeePass order is entries then groups — append is fine
        parent.append(g)
        self.parent[g] = parent
        self.groups[g.findtext("UUID")] = g
        return g

    def touch(self, el, location: bool = False):
        t = fmt_time(now_utc())
        times = el.find("Times")
        if times is None:
            times = _times(el)
        for tag in ("LastModificationTime", "LastAccessTime") + (("LocationChanged",) if location else ()):
            node = times.find(tag)
            if node is None:
                node = _sub(times, tag)
            node.text = t

    def move(self, el, new_parent):
        old = self.parent.get(el)
        if old is not None:
            old.remove(el)
        if el.tag == "Group":
            # entries first, then groups (KeePass order)
            new_parent.append(el)
        else:
            idx = next((i for i, c in enumerate(list(new_parent)) if c.tag == "Group"), None)
            if idx is None:
                new_parent.append(el)
            else:
                new_parent.insert(idx, el)
        self.parent[el] = new_parent
        self.touch(el, location=True)

    def is_descendant(self, el, ancestor) -> bool:
        node = el
        while node is not None:
            if node is ancestor:
                return True
            node = self.parent.get(node)
        return False

    def delete_permanently(self, el):
        """Remove a group or entry and record it in DeletedObjects (so merges in other apps drop it too)."""
        parent = self.parent.get(el)
        if parent is None:
            return
        doomed = [el] + [x for x in el.iter() if x.tag in ("Group", "Entry") and x is not el]
        parent.remove(el)
        deleted = self.root.find("Root/DeletedObjects")
        if deleted is None:
            deleted = _sub(self.root.find("Root"), "DeletedObjects")
        for x in doomed:
            d = _sub(deleted, "DeletedObject")
            _sub(d, "UUID", x.findtext("UUID"))
            _sub(d, "DeletionTime", fmt_time(now_utc()))
        self._index()

    # ---------- entries ----------
    def add_entry(self, group, fields: dict, protected: set | None = None, uuid: str | None = None):
        e = ET.Element("Entry")
        _sub(e, "UUID", uuid or new_uuid())
        _sub(e, "IconID", "0")
        _sub(e, "ForegroundColor", "")
        _sub(e, "BackgroundColor", "")
        _sub(e, "OverrideURL", "")
        _sub(e, "Tags", "")
        _times(e)
        for k in STANDARD_FIELDS:
            fields.setdefault(k, "")
        prot = set(protected or ()) | {"Password"}
        for k, v in fields.items():
            self._set_string(e, k, v, k in prot)
        at = _sub(e, "AutoType")
        _sub(at, "Enabled", "True")
        _sub(at, "DataTransferObfuscation", "0")
        _sub(e, "History")
        self.move(e, group)
        self.entries[e.findtext("UUID")] = e
        return e

    @staticmethod
    def _strings(e):
        return [s for s in e if s.tag == "String"]

    def _set_string(self, e, key, value, protected=False):
        for s in self._strings(e):
            if s.findtext("Key") == key:
                v = s.find("Value")
                v.text = value
                if protected:
                    v.set("Protected", "True")
                elif "Protected" in v.attrib and key not in ("Password",):
                    del v.attrib["Protected"]
                return
        s = ET.Element("String")
        _sub(s, "Key", key)
        v = _sub(s, "Value", value)
        if protected:
            v.set("Protected", "True")
        # keep Strings before AutoType/History
        idx = next((i for i, c in enumerate(list(e)) if c.tag in ("Binary", "AutoType", "History")), None)
        if idx is None:
            e.append(s)
        else:
            e.insert(idx, s)

    def get_fields(self, e) -> dict:
        return {s.findtext("Key"): (s.findtext("Value") or "") for s in self._strings(e)}

    def protected_keys(self, e) -> set:
        return {s.findtext("Key") for s in self._strings(e)
                if (s.find("Value") is not None and s.find("Value").get("Protected", "").lower() == "true")}

    def get_field(self, e, key: str) -> str | None:
        for s in self._strings(e):
            if s.findtext("Key") == key:
                return s.findtext("Value") or ""
        return None

    def remove_field(self, e, key: str):
        for s in self._strings(e):
            if s.findtext("Key") == key:
                e.remove(s)

    def snapshot(self, e):
        """Copy the entry (without its history) into its History, trimming to HistoryMaxItems."""
        hist = e.find("History")
        if hist is None:
            hist = _sub(e, "History")
        copy = ET.fromstring(ET.tostring(e))
        h = copy.find("History")
        if h is not None:
            copy.remove(h)
        hist.append(copy)
        maxn = self.history_max_items()
        if maxn >= 0:
            while len(list(hist)) > maxn:
                hist.remove(list(hist)[0])

    def update_entry(self, e, fields: dict, protected: set | None = None, remove: set | None = None):
        self.snapshot(e)
        prot = set(protected or ())
        existing_prot = self.protected_keys(e)
        for k, v in fields.items():
            self._set_string(e, k, v, k in prot or k in existing_prot)
        for k in remove or ():
            if k not in STANDARD_FIELDS:
                self.remove_field(e, k)
        self.touch(e)

    def tags(self, e) -> list:
        raw = e.findtext("Tags") or ""
        return [t.strip() for t in raw.replace(",", ";").split(";") if t.strip()]

    def set_tags(self, e, tags: list):
        el = e.find("Tags")
        if el is None:
            el = ET.Element("Tags")
            e.insert(1, el)
        el.text = ";".join(dict.fromkeys(t.strip() for t in tags if t.strip()))

    def custom_data(self, el) -> dict:
        cd = el.find("CustomData")
        if cd is None:
            return {}
        return {i.findtext("Key"): i.findtext("Value") or "" for i in cd if i.tag == "Item"}

    def set_custom_data(self, el, key: str, value: str | None):
        cd = el.find("CustomData")
        if cd is None:
            if value is None:
                return
            cd = ET.Element("CustomData")
            idx = next((i for i, c in enumerate(list(el)) if c.tag in ("String", "Binary", "AutoType", "History",
                                                                        "Group", "Entry")), None)
            if idx is None:
                el.append(cd)
            else:
                el.insert(idx, cd)
        for item in list(cd):
            if item.findtext("Key") == key:
                if value is None:
                    cd.remove(item)
                else:
                    item.find("Value").text = value
                return
        if value is not None:
            item = _sub(cd, "Item")
            _sub(item, "Key", key)
            _sub(item, "Value", value)

    def modified(self, el) -> str:
        return el.findtext("Times/LastModificationTime") or ""

    def history(self, e) -> list:
        h = e.find("History")
        return list(h) if h is not None else []

    def copy_entry_into(self, other: "Database", e, group, new_id: bool = True):
        """Copy an entry (with history and attachments) into another database."""
        clone = ET.fromstring(ET.tostring(e))
        if new_id:
            clone.find("UUID").text = new_uuid()
        # attachments: re-point Ref indexes into the other pool
        for b in clone.iter("Binary"):
            v = b.find("Value")
            if v is not None and v.get("Ref") is not None:
                try:
                    data = self.binaries[int(v.get("Ref"))]
                except (ValueError, IndexError):
                    continue
                other.binaries.append(data)
                v.set("Ref", str(len(other.binaries) - 1))
        other.move(clone, group)
        other.reindex()
        return clone

    # ---------- serialisation ----------
    def to_xml(self, stream) -> bytes:
        """The XML with protected values encrypted by `stream`, in document order."""
        saved = []
        for el in self.root.iter():
            if el.tag == "Value" and el.get("Protected", "").lower() == "true":
                plain = (el.text or "").encode("utf-8")
                saved.append((el, el.text))
                el.text = base64.b64encode(stream.update(plain)).decode()
        try:
            body = ET.tostring(self.root, encoding="utf-8", xml_declaration=False)
        finally:
            for el, text in saved:
                el.text = text
        return b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n' + body

    def clone(self) -> "Database":
        c = Database(ET.fromstring(ET.tostring(self.root)))
        c.binaries = list(self.binaries)
        c.kdf = dict(self.kdf) if self.kdf else None
        c.cipher = self.cipher
        c.source_version = self.source_version
        return c
