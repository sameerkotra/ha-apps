"""Argon2 (RFC 9106) in pure Python — a fallback only.

The app derives keys with `argon2-cffi` when it's installed (the Docker
image installs it) or with `cryptography`'s Argon2id. This module is used
when neither can do the job: Argon2d files imported from KeePassXC on a
machine without argon2-cffi, and the tests, which check it against
`cryptography`'s Argon2id. It's slow (seconds per MiB), so it's never used
for the app's own files.
"""
import hashlib
import struct

MASK = (1 << 64) - 1
TYPE_D, TYPE_I, TYPE_ID = 0, 1, 2
VERSION = 0x13


def _le32(n: int) -> bytes:
    return struct.pack("<I", n)


def _blake2b(data: bytes, size: int = 64) -> bytes:
    return hashlib.blake2b(data, digest_size=size).digest()


def _hprime(tag_len: int, data: bytes) -> bytes:
    """H' — variable-length hash (RFC 9106 §3.3)."""
    if tag_len <= 64:
        return _blake2b(_le32(tag_len) + data, tag_len)
    r = (tag_len + 31) // 32 - 2
    v = _blake2b(_le32(tag_len) + data)
    out = [v[:32]]
    for _ in range(1, r):
        v = _blake2b(v)
        out.append(v[:32])
    out.append(_blake2b(v, tag_len - 32 * r))
    return b"".join(out)


def _gb(v, a, b, c, d):
    va, vb, vc, vd = v[a], v[b], v[c], v[d]
    va = (va + vb + 2 * (va & 0xFFFFFFFF) * (vb & 0xFFFFFFFF)) & MASK
    x = vd ^ va
    vd = ((x >> 32) | (x << 32)) & MASK
    vc = (vc + vd + 2 * (vc & 0xFFFFFFFF) * (vd & 0xFFFFFFFF)) & MASK
    x = vb ^ vc
    vb = ((x >> 24) | (x << 40)) & MASK
    va = (va + vb + 2 * (va & 0xFFFFFFFF) * (vb & 0xFFFFFFFF)) & MASK
    x = vd ^ va
    vd = ((x >> 16) | (x << 48)) & MASK
    vc = (vc + vd + 2 * (vc & 0xFFFFFFFF) * (vd & 0xFFFFFFFF)) & MASK
    x = vb ^ vc
    vb = ((x >> 63) | (x << 1)) & MASK
    v[a], v[b], v[c], v[d] = va, vb, vc, vd


def _p(v):
    _gb(v, 0, 4, 8, 12); _gb(v, 1, 5, 9, 13); _gb(v, 2, 6, 10, 14); _gb(v, 3, 7, 11, 15)
    _gb(v, 0, 5, 10, 15); _gb(v, 1, 6, 11, 12); _gb(v, 2, 7, 8, 13); _gb(v, 3, 4, 9, 14)


_ROWS = [list(range(16 * i, 16 * i + 16)) for i in range(8)]
_COLS = [[2 * i + 16 * j + k for j in range(8) for k in (0, 1)] for i in range(8)]


def _fill(prev, ref, old=None):
    """The compression function G, optionally XORed with the block it replaces (passes ≥ 2)."""
    r = [a ^ b for a, b in zip(prev, ref)]
    tmp = r[:] if old is None else [a ^ b for a, b in zip(r, old)]
    for idx in _ROWS:
        v = [r[i] for i in idx]
        _p(v)
        for i, x in zip(idx, v):
            r[i] = x
    for idx in _COLS:
        v = [r[i] for i in idx]
        _p(v)
        for i, x in zip(idx, v):
            r[i] = x
    return [a ^ b for a, b in zip(tmp, r)]


def _to_block(b: bytes):
    return list(struct.unpack("<128Q", b))


def _from_block(w) -> bytes:
    return struct.pack("<128Q", *w)


def argon2(password: bytes, salt: bytes, time_cost: int, memory_kib: int, parallelism: int, tag_len: int = 32,
           type_: int = TYPE_ID, secret: bytes = b"", ad: bytes = b"", version: int = VERSION) -> bytes:
    if version != VERSION:
        raise ValueError("Only Argon2 version 1.3 (0x13) is supported.")
    p = parallelism
    h0 = _blake2b(b"".join([_le32(p), _le32(tag_len), _le32(memory_kib), _le32(time_cost), _le32(version),
                             _le32(type_), _le32(len(password)), password, _le32(len(salt)), salt,
                             _le32(len(secret)), secret, _le32(len(ad)), ad]))
    m = max(memory_kib, 8 * p)
    m = 4 * p * (m // (4 * p))
    q = m // p
    sl = q // 4
    mem = [[None] * q for _ in range(p)]
    for lane in range(p):
        mem[lane][0] = _to_block(_hprime(1024, h0 + _le32(0) + _le32(lane)))
        mem[lane][1] = _to_block(_hprime(1024, h0 + _le32(1) + _le32(lane)))
    zero = [0] * 128
    for r in range(time_cost):
        for s in range(4):
            for lane in range(p):
                independent = type_ == TYPE_I or (type_ == TYPE_ID and r == 0 and s < 2)
                start = 2 if (r == 0 and s == 0) else 0
                addr = None
                inp = None
                if independent:
                    inp = [0] * 128
                    inp[0], inp[1], inp[2], inp[3], inp[4], inp[5] = r, lane, s, m, time_cost, type_
                    if start == 2:
                        inp[6] += 1
                        addr = _fill(zero, _fill(zero, inp))
                for i in range(start, sl):
                    col = s * sl + i
                    prev_col = col - 1 if col > 0 else q - 1
                    if independent:
                        if i % 128 == 0:
                            inp[6] += 1
                            addr = _fill(zero, _fill(zero, inp))
                        rnd = addr[i % 128]
                    else:
                        rnd = mem[lane][prev_col][0]
                    ref_lane = (rnd >> 32) % p
                    if r == 0 and s == 0:
                        ref_lane = lane
                    same = ref_lane == lane
                    if r == 0:
                        if s == 0:
                            area = i - 1
                        else:
                            area = s * sl + i - 1 if same else s * sl + (-1 if i == 0 else 0)
                    else:
                        area = q - sl + i - 1 if same else q - sl + (-1 if i == 0 else 0)
                    j1 = rnd & 0xFFFFFFFF
                    rel = (j1 * j1) >> 32
                    rel = area - 1 - ((area * rel) >> 32)
                    begin = 0 if r == 0 else (0 if s == 3 else (s + 1) * sl)
                    ref_col = (begin + rel) % q
                    old = mem[lane][col] if r > 0 else None
                    mem[lane][col] = _fill(mem[lane][prev_col], mem[ref_lane][ref_col], old)
    final = mem[0][q - 1][:]
    for lane in range(1, p):
        final = [a ^ b for a, b in zip(final, mem[lane][q - 1])]
    return _hprime(tag_len, _from_block(final))
