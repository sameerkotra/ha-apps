"""Stories: longer memories written about a person (plain text)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db
from ..auth import require_user
from ..common import Strict, clean, require_person
from ..history import Batch
from .people import _nm

router = APIRouter(prefix="/api", tags=["stories"])

MAX_STORIES_PER_PERSON = 100


class StoryIn(Strict):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50000)


class StoryPatch(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=50000)


def story_out(conn, r) -> dict:
    who = conn.execute("SELECT name FROM users WHERE id = ?", (r["created_by"],)).fetchone() if r["created_by"] else None
    return {"id": r["id"], "personId": r["person_id"], "title": r["title"], "body": r["body"],
            "createdBy": who["name"] if who else None, "createdAt": r["created_at"], "updatedAt": r["updated_at"]}


@router.get("/people/{pid}/stories")
def list_stories(pid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        require_person(conn, pid)
        rows = conn.execute("SELECT * FROM stories WHERE person_id = ? ORDER BY created_at, rowid", (pid,)).fetchall()
        return [story_out(conn, r) for r in rows]


@router.post("/people/{pid}/stories", status_code=201)
def add_story(pid: str, body: StoryIn, user: dict = Depends(require_user)):
    title, text = clean(body.title), body.body.strip()
    if not title or not text:
        raise HTTPException(422, "A story needs a title and some text.")
    with db.get_conn() as conn:
        person = require_person(conn, pid)
        n = conn.execute("SELECT COUNT(*) FROM stories WHERE person_id = ?", (pid,)).fetchone()[0]
        if n >= MAX_STORIES_PER_PERSON:
            raise HTTPException(422, f"A person can have at most {MAX_STORIES_PER_PERSON} stories.")
        b = Batch(conn, user["id"], f"Added a story about {_nm(person)}")
        sid = db.new_id()
        row = b.insert("stories", {"id": sid, "person_id": pid, "title": title, "body": text,
                                   "created_by": user["id"], "created_at": b.now, "updated_at": b.now})
        b.touch(pid)
        return {"story": story_out(conn, row), "batchId": b.id}


@router.patch("/stories/{sid}")
def edit_story(sid: str, body: StoryPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "That story doesn't exist.")
        person = require_person(conn, row["person_id"])
        fields = {}
        sent = body.model_fields_set
        if "title" in sent and body.title is not None:
            fields["title"] = clean(body.title)
        if "body" in sent and body.body is not None:
            fields["body"] = body.body.strip()
        if any(not v for v in fields.values()):
            raise HTTPException(422, "A story needs a title and some text.")
        b = Batch(conn, user["id"], f"Edited a story about {_nm(person)}")
        after = b.update("stories", sid, fields)
        b.touch(row["person_id"])
        return {"story": story_out(conn, after), "batchId": b.id}


@router.delete("/stories/{sid}")
def delete_story(sid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "That story doesn't exist.")
        person = conn.execute("SELECT * FROM people WHERE id = ?", (row["person_id"],)).fetchone()
        b = Batch(conn, user["id"], f"Deleted a story about {_nm(person)}")
        b.hard_delete("stories", sid)
        b.touch(row["person_id"])
        return {"ok": True, "batchId": b.id}
