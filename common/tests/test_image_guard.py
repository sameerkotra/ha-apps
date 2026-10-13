"""The shared image header check (common/python/image_guard.py)."""
import io
import struct
import threading
import unittest
import zlib

from app.common import image_guard

try:
    from PIL import Image
except ImportError:
    Image = None


def png_header(width, height):
    """A PNG whose header declares width × height (no pixel data: nothing can decode it)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


@unittest.skipIf(Image is None, "Pillow isn't installed")
class CheckImageTest(unittest.TestCase):
    def test_header_only(self):
        self.assertEqual(image_guard.check_image(png_header(640, 480)), ("PNG", 640, 480))
        fp = io.BytesIO(png_header(10, 10))
        fp.read(3)
        self.assertEqual(image_guard.check_image(fp, formats=["PNG"]), ("PNG", 10, 10))
        self.assertEqual(fp.tell(), 0)

    def test_too_big_without_decoding(self):
        for w, h in ((100_000, 100_000), (7000, 7000), (30_000, 10)):
            with self.subTest(w=w, h=h), self.assertRaises(image_guard.ImageTooBig):
                image_guard.check_image(png_header(w, h), max_pixels=40_000_000)
        self.assertEqual(image_guard.check_image(png_header(7000, 7000), max_pixels=50_000_000)[1:], (7000, 7000))
        self.assertEqual(Image.MAX_IMAGE_PIXELS, image_guard.MAX_IMAGE_PIXELS)

    def test_not_an_image(self):
        for data in (b"", b"hello", b"%PDF-1.4", png_header(0, 5)):
            with self.subTest(data=data[:8]), self.assertRaises(image_guard.NotAnImage):
                image_guard.check_image(data)
        with self.assertRaises(image_guard.NotAnImage):
            image_guard.check_image(png_header(5, 5), formats=["JPEG"])

    def test_open_for_decode_and_slot(self):
        buf = io.BytesIO()
        Image.new("RGB", (64, 32), "red").save(buf, "JPEG")
        with image_guard.decode_slot():
            img = image_guard.open_for_decode(buf.getvalue(), draft=("RGB", (8, 8)))
            try:
                self.assertEqual(img.format, "JPEG")
                self.assertLessEqual(img.size[0], 16)           # drafted to a smaller scale
                img.load()
            finally:
                img.close()

    def test_slots_are_limited(self):
        held = []
        for _ in range(image_guard.DECODE_SLOTS):
            cm = image_guard.decode_slot()
            cm.__enter__()
            held.append(cm)
        try:
            with self.assertRaises(image_guard.DecodeBusy):
                with image_guard.decode_slot(timeout=0.05):
                    pass
        finally:
            for cm in held:
                cm.__exit__(None, None, None)
        with image_guard.decode_slot(timeout=0.05):
            pass


class WithoutPillowTest(unittest.TestCase):
    def test_import_without_pillow(self):
        self.assertTrue(hasattr(image_guard, "check_image"))
        if image_guard.Image is None:
            with self.assertRaises(image_guard.PillowMissing):
                image_guard.check_image(b"x")


if __name__ == "__main__":
    unittest.main()
