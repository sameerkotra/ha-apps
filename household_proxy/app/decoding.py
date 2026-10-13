"""Reading a compressed answer in bounded pieces (gzip / deflate), so a small compressed body can't expand into
gigabytes in memory at once: each piece is at most PIECE bytes, and the caller decides when to stop. A gzip body made
of several members one after the other (allowed, and what some servers send) is read to its end."""
import zlib

PIECE = 256 * 1024


GZIP_MAGIC = b"\x1f\x8b"


class Decoder:
    def __init__(self, encoding: str | None):
        enc = (encoding or "identity").strip().lower()
        self.kind = enc
        self.gz = enc in ("gzip", "x-gzip")
        self.tail = b""                       # bytes after a gzip member: the next member, or trailing padding
        if enc in ("gzip", "x-gzip"):
            self.d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            self.d = None                     # zlib-wrapped or raw: decided on the first bytes
        elif enc in ("identity", ""):
            self.d = False
        else:
            raise ValueError(f"unsupported content encoding {enc!r}")

    def feed(self, data: bytes):
        """The decoded pieces for one chunk of the raw body."""
        if self.d is False:
            for i in range(0, len(data), PIECE):
                yield data[i:i + PIECE]
            return
        if self.d is None:
            self.d = zlib.decompressobj(zlib.MAX_WBITS if data[:1] and (data[0] & 0x0F) == 8 else -zlib.MAX_WBITS)
        if self.gz and self.d.eof:
            self.tail += data
            data = b""
        while True:
            if data:
                out = self.d.decompress(data, PIECE)
                while True:
                    if out:
                        yield out
                    if not self.d.unconsumed_tail:
                        break
                    out = self.d.decompress(self.d.unconsumed_tail, PIECE)
                data = b""
                if self.gz and self.d.eof:
                    self.tail += self.d.unused_data
            if not (self.gz and self.d.eof and self.tail):
                return
            if self.tail[:2] == GZIP_MAGIC:           # the next member
                data, self.tail = self.tail, b""
                self.d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                continue
            if self.tail != GZIP_MAGIC[:1]:          # padding or junk after the last member: ignored, as before
                self.tail = b""
            return                                    # (a lone 0x1f: wait for the next byte)

    def flush(self):
        if self.d not in (None, False):
            rest = self.d.flush(PIECE)
            if rest:
                yield rest


async def pieces(raw_iter, encoding: str | None):
    """An async iterator of decoded pieces from an async iterator of raw chunks."""
    dec = Decoder(encoding)
    async for chunk in raw_iter:
        for piece in dec.feed(chunk):
            yield piece
    for piece in dec.flush():
        yield piece


def readable(encoding: str | None) -> bool:
    return (encoding or "identity").strip().lower() in ("identity", "", "gzip", "x-gzip", "deflate")
