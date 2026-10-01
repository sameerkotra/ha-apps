"""Background upkeep: the daily trash purge, the media online check every
5 minutes, and the hourly sync of Home Assistant persons into users."""
import asyncio
import logging
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db, ha_client, media, settings

logger = logging.getLogger("housekeeping")


def trash_days() -> int:
    """Read at the moment of use: a change in App settings applies at the next purge."""
    return settings.get("trash_days")


def purge_expired() -> dict:
    """The daily purge: everything deleted more than trash_days ago."""
    return purge(trash_days())


def purge(older_than_days: int | None) -> dict:
    """Permanently remove trashed people, families and media — all of them
    (admin 'Empty trash') or those deleted more than N days ago. Not undoable;
    history keeps its snapshots."""
    cutoff = None
    if older_than_days is not None:
        cutoff = (config.utcnow() - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cond = "deleted_at IS NOT NULL" + (" AND deleted_at < ?" if cutoff else "")
    args = [cutoff] if cutoff else []
    counts = {"people": 0, "families": 0, "media": 0}
    removed_media = []
    with db.get_conn() as conn:
        people = [r["id"] for r in conn.execute(f"SELECT id FROM people WHERE {cond}", args)]
        doomed = set(people)
        for pid in people:
            for f in conn.execute("SELECT * FROM families WHERE partner1_id = ? OR partner2_id = ?", (pid, pid)).fetchall():
                other = f["partner2_id"] if f["partner1_id"] == pid else f["partner1_id"]
                if other and other not in doomed:
                    col = "partner1_id" if f["partner1_id"] == pid else "partner2_id"
                    conn.execute(f"UPDATE families SET {col} = NULL WHERE id = ?", (f["id"],))
                else:
                    conn.execute("DELETE FROM families WHERE id = ?", (f["id"],))
                    counts["families"] += 1
            # their photos go to the trash with them (unless someone else uses them),
            # so the media step below removes the rows and the files on /share
            deleted_at = conn.execute("SELECT deleted_at FROM people WHERE id = ?", (pid,)).fetchone()[0]
            for ml in conn.execute("SELECT DISTINCT media_id FROM media_links WHERE person_id = ?", (pid,)).fetchall():
                others = conn.execute("SELECT COUNT(*) FROM media_links WHERE media_id = ? AND "
                                      "(person_id IS NULL OR person_id != ?)", (ml[0], pid)).fetchone()[0]
                if not others:
                    conn.execute("UPDATE media SET deleted_at = COALESCE(deleted_at, ?) WHERE id = ?", (deleted_at, ml[0]))
            conn.execute("DELETE FROM kid_sessions WHERE player_person_id = ?", (pid,))
            conn.execute("DELETE FROM people WHERE id = ?", (pid,))
            counts["people"] += 1
        for f in conn.execute(f"SELECT id FROM families WHERE {cond}", args).fetchall():
            conn.execute("DELETE FROM families WHERE id = ?", (f["id"],))
            counts["families"] += 1
        if media.is_online():
            for m in conn.execute(f"SELECT id FROM media WHERE {cond}", args).fetchall():
                conn.execute("UPDATE people SET photo_media_id = NULL WHERE photo_media_id = ?", (m["id"],))
                conn.execute("DELETE FROM media WHERE id = ?", (m["id"],))
                removed_media.append(m["id"])
                counts["media"] += 1
        if any(counts.values()):
            db.set_setting(conn, "tree_version", db.new_id())
    for mid in removed_media:                      # files go only after the rows are committed
        media.remove_files(mid)
    if any(counts.values()):
        logger.info("Purged from trash: %s", counts)
    return counts


async def loop():
    """Media check every 5 minutes; person sync hourly; purge daily."""
    tick = 0
    while True:
        try:
            await run_in_threadpool(media.check)
            from .routers import export as export_router
            await run_in_threadpool(export_router.cleanup)
            if tick % 12 == 0:
                await run_in_threadpool(ha_client.sync_users_blocking)
            if tick % 288 == 0:
                await run_in_threadpool(purge_expired)
        except Exception:
            logger.exception("Housekeeping step failed")
        tick += 1
        await asyncio.sleep(300)
