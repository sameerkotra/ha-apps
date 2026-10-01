"""QR code encoder (byte mode, UTF-8) — a port of the encoder in static/qr.js,
so the server can draw the guest Wi-Fi code for the Home Assistant dashboard
without a browser. Same tables and choices, so both draw identical codes
(tests compare them). Written for this app; follows ISO/IEC 18004."""

ECC_PER_BLOCK = [
    [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
]
NUM_BLOCKS = [
    [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
]
ECL_INDEX = {"L": 0, "M": 1, "Q": 2, "H": 3}
ECL_FORMAT = [1, 0, 3, 2]

EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def _rs_divisor(degree: int) -> list:
    r = [0] * degree
    r[-1] = 1
    root = 1
    for _ in range(degree):
        for j in range(degree):
            r[j] = _mul(r[j], root)
            if j + 1 < degree:
                r[j] ^= r[j + 1]
        root = _mul(root, 2)
    return r


def _rs_remainder(data: list, divisor: list) -> list:
    r = [0] * len(divisor)
    for b in data:
        f = b ^ r.pop(0)
        r.append(0)
        for i, d in enumerate(divisor):
            r[i] ^= _mul(d, f)
    return r


def _size(ver: int) -> int:
    return ver * 4 + 17


def _alignment(ver: int) -> list:
    if ver == 1:
        return []
    num = ver // 7 + 2
    step = 26 if ver == 32 else -(-(ver * 4 + 4) // (num * 2 - 2)) * 2
    r = [6]
    pos = _size(ver) - 7
    while len(r) < num:
        r.insert(1, pos)
        pos -= step
    return r


def _raw_modules(ver: int) -> int:
    r = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        r -= (25 * n - 10) * n - 55
        if ver >= 7:
            r -= 36
    return r


def _data_codewords(ver: int, e: int) -> int:
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[e][ver] * NUM_BLOCKS[e][ver]


def _format_bits(e: int, mask: int) -> int:
    data = (ECL_FORMAT[e] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _version_bits(ver: int) -> int:
    rem = ver
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (ver << 12) | rem


def _bit(x: int, i: int) -> bool:
    return (x >> i) & 1 != 0


MASKS = [
    lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
]


def _function_grid(ver: int):
    size = _size(ver)
    mods = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def put(x, y, v):
        mods[y][x] = v
        fn[y][x] = True
    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)

    def finder(cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                d = max(abs(dx), abs(dy))
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    put(x, y, d not in (2, 4))
    finder(3, 3)
    finder(size - 4, 3)
    finder(3, size - 4)
    al = _alignment(ver)
    for i in range(len(al)):
        for j in range(len(al)):
            if (i == 0 and j == 0) or (i == 0 and j == len(al) - 1) or (i == len(al) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(al[i] + dx, al[j] + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(9):
        fn[8][i] = fn[i][8] = True
    for i in range(8):
        fn[8][size - 1 - i] = True
        fn[size - 1 - i][8] = True
    put(8, size - 8, True)
    if ver >= 7:
        vb = _version_bits(ver)
        for i in range(18):
            a, b, v = size - 11 + (i % 3), i // 3, _bit(vb, i)
            put(a, b, v)
            put(b, a, v)
    return size, mods, fn


def _draw_format(mods, size: int, bits: int) -> None:
    for i in range(6):
        mods[i][8] = _bit(bits, i)
    mods[7][8] = _bit(bits, 6)
    mods[8][8] = _bit(bits, 7)
    mods[8][7] = _bit(bits, 8)
    for i in range(9, 15):
        mods[8][14 - i] = _bit(bits, i)
    for i in range(8):
        mods[8][size - 1 - i] = _bit(bits, i)
    for i in range(8, 15):
        mods[size - 15 + i][8] = _bit(bits, i)
    mods[size - 8][8] = True


def _data_positions(fn, size: int) -> list:
    out = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fn[y][x]:
                    out.append((x, y))
        right -= 2
    return out


def _penalty(m, size: int) -> int:
    p = 0
    for pas in range(2):
        for a in range(size):
            run = 1
            for b in range(1, size):
                cur = m[b][a] if pas else m[a][b]
                prev = m[b - 1][a] if pas else m[a][b - 1]
                if cur == prev:
                    run += 1
                    if run == 5:
                        p += 3
                    elif run > 5:
                        p += 1
                else:
                    run = 1
    for y in range(size - 1):
        for x in range(size - 1):
            c = m[y][x]
            if c == m[y][x + 1] and c == m[y + 1][x] and c == m[y + 1][x + 1]:
                p += 3
    pat = [True, False, True, True, True, False, True]
    for pas in range(2):
        for a in range(size):
            for b in range(size - 6):
                if not all((m[b + k][a] if pas else m[a][b + k]) == pat[k] for k in range(7)):
                    continue

                def light(i):
                    return i < 0 or i >= size or not (m[i][a] if pas else m[a][i])
                if all(light(b - k) for k in (1, 2, 3, 4)) or all(light(b + 6 + k) for k in (1, 2, 3, 4)):
                    p += 40
    dark = sum(1 for row in m for v in row if v)
    total = size * size
    p += _ceil_div_minus1(dark, total) * 10
    return p


def _ceil_div_minus1(dark: int, total: int) -> int:
    """Math.ceil(|dark*20 - total*10| / total - 1), exactly as the JavaScript computes it."""
    import math
    return math.ceil(abs(dark * 20 - total * 10) / total - 1)


def encode(text: str, ecl: str = "M") -> dict:
    """{size, version, modules: [[bool]]} (modules[y][x])."""
    e = ECL_INDEX[ecl]
    data_bytes = list(text.encode("utf-8"))
    ver = 0
    for v in range(1, 41):
        cc = 8 if v <= 9 else 16
        if 4 + cc + len(data_bytes) * 8 <= _data_codewords(v, e) * 8:
            ver = v
            break
    if not ver:
        raise ValueError("Too much text for a QR code.")
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)
    put(4, 4)
    put(len(data_bytes), 8 if ver <= 9 else 16)
    for b in data_bytes:
        put(b, 8)
    cap = _data_codewords(ver, e) * 8
    put(0, min(4, cap - len(bits)))
    put(0, (8 - len(bits) % 8) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < cap // 8:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    nb, ecc = NUM_BLOCKS[e][ver], ECC_PER_BLOCK[e][ver]
    raw = _raw_modules(ver) // 8
    num_short, short_len = nb - raw % nb, raw // nb
    divisor = _rs_divisor(ecc)
    blocks = []
    k = 0
    for i in range(nb):
        ln = short_len - ecc + (0 if i < num_short else 1)
        dat = data[k:k + ln]
        k += ln
        blocks.append((dat, _rs_remainder(dat, divisor)))
    out = []
    for i in range(short_len - ecc + 1):
        for dat, _ in blocks:
            if i < len(dat):
                out.append(dat[i])
    for i in range(ecc):
        for _, ec in blocks:
            out.append(ec[i])
    size, mods, fn = _function_grid(ver)
    pos = _data_positions(fn, size)
    for i, (x, y) in enumerate(pos):
        mods[y][x] = _bit(out[i >> 3], 7 - (i & 7)) if i < len(out) * 8 else False
    best, best_p = None, None
    for mask in range(8):
        m = [row[:] for row in mods]
        f = MASKS[mask]
        for x, y in pos:
            if f(x, y):
                m[y][x] = not m[y][x]
        _draw_format(m, size, _format_bits(e, mask))
        p = _penalty(m, size)
        if best_p is None or p < best_p:
            best_p, best = p, m
    return {"size": size, "version": ver, "modules": best}


def svg(text: str, ecl: str = "M", border: int = 4) -> str:
    """A small, self-contained SVG of the code (black on white, crisp edges)."""
    q = encode(text, ecl)
    n = q["size"] + 2 * border
    path = "".join(f"M{x + border} {y + border}h1v1h-1z" for y, row in enumerate(q["modules"]) for x, on in enumerate(row) if on)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def wifi_text(ssid: str, password: str, security: str) -> str:
    """The WIFI: payload phones understand (special characters escaped)."""
    def esc(t):
        return "".join("\\" + c if c in '\\;,:"' else c for c in (t or ""))
    if security == "nopass":
        return f"WIFI:T:nopass;S:{esc(ssid)};;"
    return f"WIFI:T:{security};S:{esc(ssid)};P:{esc(password)};;"
