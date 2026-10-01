"""Search (SPEC §6): message text (FTS5) and file names, only in chats you're in, only what you can see."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import chats, db
from ..auth import require_user

router = APIRouter(prefix="/api", tags=["search"])
HL_START, HL_END = "\u0002", "\u0003"


def fts_query(q: str) -> str | None:
    words = re.findall(r"[\w@#'’-]+", q, flags=re.UNICODE)
    words = [w.replace('"', "") for w in words if w.strip("'’-")][:8]
    if not words:
        return None
    return " ".join(f'"{w}"*' for w in words)


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=200), conversation: str | None = Query(default=None, max_length=64),
           user: dict = Depends(require_user)):
    fq = fts_query(q)
    if fq is None:
        return {"messages": [], "files": []}
    with db.get_conn() as conn:
        where, args = "", []
        if conversation:
            chats.access(conn, conversation, user)
            where, args = " AND m.conversation_id = ?", [conversation]
        try:
            rows = conn.execute(
                "SELECT m.id, m.conversation_id, m.user_id, m.created_at, m.kind, "
                f"snippet(messages_fts, 0, '{HL_START}', '{HL_END}', '…', 16) AS snip "
                "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
                "JOIN members mb ON mb.conversation_id = m.conversation_id AND mb.user_id = ? "
                "WHERE messages_fts MATCH ? AND m.deleted_at IS NULL AND m.kind != 'system' AND m.id > mb.joined_message_id"
                + where + " ORDER BY m.id DESC LIMIT 100", [user["id"], fq, *args]).fetchall()
        except Exception:
            raise HTTPException(422, "Try different words.")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        frows = conn.execute(
            "SELECT a.*, m.conversation_id AS cid, m.user_id AS sender, m.created_at AS sent_at FROM attachments a "
            "JOIN messages m ON m.id = a.message_id JOIN members mb ON mb.conversation_id = m.conversation_id AND mb.user_id = ? "
            "WHERE a.original_name LIKE ? ESCAPE '\\' AND m.deleted_at IS NULL AND m.id > mb.joined_message_id"
            + where + " ORDER BY m.id DESC LIMIT 50", [user["id"], like, *args]).fetchall()
        names, people = {}, {}

        def cname(cid):
            if cid not in names:
                names[cid] = chats.display_name_of(conn, chats.conv_row(conn, cid), user["id"])
            return names[cid]

        def pname(uid):
            if uid not in people:
                people[uid] = chats.shown_name(chats.user_row(conn, uid)) if uid else ""
            return people[uid]
        msgs = [{"id": r["id"], "conversationId": r["conversation_id"], "conversationName": cname(r["conversation_id"]),
                 "author": pname(r["user_id"]), "createdAt": r["created_at"], "kind": r["kind"],
                 "snippet": chats.strip_marks(r["snip"] or "")} for r in rows]
        fl = []
        for a in frows:
            d = chats.attachment_out(a)
            d.update({"messageId": a["message_id"], "conversationId": a["cid"], "conversationName": cname(a["cid"]),
                      "sender": pname(a["sender"]), "sentAt": a["sent_at"]})
            fl.append(d)
    return {"messages": msgs, "files": fl}
