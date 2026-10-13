"""The shared wildcard matcher (common/python/wildcard.py): fnmatch's answers, without its exponential case."""
import fnmatch
import time
import unittest

from app.common import wildcard


class WildcardTest(unittest.TestCase):
    def test_like_fnmatch(self):
        cases = [("*.pdf", "a.pdf"), ("*.pdf", "a.pdfx"), ("*.pdf", ".pdf"), ("a?c", "abc"), ("a?c", "ac"),
                 ("a*c", "ac"), ("a*c", "abbbc"), ("a*c", "abbbcd"), ("[abc]x", "bx"), ("[abc]x", "dx"),
                 ("[!abc]x", "dx"), ("[!abc]x", "ax"), ("[a-c]x", "bx"), ("[a-c]x", "dx"), ("[]a]x", "]x"),
                 ("[]a]x", "ax"), ("[a-]x", "-x"), ("[-a]x", "-x"), ("a[", "a["), ("a[b", "a[b"), ("", ""), ("", "a"),
                 ("*", ""), ("*", "anything"), ("**", "x"), ("?", ""), ("?", "x"), ("*a*b*c*", "xaxbxcx"),
                 ("*a*b*c*", "xaxcxbx"), ("a*?", "a"), ("a*?", "ab"), ("[!]", "x"), ("x[", "x["), ("re-202?-[0-9][0-9].csv", "re-2026-07.csv")]
        for pattern, name in cases:
            with self.subTest(pattern=pattern, name=name):
                self.assertEqual(wildcard.match(pattern, name, ignore_case=False), fnmatch.fnmatchcase(name, pattern))

    def test_case(self):
        self.assertTrue(wildcard.match("*.PDF", "invoice.pdf"))
        self.assertTrue(wildcard.match("[a-c]*", "Beta"))
        self.assertFalse(wildcard.match("[a-c]*", "Beta", ignore_case=False))
        self.assertTrue(wildcard.match("stra*e", "STRASSE"))
        self.assertEqual(wildcard.filter(["a.txt", "B.TXT", "c.md"], "*.txt"), ["a.txt", "B.TXT"])
        self.assertTrue(wildcard.is_pattern("a*"))
        self.assertFalse(wildcard.is_pattern("plain.txt"))

    def test_adversarial_pattern_is_fast(self):
        """fnmatch's regular expression backtracks exponentially on these; each takes well under 10 ms here
        (the best of three runs, so a busy test machine doesn't make it flaky)."""
        cases = [("*a" * 40 + "*b", "a" * 2000, False), ("*a" * 40 + "*b", "a" * 2000 + "b", True),
                 ("*a*a*a*a*a*a*a*a*a*a*a*a*b", "a" * 5000, False), ("*?*?*?*?*?*?*?*?*?*?*?*?x", "y" * 3000, False)]
        for pattern, name, want in cases:
            with self.subTest(pattern=pattern[:20], n=len(name)):
                best = float("inf")
                for _ in range(3):
                    t0 = time.perf_counter()
                    self.assertEqual(wildcard.match(pattern, name), want)
                    best = min(best, time.perf_counter() - t0)
                self.assertLess(best, 0.010)

    def test_limits(self):
        with self.assertRaises(ValueError):
            wildcard.match("*" * 1001, "x")
        with self.assertRaises(ValueError):
            wildcard.match("*", "x" * 10_001)
        tokens = wildcard.compile("*.txt")
        self.assertTrue(wildcard.match_compiled(tokens, "A.TXT"))


if __name__ == "__main__":
    unittest.main()
