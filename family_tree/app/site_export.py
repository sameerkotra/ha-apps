"""The website export (§13.6.2): a zip that opens offline from index.html.

Built from `export_view.View` only, so it contains exactly what the Export
options allow. Standard library only: HTML is assembled from strings with
every value passed through `html.escape`. The tree page reuses the app's own
chart (static/tree.js) with the data in assets/data.js (a script, because
browsers block fetch() from file://).
"""
import html
import json
import os
import re
from urllib.parse import quote
import unicodedata
import zipfile
from datetime import datetime

from . import media
from .graph import generation_rows
from .export_view import ExportOptions, View

HERE = os.path.dirname(__file__)
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "application/pdf": "pdf"}
# 'self' plus file: so the pages work both from a web host and double-clicked from disk
CSP = ("default-src 'none'; script-src 'self' file:; style-src 'self' file:; img-src 'self' file: data:; "
       "base-uri 'none'; form-action 'none'")


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return (s or "person")[:40]


def _theme_css() -> str:
    """Theme variables and the chart rules, copied from the app's stylesheet."""
    with open(os.path.join(HERE, "static", "style.css"), encoding="utf-8") as f:
        css = f.read()
    themes = css[: css.index(":root { --radius")]
    parchment = re.search(r'\[data-theme="parchment"\] \{(.*?)\n\}', css, re.S).group(1)
    auto = ('html[data-theme="auto"] {}\n@media (prefers-color-scheme: light) {\n  html[data-theme="auto"] {'
            + parchment + "\n  }\n}\n")
    tree_rules = "\n".join(line for line in css.splitlines() if line.startswith(".tree-svg"))
    return themes + auto + tree_rules + "\n"


def _generations(v: View) -> tuple[dict, dict]:
    """Row per person for the whole-tree chart (parents one row up, partners level)."""
    nbr = {pid: [] for pid in v.people}
    for f in v.families:
        ps = [x for x in (f["p1"], f["p2"]) if x]
        if len(ps) == 2:
            nbr[ps[0]].append((ps[1], 0))
            nbr[ps[1]].append((ps[0], 0))
        for c in f["children"]:
            for p in ps:
                nbr[p].append((c["id"], 1))
                nbr[c["id"]].append((p, -1))
    return generation_rows(sorted(v.people, key=lambda p: -len(nbr[p])), nbr.__getitem__)


class Site:
    def __init__(self, v: View, options: ExportOptions, progress=None):
        self.v, self.o = v, options
        self.progress = progress or (lambda pct, msg: None)
        self.pages = {}                       # person id → relative page path
        for pid, p in v.people.items():
            if not p["placeholder"]:
                self.pages[pid] = f"people/{slugify(p['name'])}-{pid[:8]}.html"
        self.files = {mid: f"{mid}.{EXT.get(m['contentType'], 'bin')}" for mid, m in v.media.items()}
        site = options.site
        self.home = (site.homePersonId if site.homePersonId in self.pages
                     else v.home if v.home in self.pages
                     else next(iter(sorted(self.pages, key=lambda x: v.people[x]["name"])), None))
        self.title = (site.title or "Our family").strip()
        self.pages_on = site.pages
        self.show_places = bool(site.pages.places and options.places and (
            any(ev.get("place") for p in v.people.values() for ev in p.get("events") or [])
            or any(ev.get("place") for f in v.families for ev in f["events"])))
        self.stamp = datetime.now().strftime("%d %B %Y")

    # ---------- page chrome ----------
    def page(self, title, body, depth=0, active="", wide=False, scripts=()):
        b = "../" * depth
        nav = [("index.html", "Home", "home")]
        if self.pages_on.tree:
            nav.append(("tree.html", "Tree", "tree"))
        if self.pages_on.people:
            nav.append(("people.html", "People", "people"))
        if self.pages_on.surnames:
            nav.append(("surnames.html", "Surnames", "surnames"))
        if self.show_places:
            nav.append(("places.html", "Places", "places"))
        on = ' class="active"'
        links = "".join(f'<a href="{b}{h}"{on if k == active else ""}>{e(t)}</a>' for h, t, k in nav)
        main_cls = ' class="wide"' if wide else ""
        themes = "".join(f'<option value="{k}">{e(n)}</option>' for k, n in (
            ("auto", "Auto"), ("heritage", "Heritage"), ("slate", "Slate"), ("daylight", "Daylight"), ("parchment", "Parchment")))
        extra = "".join(f'<script src="{b}assets/{s}"></script>' for s in scripts)
        return (f'<!DOCTYPE html>\n<html lang="en" data-theme="{e(self.o.site.theme)}">\n<head>\n<meta charset="utf-8">\n'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                f'<meta http-equiv="Content-Security-Policy" content="{CSP}">\n'
                f'<meta name="referrer" content="no-referrer">\n'
                f'<title>{e(title)} — {e(self.title)}</title>\n<link rel="stylesheet" href="{b}assets/site.css">\n'
                f'{extra}<script src="{b}assets/site.js"></script>\n</head>\n<body data-base="{b}">\n'
                f'<header class="site-head"><a class="brand" href="{b}index.html">{e(self.title)}</a><nav>{links}</nav>'
                f'<label class="dim">Theme <select id="themeSel" aria-label="Theme">{themes}</select></label></header>\n'
                f'<main{main_cls}>\n{body}\n</main>\n'
                f'<footer>Exported from Family Tree on {e(self.stamp)}. Please keep it within the family.</footer>\n'
                f'</body>\n</html>\n')

    def avatar(self, p, depth, big=False):
        cls = f'av {"big " if big else ""}{e(p.get("gender") or "unknown")}'
        if p.get("photo") and p["photo"] in self.files:
            return f'<span class="{cls}"><img src="{"../" * depth}media/{e(self.files[p["photo"]])}" alt=""></span>'
        ini = ((p.get("given") or p["name"] or "?")[:1] + (p.get("surname") or "")[:1]).upper()
        return f'<span class="{cls}">{e(ini)}</span>'

    def rel(self, p):
        if not p.get("relationship") or not self.v.relative_to_name:
            return ""
        if p["relationship"] == "you":
            return ""
        return f"{self.v.relative_to_name}'s {p['relationship']}"

    def card(self, pid, depth, extra=""):
        p = self.v.people.get(pid)
        if not p:
            return ""
        if p["placeholder"]:
            return f'<span class="pc private">{self.avatar(p, depth)}<span><span class="nm">Private</span></span></span>'
        sub = " · ".join(x for x in (p["years"], extra, self.rel(p)) if x)
        return (f'<a class="pc" href="{"../" * depth}{e(self.pages[pid])}">{self.avatar(p, depth)}'
                f'<span><span class="nm">{e(p["name"])}</span>{f"<br><span class=sub>{e(sub)}</span>" if sub else ""}</span></a>')

    # ---------- family lookups ----------
    def relatives(self, pid):
        parents, partners, children, siblings = [], [], [], []
        for f in self.v.families:
            ps = [x for x in (f["p1"], f["p2"]) if x]
            kids = [c["id"] for c in f["children"]]
            if pid in kids:
                parents += [(x, next((c["relation"] for c in f["children"] if c["id"] == pid), "birth")) for x in ps]
                siblings += [k for k in kids if k != pid]
            if pid in ps:
                other = next((x for x in ps if x != pid), None)
                partners.append((other, f))
                children += [(c["id"], c["relation"]) for c in f["children"]]
        seen, sib = set(), []
        for s in siblings:
            if s not in seen:
                seen.add(s)
                sib.append(s)
        return parents, partners, children, sib

    # ---------- pages ----------
    def person_page(self, pid):
        p = self.v.people[pid]
        d = 1
        head = [f'<div class="person-head">{self.avatar(p, d, big=True)}<div><h1>{e(p["name"])}</h1>']
        aka = [f"née {p['birthSurname']}" if p.get("birthSurname") else "", f"“{p['nickname']}”" if p.get("nickname") else ""]
        aka += p.get("otherNames") or []
        aka = [a for a in aka if a]
        if aka:
            head.append(f'<div class="dim">{e(" · ".join(aka))}</div>')
        lines = []
        if p.get("birth") and (p["birth"]["date"] or p["birth"]["place"]):
            when = p["birth"]["date"] and p["birth"]["date"] + (f" at {p['birth']['time']}" if p["birth"].get("time") else "")
            lines.append("Born " + " in ".join(x for x in (when, p["birth"]["place"]) if x))
        if p.get("death") and (p["death"]["date"] or p["death"]["place"]):
            when = p["death"]["date"] and p["death"]["date"] + (f" at {p['death']['time']}" if p["death"].get("time") else "")
            lines.append("Died " + " in ".join(x for x in (when, p["death"]["place"]) if x))
        if lines:
            head.append(f'<div>{e(" · ".join(lines))}</div>')
        if self.rel(p):
            head.append(f'<div class="rel">{e(self.rel(p))}</div>')
        head.append("</div></div>")
        parts = ["".join(head)]
        if p.get("custom"):
            parts.append("<div class='card kv'>" + "".join(
                f"<div><span class='dim'>{e(c['label'])}</span> {e(c['value'])}</div>" for c in p["custom"]) + "</div>")
        if p.get("contacts"):
            kinds = {"phone": "📞", "whatsapp": "💬", "email": "✉️", "address": "🏠"}
            parts.append("<h2>Contact</h2><div class='card kv'>" + "".join(
                f"<div>{kinds.get(c['kind'], '•')} {e(c['label'] + ': ') if c.get('label') else ''}{e(c['value'])}</div>"
                for c in p["contacts"]) + "</div>")
        if p["limited"] and self.o.deaths:
            parts.append('<p class="hint">Details of living people aren\'t included in this export.</p>')
        parents, partners, children, siblings = self.relatives(pid)
        fam = []
        if parents:
            fam.append("<h2>Parents</h2><div class='people-grid'>" + "".join(
                self.card(x, d, "" if rel == "birth" else rel) for x, rel in parents) + "</div>")
        for other, f in partners:
            label = {"partners": "Partner", "married": "Spouse"}.get(f["kind"], "Spouse / partner")
            married = next((ev for ev in f["events"] if ev["type"] == "marriage"), None)
            info = ""
            if married and (married["date"] or married["place"]):
                info = "Married " + " in ".join(x for x in (married["date"], married["place"]) if x)
            kids = "".join(self.card(c["id"], d, "" if c["relation"] == "birth" else c["relation"]) for c in f["children"])
            fam.append(f"<h2>{label}</h2>"
                       + (f"<div class='people-grid'>{self.card(other, d)}</div>" if other else "")
                       + (f"<p class='dim'>{e(info)}</p>" if info else "")
                       + (f"<h2>Children</h2><div class='people-grid'>{kids}</div>" if kids else ""))
        if siblings:
            fam.append("<h2>Brothers & sisters</h2><div class='people-grid'>" + "".join(self.card(s, d) for s in siblings) + "</div>")
        parts += fam
        if p["events"]:
            rows = "".join(
                f'<div class="ev"><div class="when">{e(ev["date"] or "")}</div><div><b>{e(ev["label"])}</b>'
                f'{(" — " + e(ev["title"])) if ev["title"] else ""}'
                f'{("<div class=dim>" + e(ev["place"]) + "</div>") if ev["place"] else ""}'
                f'{("<div class=dim>" + e(ev["description"]) + "</div>") if ev.get("description") else ""}</div></div>'
                for ev in p["events"])
            parts.append(f"<h2>Life</h2><div class='card timeline'>{rows}</div>")
        if p.get("biography"):
            parts.append(f"<h2>Biography</h2><div class='card bio'>{e(p['biography'])}</div>")
        for s in p.get("stories") or []:
            parts.append(f"<div class='card story'><h3>{e(s['title'])}</h3><div class='body'>{e(s['body'])}</div></div>")
        if p.get("sources"):
            parts.append("<h2>Sources</h2><ol class='card sources'>" + "".join(
                f"<li>{e(x['title'])}{(', ' + e(x['page'])) if x.get('page') else ''}"
                f"{(' <span class=dim>— ' + e(x['fact']) + '</span>') if x.get('fact') else ''}</li>" for x in p["sources"]) + "</ol>")
        mids = p.get("media") or []
        photos = [m for m in mids if self.v.media[m]["kind"] == "photo"]
        docs = [m for m in mids if self.v.media[m]["kind"] == "document"]
        if photos:
            items = "".join(
                f'<figure><a href="../media/{e(self.files[m])}"><img src="../media/{e(self.files[m])}" alt="{e(self.v.media[m]["title"] or "Photo")}"></a>'
                f'<figcaption>{e(" · ".join(x for x in (self.v.media[m]["title"], self.v.media[m]["date"]) if x))}</figcaption></figure>'
                for m in photos)
            parts.append(f"<h2>Photos</h2><div class='gallery'>{items}</div>")
        if docs:
            items = "".join(f'<a href="../media/{e(self.files[m])}" download>📄 {e(self.v.media[m]["title"] or "Document")}</a>' for m in docs)
            parts.append(f"<h2>Documents</h2><div class='card docs'>{items}</div>")
        return self.page(p["name"], "\n".join(parts), depth=1, active="people")

    def index_page(self):
        v = self.v
        real = [p for p in v.people.values() if not p["placeholder"]]
        surnames = {p["surname"] for p in real if p.get("surname")}
        years = [p["birth"]["year"] for p in real if p.get("birth") and p["birth"].get("year")]
        gens = (max(self.gen.values()) + 1) if self.gen else 0
        cover = self.o.site.coverMediaId if self.o.site.coverMediaId in self.files else (
            v.people[self.home]["photo"] if self.home and v.people[self.home].get("photo") in self.files else None)
        intro = "".join(f"<p>{e(par.strip())}</p>" for par in re.split(r"\n\s*\n", self.o.site.intro or "") if par.strip())
        hero = (f'<div class="hero">{f"<img class=cover src=media/{e(self.files[cover])} alt=>" if cover else ""}'
                f'<div class="text"><h1>{e(self.title)}</h1><div class="intro">{intro}</div>')
        if self.home and self.pages_on.tree:
            hero += '<p><a class="btn" href="tree.html">Open the tree</a></p>'
        hero += "</div></div>"
        home = ""
        if self.home:
            home = f"<h2>Start here</h2><div class='people-grid'>{self.card(self.home, 0)}</div>"
        stats = (f"<div class='stats'><div class='stat'><b>{len(real)}</b><span>People</span></div>"
                 f"<div class='stat'><b>{gens}</b><span>Generations</span></div>"
                 f"<div class='stat'><b>{len(surnames)}</b><span>Surnames</span></div>"
                 + (f"<div class='stat'><b>{min(years)}</b><span>Earliest birth</span></div>" if years else "") + "</div>")
        search = ("<h2>Find someone</h2><input id='homeSearch' class='search' type='search' placeholder='Type a name…' "
                  "aria-label='Search'><div id='homeResults' class='results'></div>")
        return self.page("Home", hero + stats + home + search, active="home", scripts=("data.js",))

    def people_page(self):
        rows = []
        real = sorted((p for p in self.v.people.values() if not p["placeholder"]),
                      key=lambda p: ((p.get("surname") or "~").lower(), (p.get("given") or "").lower()))
        letter = None
        for p in real:
            L = ((p.get("surname") or p["name"] or "?")[:1]).upper()
            if L != letter:
                letter = L
                rows.append(f'<div class="letter-head" id="L-{e(L)}">{e(L)}</div>')
            alt = " ".join(x for x in (p.get("birthSurname"), p.get("nickname"), *(p.get("otherNames") or [])) if x)
            sub = " · ".join(x for x in (p["years"], self.rel(p)) if x)
            rows.append(f'<a class="row" href="{e(self.pages[p["id"]])}" data-search="{e(p["name"] + " " + alt)}">'
                        f'{self.avatar(p, 0)}<span class="grow"><span class="nm">{e(p["name"])}</span>'
                        f'{f"<br><span class=sub>{e(sub)}</span>" if sub else ""}</span></a>')
        body = (f"<h1>People</h1><p class='dim'>{len(real)} people</p>"
                "<input id='listSearch' class='search' type='search' placeholder='Filter by name…' aria-label='Filter'>"
                f"<div class='plist'>{''.join(rows)}</div>")
        return self.page("People", body, active="people")

    def surnames_page(self):
        counts = {}
        for p in self.v.people.values():
            if not p["placeholder"] and p.get("surname"):
                counts[p["surname"]] = counts.get(p["surname"], 0) + 1
        link = self.pages_on.people
        items = "".join((f'<a class="row" href="people.html?s={e(quote(s))}">' if link else '<div class="row">')
                        + f'<span class="grow"><span class="nm">{e(s)}</span>'
                        f'<br><span class="sub">{n} {"person" if n == 1 else "people"}</span></span>'
                        + ('</a>' if link else '</div>')
                        for s, n in sorted(counts.items(), key=lambda x: x[0].lower()))
        return self.page("Surnames", f"<h1>Surnames</h1><div class='plist'>{items}</div>", active="surnames")

    def places_page(self):
        places = {}
        for pid, p in self.v.people.items():
            for ev in p.get("events") or []:
                if ev.get("place"):
                    places.setdefault(ev["place"], []).append((ev, pid))
        for f in self.v.families:
            for ev in f["events"]:
                if ev.get("place"):
                    places.setdefault(ev["place"], []).append((ev, f["p1"] or f["p2"]))
        blocks = []
        for place in sorted(places, key=str.lower):
            li = "".join(
                f"<li>{e(ev['label'])}: " + (f'<a href="{e(self.pages[pid])}">{e(self.v.people[pid]["name"])}</a>'
                                            if pid in self.pages else "Private")
                + (f" ({e(ev['date'])})" if ev.get("date") else "") + "</li>"
                for ev, pid in sorted(places[place], key=lambda x: x[0].get("sortKey") or ""))
            blocks.append(f"<div class='card place'><h3>{e(place)}</h3><ul>{li}</ul></div>")
        body = "<h1>Places</h1>" + ("".join(blocks) or "<p class='dim'>No places recorded.</p>")
        return self.page("Places", body, active="places")

    def tree_page(self):
        body = ("<div class='tree-page'><div class='tree-bar'><input id='treeFind' type='search' placeholder='Find someone…' "
                "aria-label='Find someone' list='names'><datalist id='names'>"
                + "".join(f"<option value=\"{e(p['name'])}\">" for p in self.v.people.values() if not p["placeholder"])
                + "</datalist><button id='zIn' type='button'>+</button><button id='zOut' type='button'>−</button>"
                  "<button id='zFit' type='button'>Fit</button><span class='dim'>Tap a card to open · drag and pinch to move</span></div>"
                  "<div id='treeCanvas' class='tree-canvas'></div></div>")
        return self.page("Tree", body, active="tree", wide=True, scripts=("data.js", "tree.js"))

    def data_js(self):
        people = []
        for pid, p in self.v.people.items():
            alt = " ".join(x for x in (p.get("birthSurname"), p.get("nickname"), *(p.get("otherNames") or [])) if x)
            people.append({
                "id": pid, "name": p["name"], "given": p.get("given"), "surname": p.get("surname"),
                "gender": p.get("gender") or "unknown", "living": not p.get("died"),
                "years": p.get("years") or "", "photo": self.files.get(p.get("photo")) if p.get("photo") else None,
                "gen": self.gen.get(pid, 0), "comp": self.comp.get(pid, 0), "relationship": p.get("relationship"),
                "page": self.pages.get(pid), "alt": alt,
            })
        fams = [{"id": f["id"], "p1": f["p1"], "p2": f["p2"], "kind": f["kind"], "ended": f["ended"],
                 "children": f["children"]} for f in self.v.families]
        data = {"people": people, "families": fams, "focus": self.home, "relativeTo": self.v.relative_to_name or ""}
        # a .js file, not JSON, so it loads from file:// ; "</" is escaped for good measure
        return "window.TREE = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";\n"

    # ---------- the zip ----------
    def write(self, zpath: str) -> None:
        self.gen, self.comp = _generations(self.v)
        total = len(self.pages) + len(self.files) + 8
        done = 0

        def tick(msg):
            nonlocal done
            done += 1
            if done % 25 == 0 or done == total:
                self.progress(min(99, int(done * 100 / total)), msg)

        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            with open(os.path.join(HERE, "site_assets", "site.css"), encoding="utf-8") as f:
                z.writestr("assets/site.css", _theme_css() + f.read())
            z.write(os.path.join(HERE, "site_assets", "site.js"), "assets/site.js")
            z.write(os.path.join(HERE, "static", "tree.js"), "assets/tree.js")
            z.writestr("assets/data.js", self.data_js())
            z.writestr("index.html", self.index_page())
            if self.pages_on.tree:
                z.writestr("tree.html", self.tree_page())
            if self.pages_on.people:
                z.writestr("people.html", self.people_page())
            if self.pages_on.surnames:
                z.writestr("surnames.html", self.surnames_page())
            if self.show_places:
                z.writestr("places.html", self.places_page())
            for pid in self.pages:
                z.writestr(self.pages[pid], self.person_page(pid))
                tick("Writing pages")
            for mid, name in self.files.items():
                m = self.v.media[mid]
                src = m.get("path") or media.file_path(mid, m["sizeKey"])
                if os.path.isfile(src):
                    z.write(src, f"media/{name}", compress_type=zipfile.ZIP_STORED)
                tick("Copying photos")
            z.writestr("README.txt", f"{self.title}\n\nOpen index.html in any web browser. Everything works offline.\n"
                                     f"Exported from Family Tree on {self.stamp}. Anyone with these files can read "
                                     "everything in them — share with care.\n")
