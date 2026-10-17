#!/bin/sh
# Start-up (as root): /data — the database and the encrypted vault files, mapped by the Supervisor and owned by
# root — goes to the app's own user, then the app runs as that user (security review 2026-10, #5). Nothing
# else needs root: /tmp is world-writable, /app is read-only for the app.
set -e
chown -R vault:vault /data 2>/dev/null || true
exec su-exec vault:vault "$@"
