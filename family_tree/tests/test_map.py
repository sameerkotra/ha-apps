"""Places map (§13.3): its switch and settings, CSP, geocoding (against a fake
Nominatim), points, pins, and the vendored Leaflet. Places are invented."""
from base import ADMIN, ApiTestCase, addon_version, set_app_settings, sql
import _env  # noqa: F401

import hashlib
import json
import os
import re
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from app import geocode, settings


class FakeNominatim:
    """Answers /search?q=… from a dict; records every query and User-Agent."""

    def __init__(self, known):
        self.known = known
        self.queries = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(u.query).get("q", [""])[0]
                outer.queries.append((u.path, q, self.headers.get("User-Agent")))
                hit = outer.known.get(q)
                body = json.dumps([{"lat": str(hit[0]), "lon": str(hit[1])}] if hit else []).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class MapCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        set_app_settings(feature_map=False)          # as on a new install: the map uses the internet
        p = mock.patch.object(geocode, "MIN_INTERVAL", 0)
        p.start()
        self.addCleanup(p.stop)
        self.nom = FakeNominatim({"Guntur, Andhra Pradesh, India": (16.3, 80.45), "Hyderabad, India": (17.38, 78.48),
                                  "Springfield, USA": (39.8, -89.64), "Chennai, India": (13.08, 80.27)})
        self.addCleanup(self.nom.stop)

    def enable(self):
        set_app_settings(feature_map=True, nominatim_url=self.nom.url)

    def event(self, pid, etype, place, y=None):
        body = {"personId": pid, "type": etype, "place": place}
        if y:
            body["date"] = {"y": y}
        return self.ok(self.post("/api/events", body), 201)

    def run_geocoder(self):
        n = 0
        while geocode.step_blocking():
            n += 1
            self.assertLess(n, 50)
        return n


class Settings(MapCase):
    def test_off_by_default_and_validation(self):
        v = self.ok(self.get("/api/admin/settings", user=ADMIN))["values"]
        self.assertEqual((v["feature_map"], v["map_tiles_url"], v["nominatim_url"]),
                         (False, "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "https://nominatim.openstreetmap.org"))
        for body in ({"feature_map": "yes"}, {"enable_map": True}, {"map_tiles_url": "https://tiles.example/{z}/{x}.png"},
                     {"map_tiles_url": "javascript:alert(1)//{z}{x}{y}"}, {"map_tiles_url": "https://a b/{z}/{x}/{y}"},
                     {"nominatim_url": "ftp://example.org"}, {"nominatim_url": "https://x.org/'; img-src *"}):
            self.assertEqual(self.put("/api/admin/settings", body, user=ADMIN).status_code, 422, body)
        r = self.ok(self.put("/api/admin/settings", {"feature_map": True, "nominatim_url": "https://geo.example.org/"}, user=ADMIN))
        self.assertEqual(r["values"]["nominatim_url"], "https://geo.example.org")
        self.assertTrue(self.ok(self.get("/api/me"))["mapEnabled"])
        self.addCleanup(set_app_settings, feature_map=False, nominatim_url=settings.NOMINATIM_DEFAULT)

    def test_csp_follows_the_setting(self):
        csp = self.get("/api/health").headers["content-security-policy"]
        self.assertNotIn("tile.openstreetmap.org", csp)
        self.enable()
        csp = self.get("/api/health").headers["content-security-policy"]
        self.assertIn("img-src 'self' data: blob: https://tile.openstreetmap.org;", csp)
        self.assertIn("script-src 'self';", csp)                 # nothing else widens
        set_app_settings(map_tiles_url="https://{s}.tiles.example.org:8443/{z}/{x}/{y}.png")
        self.assertIn("https://*.tiles.example.org:8443", self.get("/api/health").headers["content-security-policy"])

    def test_endpoints_need_the_map_on(self):
        r = self.get("/api/map/status")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["featureOff"], "map")
        self.assertIn("Places map is turned off", r.json()["detail"])
        self.assertEqual(self.get("/api/map/points").status_code, 404)
        self.assertEqual(self.put("/api/map/places", {"place": "X", "lat": 1, "lon": 2}).status_code, 404)
        self.assertFalse(self.ok(self.get("/api/me"))["mapEnabled"])
        self.assertFalse(geocode.step_blocking())                # and nothing is looked up
        self.assertEqual(self.nom.queries, [])


class Geocoding(MapCase):
    def test_keys_and_variants(self):
        self.assertEqual(geocode.key("  Guntur ,  Andhra Pradesh, INDIA "), "guntur, andhra pradesh, india")
        self.assertEqual(geocode.variants("Flat 4B, 12 Main Road, Guntur, Andhra Pradesh, India"),
                         ["Flat 4B, 12 Main Road, Guntur, Andhra Pradesh, India", "12 Main Road, Guntur, Andhra Pradesh, India",
                          "Guntur, Andhra Pradesh, India", "Andhra Pradesh, India"])

    def test_background_lookup(self):
        a = self.person("Ann", "Smith")
        self.event(a, "birth", "12 Main Road, Guntur, Andhra Pradesh, India", 1950)
        self.event(a, "residence", "Hyderabad, India", 1975)
        self.event(a, "residence", "  hyderabad,india ", 1980)        # same place, written differently
        self.event(a, "death", "Atlantis", 2010)
        self.enable()
        self.assertEqual(self.run_geocoder(), 3)
        rows = {r["place_key"]: r for r in sql("SELECT * FROM place_geo")}
        self.assertEqual(rows["hyderabad, india"]["source"], "geocoder")
        self.assertEqual((rows["atlantis"]["source"], rows["atlantis"]["tries"]), ("failed", 1))
        g = rows["12 main road, guntur, andhra pradesh, india"]
        self.assertEqual((g["lat"], g["lon"]), (16.3, 80.45))        # found by dropping the street
        # only place text is sent, with an identifying User-Agent
        self.assertTrue(all(path == "/search" and "Ann" not in q and ua.startswith("FamilyTree-") for path, q, ua in self.nom.queries))
        # nothing more to do until a week passes for the failed one
        self.assertFalse(geocode.step_blocking())
        st = self.ok(self.get("/api/map/status"))
        self.assertEqual((st["places"], st["located"], st["failed"], st["pending"]), (3, 2, 1, 0))
        # admins can retry the failed ones now
        self.assertEqual(self.post("/api/map/retry").status_code, 403)
        self.assertEqual(self.ok(self.post("/api/map/retry", user=ADMIN))["retrying"], 1)
        self.assertTrue(geocode.step_blocking())

    def test_manual_pin_wins(self):
        a = self.person("Ann")
        self.event(a, "residence", "Atlantis", 1990)
        self.enable()
        self.run_geocoder()
        r = self.ok(self.put("/api/map/places", {"place": "atlantis ", "lat": 36.4, "lon": 25.4}))
        self.assertEqual(r["source"], "manual")
        pts = self.ok(self.get("/api/map/points"))["points"]
        self.assertEqual([(p["place"], p["lat"], p["source"]) for p in pts], [("Atlantis", 36.4, "manual")])
        sql("UPDATE place_geo SET checked_at = '2000-01-01T00:00:00Z'")
        self.assertFalse(geocode.step_blocking())                   # never looked up again
        self.ok(self.delete("/api/map/places?place=Atlantis"))
        self.assertTrue(geocode.step_blocking())
        self.assertEqual(self.delete("/api/map/places?place=Nowhere").status_code, 404)
        for bad in ({"place": "X", "lat": 91, "lon": 0}, {"place": "X", "lat": 0, "lon": 181}, {"place": "", "lat": 0, "lon": 0}):
            self.assertEqual(self.put("/api/map/places", bad).status_code, 422, bad)


class Points(MapCase):
    def setUp(self):
        super().setUp()
        self.gpa = self.person("Grandpa", gender="male")
        self.dad = self.person("Dad", gender="male")
        self.mum = self.person("Mum", gender="female")
        self.me = self.person("Me")
        self.family(self.gpa, None, [self.dad])
        self.fid = self.family(self.dad, self.mum, [self.me])
        self.event(self.gpa, "birth", "Guntur, Andhra Pradesh, India", 1930)
        self.event(self.dad, "birth", "Chennai, India", 1960)
        self.event(self.dad, "residence", "Springfield, USA", 1990)
        self.event(self.me, "birth", "Springfield, USA", 1992)
        self.ok(self.post("/api/events", {"familyId": self.fid, "type": "marriage", "place": "Hyderabad, India",
                                          "date": {"y": 1988}}), 201)
        self.event(self.mum, "occupation", "Nowhere Land", 1985)
        self.set_me(self.me)
        self.enable()
        self.run_geocoder()

    def pts(self, **params):
        qs = urllib.parse.urlencode(params)
        return self.ok(self.get(f"/api/map/points?{qs}"))

    def test_everyone(self):
        r = self.pts()
        got = [(p["kind"], p["year"], p["place"], sorted(x["name"] for x in p["people"])) for p in r["points"]]
        self.assertEqual(got, [("birth", 1930, "Guntur, Andhra Pradesh, India", ["Grandpa"]),
                               ("birth", 1960, "Chennai, India", ["Dad"]),
                               ("marriage", 1988, "Hyderabad, India", ["Dad", "Mum"]),
                               ("residence", 1990, "Springfield, USA", ["Dad"]),
                               ("birth", 1992, "Springfield, USA", ["Me"])])
        self.assertEqual([(u["place"], u["events"], u["status"]) for u in r["unlocated"]], [("Nowhere Land", 1, "failed")])

    def test_filters(self):
        anc = self.pts(filter="ancestors")                       # "me" by default
        self.assertEqual(sorted({x["name"] for p in anc["points"] for x in p["people"]}), ["Dad", "Grandpa", "Me", "Mum"])
        one = self.pts(filter="person", personId=self.dad)
        self.assertEqual([p["kind"] for p in one["points"]], ["birth", "marriage", "residence"])
        desc = self.pts(filter="descendants", personId=self.dad)
        self.assertEqual(sorted({x["name"] for p in desc["points"] for x in p["people"]}), ["Dad", "Me", "Mum"])
        self.assertEqual(self.get("/api/map/points?filter=person", user=ADMIN).status_code, 422)
        # trashed people drop off the map
        self.ok(self.delete(f"/api/people/{self.gpa}"))
        self.assertNotIn(1930, [p["year"] for p in self.pts()["points"]])


class Assets(ApiTestCase):
    def test_vendored_leaflet(self):
        root = os.path.join(_env.ROOT, "app", "static", "vendor", "leaflet")
        want = dict(re.findall(r"- (leaflet\.(?:js|css))\s+([0-9a-f]{64})", open(os.path.join(root, "README.md")).read()))
        for name, digest in want.items():
            self.assertEqual(hashlib.sha256(open(os.path.join(root, name), "rb").read()).hexdigest(), digest, name)
        self.assertEqual(len(want), 2)
        self.assertEqual(self.get("/vendor/leaflet/leaflet.js").status_code, 200)

    def test_asset_versions_match_the_addon(self):
        html = open(os.path.join(_env.ROOT, "app", "static", "index.html")).read()
        versions = set(re.findall(r"\?v=([0-9.]+)", html))
        self.assertEqual(versions, {addon_version()})


if __name__ == "__main__":
    unittest.main()
