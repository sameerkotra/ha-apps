"""Download a chat (SPEC §15.9): a zip with one self-contained, readable page and the files.

The page has no scripts and every value is escaped. Disappearing messages are left out, and so are
files beyond the size cap (the page says what was left out)."""
import html
import os
import re
import tempfile
import zipfile

from . import chats, config, db, files, settings

CSS = """body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;margin:24px auto;padding:0 16px;color:#1c2433;background:#f4f6fa}
h1{font-size:1.4rem;margin:0 0 4px}.hint{color:#5e6b80;font-size:.85rem}.day{text-align:center;margin:18px 0 8px;color:#5e6b80;font-size:.85rem}
.msg{background:#fff;border:1px solid #d8dfec;border-radius:10px;padding:8px 12px;margin:6px 0}.sys{text-align:center;color:#5e6b80;font-size:.85rem;margin:6px 0}
.who{font-weight:700;font-size:.9rem}.when{color:#5e6b80;font-size:.8rem;margin-left:6px}.quote{border-left:3px solid #1f8a64;padding-left:8px;color:#5e6b80;font-size:.9rem;margin:4px 0}
.del{color:#5e6b80;font-style:italic}code,pre{font-family:Menlo,Consolas,monospace;background:#eef1f7;border-radius:4px;padding:0 4px}pre{padding:6px 8px;white-space:pre-wrap}
blockquote{border-left:3px solid #aab;margin:4px 0;padding-left:8px;color:#5e6b80}img{max-width:100%;max-height:420px;border-radius:6px;display:block;margin:4px 0}
.poll{border:1px solid #d8dfec;border-radius:8px;padding:6px 10px;margin:4px 0}.pin{color:#a86b00;font-size:.8rem}.ann{border-color:#a86b00;background:#fdf1d8}
.p{white-space:pre-wrap}ul,ol{margin:2px 0;padding-left:1.4em}"""

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"]+", re.I)


def _inline(s: str, depth: int = 0) -> str:
    out = []
    i = 0
    word = re.compile(r"\w", re.U)
    while i < len(s):
        c = s[i]
        prev = s[i - 1] if i else ""
        if c == "`":
            j = s.find("`", i + 1)
            if j > i + 1:
                out.append(f"<code>{html.escape(s[i + 1:j])}</code>")
                i = j + 1
                continue
        m = URL_RE.match(s, i) if not word.match(prev or " ") else None
        if m:
            u = m.group(0).rstrip(".,!?;:'\")]")
            href = u if u.lower().startswith("http") else "https://" + u
            out.append(f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer">{html.escape(u)}</a>')
            i += len(u)
            continue
        if c in "*_~" and depth < 6 and not word.match(prev or " ") and i + 1 < len(s) and not s[i + 1].isspace() and s[i + 1] != c:
            k = i + 2
            found = -1
            while True:
                k = s.find(c, k)
                if k < 0:
                    break
                if not s[k - 1].isspace() and not word.match(s[k + 1] if k + 1 < len(s) else " "):
                    found = k
                    break
                k += 1
            if found > 0:
                tag = {"*": "strong", "_": "em", "~": "s"}[c]
                out.append(f"<{tag}>{_inline(s[i + 1:found], depth + 1)}</{tag}>")
                i = found + 1
                continue
        out.append(html.escape(c))
        i += 1
    return "".join(out)


def render_text(text: str) -> str:
    lines = (text or "").split("\n")
    out, para = [], []

    def flush():
        if para:
            out.append('<div class="p">' + "<br>".join(_inline(x) for x in para) + "</div>")
            para.clear()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if t.startswith("```"):
            flush()
            body, j = [t[3:]], i + 1
            if len(t) > 6 and t.endswith("```"):
                out.append(f"<pre>{html.escape(t[3:-3])}</pre>")
                i += 1
                continue
            while j < len(lines) and not lines[j].rstrip().endswith("```"):
                body.append(lines[j])
                j += 1
            if j < len(lines):
                body.append(lines[j].rstrip()[:-3])
                out.append(f"<pre>{html.escape(chr(10).join(body).strip(chr(10)))}</pre>")
                i = j + 1
                continue
        for pat, tag in ((r"^\s*[-•]\s+(.*)$", "ul"), (r"^\s*\d{1,4}\.\s+(.*)$", "ol")):
            if re.match(pat, lines[i]):
                flush()
                items = []
                while i < len(lines) and re.match(pat, lines[i]):
                    items.append(f"<li>{_inline(re.match(pat, lines[i]).group(1))}</li>")
                    i += 1
                out.append(f"<{tag}>{''.join(items)}</{tag}>")
                break
        else:
            if re.match(r"^>\s?", lines[i]):
                flush()
                q = []
                while i < len(lines) and re.match(r"^>\s?", lines[i]):
                    q.append(_inline(re.sub(r"^>\s?", "", lines[i])))
                    i += 1
                out.append("<blockquote>" + "<br>".join(q) + "</blockquote>")
                continue
            para.append(lines[i])
            i += 1
    flush()
    return "".join(out)


def build(conv_id: str, user: dict, date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """→ (path of a temporary zip under /data, download name)."""
    e = html.escape
    cap = settings.get("export_max_mb") * 1024 * 1024
    with db.get_conn() as conn:
        chats.access(conn, conv_id, user)
        has_files = conn.execute("SELECT 1 FROM attachments WHERE conversation_id = ? AND message_id IS NOT NULL LIMIT 1",
                                 (conv_id,)).fetchone()
    if has_files:
        files.require_online()          # not "no longer available" for every file just because the NAS is off
    with db.get_conn() as conn:
        conv, m = chats.access(conn, conv_id, user)
        title = chats.display_name_of(conn, conv, user["id"])
        where, args = "", []
        from datetime import datetime, timedelta
        if date_from:          # the days are Home Assistant's local days
            where += " AND created_at >= ?"
            args.append(config.iso(datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=config.tz())))
        if date_to:
            where += " AND created_at < ?"
            args.append(config.iso(datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=config.tz()) + timedelta(days=1)))
        rows = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND id > ? AND expires_at IS NULL" + where +
                            " ORDER BY id", (conv_id, m["joined_message_id"], *args)).fetchall()
        msgs = chats.messages_out(conn, rows, user["id"], m["joined_message_id"])
        att_rows = {a["id"]: a for a in conn.execute(
            "SELECT * FROM attachments WHERE conversation_id = ? AND message_id IS NOT NULL", (conv_id,))}
        disappearing_ids = {r["id"] for r in conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND expires_at IS NOT NULL", (conv_id,))}
        skipped_disappearing = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND id > ? "
                                            "AND expires_at IS NOT NULL", (conv_id, m["joined_message_id"])).fetchone()[0]
        db.audit(conn, "exported", user["id"], conv_id, user["id"])
    fd, tmp = tempfile.mkstemp(prefix=".export-", suffix=".zip", dir=config.DATA_DIR)
    os.close(fd)
    used, left_out = 0, []
    names_in_zip: set = set()
    body = []
    last_day = None
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for msg in msgs:
            day = msg["createdAt"][:10]
            if day != last_day:
                body.append(f'<div class="day">{e(day)}</div>')
                last_day = day
            when = e(msg["createdAt"][11:16])
            if msg["kind"] == "system":
                body.append(f'<div class="sys">{e(msg["body"])} · {when}</div>')
                continue
            parts = [f'<div class="who">{e(msg["author"] or "")}<span class="when">{when}</span>'
                     + (' <span class="pin">📌 pinned</span>' if msg["pinned"] else "")
                     + (" <span class=\"hint\">(edited)</span>" if msg["editedAt"] else "") + "</div>"]
            if msg["forwarded"]:
                parts.append('<div class="hint">↪ Forwarded</div>')
            rt = msg["replyTo"]
            if rt and rt.get("id") in disappearing_ids:
                parts.append('<div class="quote">Original message not included</div>')
            elif rt and not rt.get("hidden") and not rt.get("gone"):
                parts.append(f'<div class="quote">{e(rt.get("author") or "")}: {e(rt.get("text") or "")}</div>')
            if msg["deleted"]:
                parts.append('<div class="del">Message deleted</div>')
            elif msg["card"]:
                cd = msg["card"]
                owner = f" ({cd['owner']}'s)" if cd.get("owner") else ""
                parts.append(f'<div class="poll">{chats.CARD_ICONS.get(cd["type"], "📄")} <strong>{e(cd["title"])}</strong>'
                             f' <span class="hint">— shared from {e(cd["badge"])}{e(owner)}</span></div>')
            elif msg["poll"]:
                p = msg["poll"]
                opts = "".join(f"<li>{e(o['text'])} — {len(o['votes'])}</li>" for o in p["options"])
                parts.append(f'<div class="poll"><strong>📊 {e(p["question"])}</strong>'
                             f'{" (closed)" if p["closed"] else ""}<ul>{opts}</ul></div>')
            else:
                if msg["body"]:
                    parts.append(render_text(msg["body"]))
                for a in msg["attachments"]:
                    row = att_rows.get(a["id"])
                    if a["missing"] or a["adminDeleted"] or row is None:
                        parts.append(f'<div class="hint">📎 {e(a["name"])} — no longer available</div>')
                        continue
                    try:
                        src = files.resolve(row["rel_path"])
                        size = os.path.getsize(src)
                    except Exception:
                        parts.append(f'<div class="hint">📎 {e(a["name"])} — no longer available</div>')
                        continue
                    if used + size > cap:
                        left_out.append(a["name"])
                        parts.append(f'<div class="hint">📎 {e(a["name"])} — left out (size limit)</div>')
                        continue
                    arc = files.unique_name_in(names_in_zip, files.clean(a["name"], 120))
                    names_in_zip.add(arc)
                    z.write(src, "files/" + arc)
                    used += size
                    href = "files/" + arc.replace("%", "%25").replace("#", "%23").replace("?", "%3F")
                    if a["image"]:
                        parts.append(f'<a href="{e(href, quote=True)}"><img src="{e(href, quote=True)}" alt="{e(a["name"], quote=True)}"></a>')
                    else:
                        parts.append(f'<div>📎 <a href="{e(href, quote=True)}">{e(a["name"])}</a> <span class="hint">{a["size"]} bytes</span></div>')
            cls = "msg ann" if msg["announcement"] else "msg"
            body.append(f'<div class="{cls}">{"".join(parts)}</div>')
        notes = []
        if skipped_disappearing:
            notes.append(f"{skipped_disappearing} disappearing message(s) are not included.")
        if left_out:
            notes.append(f"{len(left_out)} file(s) were left out to stay under {settings.get('export_max_mb')} MB.")
        page = ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
                f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'\">"
                f"<title>{e(title)}</title><style>{CSS}</style></head><body>"
                f"<h1>{e(title)}</h1><div class=\"hint\">Downloaded from Household Chat on {e(config.local_now().strftime('%Y-%m-%d %H:%M'))}"
                + (f" · {e(date_from or '…')} to {e(date_to or '…')}" if date_from or date_to else "") + "</div>"
                + "".join(f'<p class="hint">{e(n)}</p>' for n in notes) + "".join(body) + "</body></html>")
        page_name = files.clean(title, 80) + ".html"
        z.writestr(page_name, page)
    return tmp, files.clean(f"{title} chat.zip", 100)
