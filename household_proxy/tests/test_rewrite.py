"""Rewriting (app/rewrite.py, spec/SPEC.md §3.2): URLs, HTML attributes, CSS, headers, cookies, the site's CSP."""
import _env  # noqa: F401  (must be first)

import unittest

from app import rewrite

ING = "/api/hassio_ingress/abc"
P = f"{ING}/s/nas"


def ctx(**kw):
    base = dict(ingress=ING, slug="nas", scheme="http", host="192.168.1.10", port=5000)
    base.update(kw)
    return rewrite.Ctx(**base)


class Urls(unittest.TestCase):
    def test_rewritten(self):
        c = ctx()
        for given, want in [("/x", f"{P}/x"), ("/", f"{P}/"), ("/a?b=1#c", f"{P}/a?b=1#c"),
                            ("http://192.168.1.10:5000/x", f"{P}/x"), ("http://192.168.1.10:5000", f"{P}/"),
                            ("//192.168.1.10:5000/x", f"{P}/x"), ("HTTP://192.168.1.10:5000/Y", f"{P}/Y"),
                            ("  /x", f"  {P}/x")]:
            self.assertEqual(rewrite.url(given, c), want, given)

    def test_left_alone(self):
        c = ctx()
        for given in ("x", "./x", "../x", "#top", "?q=1", "https://example.com/x", "//example.com/x",
                      "http://192.168.1.10:5001/x", "http://192.168.1.11:5000/x", "data:image/png;base64,AA",
                      "javascript:void(0)", "mailto:a@example.com", "blob:x", f"{ING}/s/nas/x", ING, "/\\evil.com",
                      ""):
            self.assertEqual(rewrite.url(given, c), given, given)

    def test_default_ports(self):
        c = ctx(scheme="https", port=443, host="nas.lan")
        self.assertEqual(rewrite.url("https://nas.lan/x", c), f"{P}/x")
        self.assertEqual(rewrite.url("https://NAS.lan./x", c), f"{P}/x")
        self.assertEqual(rewrite.url("http://nas.lan/x", c), "http://nas.lan/x")      # port 80 ≠ 443

    def test_without_ingress(self):
        self.assertEqual(rewrite.url("/x", ctx(ingress="")), "/s/nas/x")

    def test_ingress_path_shape(self):
        self.assertEqual(rewrite.ingress_path("/api/hassio_ingress/abc_-1/"), "/api/hassio_ingress/abc_-1")
        for bad in (None, "", "/evil", "/api/hassio_ingress/a/b", "/api/hassio_ingress/a\"><script>"):
            self.assertEqual(rewrite.ingress_path(bad), "")


class Html(unittest.TestCase):
    def test_attributes_and_text_kept(self):
        src = ('<!DOCTYPE html><html><head><!-- c --><script>if (a < b && c) x("/no")</script></head>'
               '<body class=x data-a=\'1\'><a href="/a" title="q&quot;">t&amp;t &#169;</a>'
               '<img src=/i.png alt="x > y"><br/><input disabled></body></html>')
        out = rewrite.html_text(src, ctx())
        self.assertIn('<!DOCTYPE html>', out)
        self.assertIn('<!-- c -->', out)
        self.assertIn('if (a < b && c) x("/no")', out)              # scripts aren't rewritten
        self.assertIn("<body class=x data-a='1'>", out)              # untouched tags stay byte for byte
        self.assertIn(f'<a href="{P}/a" title="q&quot;">t&amp;t &#169;</a>', out)
        self.assertIn(f'<img src="{P}/i.png" alt="x &gt; y">', out)
        self.assertIn("<br/>", out)
        self.assertIn("<input disabled>", out)

    def test_attribute_values_escaped(self):
        out = rewrite.html_text('<a href="/x&quot;onmouseover=&quot;alert(1)">a</a>', ctx())
        self.assertIn(f'href="{P}/x&quot;onmouseover=&quot;alert(1)"', out)
        self.assertNotIn('" onmouseover', out)

    def test_shim_first_in_head(self):
        c = ctx(shim='<script src="S"></script>')
        out = rewrite.html_text('<html><head><meta charset="utf-8"><script src="/a.js"></script></head></html>', c)
        self.assertTrue(out.startswith('<html><head><script src="S"></script><meta charset="utf-8">'))
        out = rewrite.html_text('<p>no head</p>', c)
        self.assertTrue(out.startswith('<script src="S"></script><p>'))
        self.assertEqual(rewrite.html_text("", c), '<script src="S"></script>')

    def test_meta_refresh_and_srcset(self):
        out = rewrite.html_text('<meta http-equiv="Refresh" content="0; URL=\'/go\'"><img srcset="/a.png 1x,/b.png 2x">',
                                ctx())
        self.assertIn(f"content=\"0; URL='{P}/go'\"", out.replace("&#x27;", "'"))
        self.assertIn(f'srcset="{P}/a.png 1x, {P}/b.png 2x"', out)

    def test_encodings_round_trip(self):
        body = '<html><head><meta charset="iso-8859-1"></head><body><a href="/é">é</a></body></html>'.encode("latin-1")
        out = rewrite.body("html", body, "text/html", ctx())
        self.assertIn(f'href="{P}/é"'.encode("latin-1"), out)
        broken = b'<a href="/x">\xff\xfe</a>'
        self.assertIn(b"\xff\xfe", rewrite.body("html", broken, "text/html; charset=utf-8", ctx()))


class Css(unittest.TestCase):
    def test_urls(self):
        c = ctx()
        self.assertEqual(rewrite.css('a{b:url(/x.png)} c{d:url("/y.png")} e{f:url( \'/z.png\' )}', c),
                         f'a{{b:url({P}/x.png)}} c{{d:url("{P}/y.png")}} e{{f:url(\'{P}/z.png\')}}')
        self.assertEqual(rewrite.css('@import "/a.css"; @import url(/b.css);', c),
                         f'@import "{P}/a.css"; @import url({P}/b.css);')
        self.assertEqual(rewrite.css("a{b:url(data:image/png;base64,AAA)}", c), "a{b:url(data:image/png;base64,AAA)}")


class Headers(unittest.TestCase):
    def test_set_cookie(self):
        c = ctx()
        self.assertEqual(rewrite.set_cookie("a=1; Path=/app; Domain=nas.lan; HttpOnly", c),
                         f"hp_nas_a=1; Path={P}/app; HttpOnly")
        self.assertEqual(rewrite.set_cookie("a=1", c), f"hp_nas_a=1; Path={P}/")
        c.secure = False
        self.assertEqual(rewrite.set_cookie("a=1; Secure; SameSite=None", c), f"hp_nas_a=1; Path={P}/; SameSite=Lax")

    def test_csp(self):
        c = ctx()
        policy, nonce = rewrite.csp("default-src 'self'; frame-ancestors 'none'", c, True)
        self.assertEqual((policy, nonce), ("default-src 'self'", None))
        policy, _ = rewrite.csp("script-src 'none'; object-src 'none'", c, True)
        self.assertEqual(policy, "script-src 'self'; object-src 'none'")
        policy, nonce = rewrite.csp("script-src 'nonce-abc123' 'strict-dynamic'", c, True)
        self.assertEqual(nonce, "abc123")
        self.assertEqual(rewrite.csp("frame-ancestors 'none'", c, False), (None, None))
        c.secure = False
        self.assertEqual(rewrite.csp("upgrade-insecure-requests; img-src *", c, False)[0], "img-src *")

    def test_link_and_refresh(self):
        c = ctx()
        self.assertEqual(rewrite.link_header("</a.js>; rel=preload, <https://cdn.example.com/b>; rel=x", c),
                         f"<{P}/a.js>; rel=preload, <https://cdn.example.com/b>; rel=x")
        self.assertEqual(rewrite.refresh("5;url=/next", c), f"5;url={P}/next")

    def test_policy_with_only_default_src_gets_its_own_script_src(self):
        c = ctx()
        policy, _ = rewrite.csp("default-src 'none'; img-src data:", c, True)
        self.assertEqual(policy, "default-src 'none'; script-src 'self'; img-src data:")     # images etc. untouched
        policy, _ = rewrite.csp("default-src https://cdn.example.com", c, True)
        self.assertEqual(policy, "default-src https://cdn.example.com; script-src https://cdn.example.com 'self'")
        policy, nonce = rewrite.csp("default-src 'nonce-n1' 'strict-dynamic'", c, True)
        self.assertEqual((policy, nonce), ("default-src 'nonce-n1' 'strict-dynamic'", "n1"))
        self.assertEqual(rewrite.csp("default-src 'self'", c, True)[0], "default-src 'self'")

    def test_meta_policy_in_the_page(self):
        c = ctx(secure=False, shim="<script src=x></script>")
        page = ('<html><head><meta http-equiv="Content-Security-Policy" content="script-src \'none\'; '
                'frame-ancestors \'none\'; upgrade-insecure-requests"><title>t</title></head></html>')
        out = rewrite.html_text(page, c)
        self.assertIn('content="script-src &#x27;none&#x27;"', out)        # the shim has run already
        self.assertNotIn("frame-ancestors", out)
        self.assertNotIn("upgrade-insecure", out)
        c.strip_csp = True
        self.assertNotIn("Content-Security-Policy", rewrite.html_text(page, c))
        only = '<head><meta http-equiv="content-security-policy" content="frame-ancestors \'none\'"></head>'
        self.assertEqual(rewrite.html_text(only, ctx(strip_csp=False)), "<head></head>")

    def test_site_cookies_only(self):
        self.assertEqual(rewrite.site_cookies("hp_nas_a=1; b=2; hp_nas2_c=3; hp_nas_d=x=y; ingress_session=s", "nas"),
                         "a=1; d=x=y")
        self.assertIsNone(rewrite.site_cookies("ingress_session=s", "nas"))
        self.assertIsNone(rewrite.site_cookies(None, "nas"))


class Decoding(unittest.TestCase):
    def test_several_gzip_members_read_to_the_end(self):
        import gzip
        from app import decoding
        data = gzip.compress(b"<html>one ") + gzip.compress(b"two ") + gzip.compress(b"three</html>") + b"\0" * 4
        for split in (1, 2, 20, 21, 22, len(data) - 1):
            d = decoding.Decoder("gzip")
            out = b"".join([*d.feed(data[:split]), *d.feed(data[split:]), *d.flush()])
            self.assertEqual(out, b"<html>one two three</html>", split)

    def test_kinds(self):
        self.assertEqual(rewrite.kind_of("text/html; charset=utf-8"), "html")
        self.assertEqual(rewrite.kind_of("TEXT/CSS"), "css")
        self.assertIsNone(rewrite.kind_of("application/javascript"))
        self.assertIsNone(rewrite.kind_of(None))


if __name__ == "__main__":
    unittest.main()
