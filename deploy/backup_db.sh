#!/usr/bin/env bash
# CC daily Postgres backup (Story 4.4, AC3) — runs on the VPS via
# cc-backup.timer (systemd). Manual run:  sudo bash /srv/cc/deploy/backup_db.sh
#
# Postgres on this VPS is Dockerized (container `lohari-postgres`, see
# deploy/README.md step 5) — pg_dump runs INSIDE the container over the local
# socket (the official postgres image trusts local connections), so no
# password ever touches this script or the shell history.
#
# Output: /var/backups/cc/cc-<UTC timestamp>.dump — pg_dump custom format
# (compressed, restorable selectively with pg_restore). Every dump is
# verified readable (pg_restore --list) before old dumps are pruned.
# Restore procedure: docs/runbooks/backups-y-restauracion.md
#
# Overridable via environment (systemd unit or shell):
#   BACKUP_DIR      (default /var/backups/cc)
#   PG_CONTAINER    (default lohari-postgres)
#   PG_USER         (default cc)
#   PG_DB           (default cc)
#   RETENTION_DAYS  (default 14)
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/cc}"
PG_CONTAINER="${PG_CONTAINER:-lohari-postgres}"
PG_USER="${PG_USER:-cc}"
PG_DB="${PG_DB:-cc}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (docker + /var/backups):  sudo bash $0" >&2
    exit 1
fi

# Backups hold every tenant's data — root-only directory and files.
umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y-%m-%d_%H%M%S)"
dump="$BACKUP_DIR/cc-$stamp.dump"
tmp="$dump.partial"

echo "==> [1/3] pg_dump $PG_DB from container $PG_CONTAINER"
# --format=custom: compressed, integrity-checkable, selective restore.
# Written to .partial first so a failed dump never looks like a backup.
docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --format=custom > "$tmp"
mv "$tmp" "$dump"

echo "==> [2/3] verify the dump is restorable (pg_restore --list)"
# Reads the dump's table of contents — catches truncated/corrupt archives.
# A full restore drill (into a scratch DB) is the runbook's monthly task.
docker exec -i "$PG_CONTAINER" pg_restore --list < "$dump" > /dev/null

echo "==> [3/3] prune dumps older than $RETENTION_DAYS days"
find "$BACKUP_DIR" -maxdepth 1 -name 'cc-*.dump' -type f \
    -mtime +"$RETENTION_DAYS" -print -delete
# Never leave stale partials behind (a previous failed run).
find "$BACKUP_DIR" -maxdepth 1 -name 'cc-*.dump.partial' -type f -delete

size="$(du -h "$dump" | cut -f1)"
echo "==> backup done: $dump ($size)"
