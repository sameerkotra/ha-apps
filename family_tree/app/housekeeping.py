"""Background upkeep: the daily trash purge, the media online check every
5 minutes, and the hourly sync of Home Assistant persons into users."""
import logging
import os
import sqlite3
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db, ha_client, media, settings
from .common import backup_core
from .common import housekeeping as jobs_core

logger = logging.getLogger("housekeeping")


def trash_days() -> int:
    """Read at the moment of use: a change in App settings applies at the next purge."""
    return settings.get("trash_days")


def purge_expired() -> dict:
    """The daily purge: everything deleted more than trash_days ago (and old history, if an admin asked)."""
    out = purge(trash_days())
    prune_history()
    return out


def prune_history(keep_days: int | None = None) -> int:
    """History older than the *Keep history for* App setting, removed (0, the default: keep everything).
    Its changes, people and "This is me" moves go with each batch (ON DELETE CASCADE)."""
    keep_days = settings.get("history_keep_days") if keep_days is None else keep_days
    if not keep_days:
        return 0
    cutoff = (config.utcnow() - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM batches WHERE created_at < ?", (cutoff,)).rowcount
    if n:
        logger.info("Removed %s history entries older than %s days", n, keep_days)
    return n


def _purge_one(conn, what: str, key: str, work) -> bool:
    """Run one item's deletes inside a savepoint: if the database refuses them (a reference this
    version doesn't know about), that item is put back as it was, logged, and the purge carries on
    with the rest instead of failing as a whole."""
    conn.execute("SAVEPOINT purge_item")
    try:
        work()
    except sqlite3.DatabaseError as e:
        conn.execute("ROLLBACK TO purge_item")
        conn.execute("RELEASE purge_item")
        logger.warning("Couldn't purge %s %s from the trash (%s); it stays there for now.", what, key, e)
        return False
    conn.execute("RELEASE purge_item")
    return True


def purge(older_than_days: int | None) -> dict:
    """Permanently remove trashed people, families, sources and media — all of them
    (admin 'Empty trash') or those deleted more than N days ago. Not undoable;
    history keeps its snapshots.

    Every reference to a purged row that has no ON DELETE rule of its own is cleared first
    (a source told by the person, a source's scan, people merged into the person), and each
    item is purged in its own savepoint, so one row the database refuses can't keep the
    whole trash from emptying."""
    cutoff = None
    if older_than_days is not None:
        cutoff = (config.utcnow() - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cond = "deleted_at IS NOT NULL" + (" AND deleted_at < ?" if cutoff else "")
    args = [cutoff] if cutoff else []
    counts = {"people": 0, "families": 0, "sources": 0, "media": 0}
    skipped = 0
    removed_media = []
    with db.get_conn() as conn:
        conn.execute("BEGIN")                      # one transaction; each item is a savepoint inside it
        people = [r["id"] for r in conn.execute(f"SELECT id FROM people WHERE {cond}", args)]
        doomed = set(people)

        def purge_person(pid):
            fams = 0
            for f in conn.execute("SELECT * FROM families WHERE partner1_id = ? OR partner2_id = ?", (pid, pid)).fetchall():
                other = f["partner2_id"] if f["partner1_id"] == pid else f["partner1_id"]
                if other and other not in doomed:
                    col = "partner1_id" if f["partner1_id"] == pid else "partner2_id"
                    conn.execute(f"UPDATE families SET {col} = NULL WHERE id = ?", (f["id"],))
                else:
                    conn.execute("DELETE FROM families WHERE id = ?", (f["id"],))
                    fams += 1
            # their photos go to the trash with them (unless someone else uses them),
            # so the media step below removes the rows and the files on /share
            deleted_at = conn.execute("SELECT deleted_at FROM people WHERE id = ?", (pid,)).fetchone()[0]
            for ml in conn.execute("SELECT DISTINCT media_id FROM media_links WHERE person_id = ?", (pid,)).fetchall():
                others = conn.execute("SELECT COUNT(*) FROM media_links WHERE media_id = ? AND "
                                      "(person_id IS NULL OR person_id != ?)", (ml[0], pid)).fetchone()[0]
                if not others:
                    conn.execute("UPDATE media SET deleted_at = COALESCE(deleted_at, ?) WHERE id = ?", (deleted_at, ml[0]))
            # references without an ON DELETE rule: cleared, or the DELETE below is refused
            conn.execute("UPDATE sources SET told_by_person_id = NULL WHERE told_by_person_id = ?", (pid,))
            conn.execute("UPDATE people SET merged_into = NULL WHERE merged_into = ?", (pid,))
            conn.execute("DELETE FROM kid_sessions WHERE player_person_id = ?", (pid,))
            conn.execute("DELETE FROM people WHERE id = ?", (pid,))
            counts["families"] += fams

        for pid in people:
            if _purge_one(conn, "person", pid, lambda pid=pid: purge_person(pid)):
                counts["people"] += 1
            else:
                skipped += 1
        for f in conn.execute(f"SELECT id FROM families WHERE {cond}", args).fetchall():
            if _purge_one(conn, "family", f["id"], lambda fid=f["id"]: conn.execute("DELETE FROM families WHERE id = ?", (fid,))):
                counts["families"] += 1
            else:
                skipped += 1

        def purge_source(sid):
            conn.execute("DELETE FROM citations WHERE source_id = ?", (sid,))
            conn.execute("DELETE FROM sources WHERE id = ?", (sid,))

        for s in conn.execute(f"SELECT id FROM sources WHERE {cond}", args).fetchall():
            if _purge_one(conn, "source", s["id"], lambda sid=s["id"]: purge_source(sid)):
                counts["sources"] += 1
            else:
                skipped += 1

        def purge_media(mid):
            conn.execute("UPDATE people SET photo_media_id = NULL WHERE photo_media_id = ?", (mid,))
            conn.execute("UPDATE sources SET media_id = NULL WHERE media_id = ?", (mid,))
            conn.execute("DELETE FROM media WHERE id = ?", (mid,))

        if media.is_online():
            for m in conn.execute(f"SELECT id FROM media WHERE {cond}", args).fetchall():
                if _purge_one(conn, "photo", m["id"], lambda mid=m["id"]: purge_media(mid)):
                    removed_media.append(m["id"])
                    counts["media"] += 1
                else:
                    skipped += 1
        if any(counts.values()):
            db.set_setting(conn, "tree_version", db.new_id())
    for mid in removed_media:                      # files go only after the rows are committed
        media.remove_files(mid)
    if any(counts.values()):
        logger.info("Purged from trash: %s", counts)
    if skipped:
        counts["skipped"] = skipped
    return counts


STALE_TEMP_SECONDS = 6 * 3600


def sweep_temp() -> int:
    """Backup snapshots and restore uploads a browser never finished (family-tree-*.db/.zip in /data, and
    the with-photos backups built on the share), once they're 6 hours old."""
    n = backup_core.remove_stale(config.DATA_DIR, db.TEMP_PREFIX, STALE_TEMP_SECONDS)
    if media.is_online():                       # never touch an unmounted share
        n += backup_core.remove_stale(os.path.join(media.root(), ".exports"), db.TEMP_PREFIX, STALE_TEMP_SECONDS)
    if n:
        logger.info("Removed %s abandoned temporary file(s)", n)
    return n


async def step(tick: int) -> None:
    """One run of `loop` (every 5 minutes): the media check every time; person
    sync and the sweep of abandoned temporary files hourly (every 12th run); purge daily (every 288th)."""
    await run_in_threadpool(media.check)
    from .routers import export as export_router
    await run_in_threadpool(export_router.cleanup)
    if tick % 12 == 0:
        await run_in_threadpool(ha_client.sync_users_blocking)
        await run_in_threadpool(sweep_temp)
    if tick % 288 == 0:
        await run_in_threadpool(purge_expired)


# Every 5 minutes, from main.py's lifespan (tests may replace `loop`).
loop = jobs_core.periodic(300, step, thread=False, tick=True, log=logger, error="Housekeeping step failed")
