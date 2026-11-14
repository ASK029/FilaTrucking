#!/bin/sh
# Nightly MySQL backup (QA-027). Runs inside the "backup" service of docker-compose.prod.yml, or by hand:
#   docker compose -f docker-compose.prod.yml run --rm backup /deploy/backup_mysql.sh once
#
# Writes /backups/filatrucking-YYYYmmdd-HHMMSS.sql.gz, verifies it, keeps the newest $KEEP_DAYS days.
# Needs: MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD (environment), BACKUP_DIR (default /backups).
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
INTERVAL_HOURS="${INTERVAL_HOURS:-24}"
MYSQL_HOST="${MYSQL_HOST:-mysql}"

backup_once() {
    stamp="$(date -u +%Y%m%d-%H%M%S)"
    target="$BACKUP_DIR/filatrucking-$stamp.sql.gz"
    tmp="$target.partial"
    mkdir -p "$BACKUP_DIR"

    # --single-transaction: consistent snapshot without locking the live app (InnoDB)
    # --no-tablespaces: the application user has no PROCESS privilege
    if MYSQL_PWD="$MYSQL_PASSWORD" mysqldump -h "$MYSQL_HOST" -u "$MYSQL_USER" \
            --single-transaction --no-tablespaces --routines --triggers --events \
            --default-character-set=utf8mb4 "$MYSQL_DATABASE" | gzip -9 > "$tmp"; then
        # a dump that does not end with the mysqldump trailer is truncated: never keep it as a "good" backup
        if gzip -t "$tmp" && gunzip -c "$tmp" | tail -n 5 | grep -q "Dump completed"; then
            mv "$tmp" "$target"
            echo "$(date -u +%FT%TZ) backup ok: $target ($(wc -c < "$target") bytes)"
        else
            rm -f "$tmp"
            echo "$(date -u +%FT%TZ) BACKUP FAILED: dump is incomplete" >&2
            return 1
        fi
    else
        rm -f "$tmp"
        echo "$(date -u +%FT%TZ) BACKUP FAILED: mysqldump returned an error" >&2
        return 1
    fi

    # retention: delete verified dumps older than KEEP_DAYS (never the newest one)
    find "$BACKUP_DIR" -name 'filatrucking-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
}

if [ "${1:-loop}" = "once" ]; then
    backup_once
    exit $?
fi

# loop mode: first backup shortly after start (so a fresh deploy is protected at once), then every INTERVAL_HOURS
sleep 60
while :; do
    backup_once || true   # keep looping: the next night may succeed; the failure is in the container log
    sleep "$((INTERVAL_HOURS * 3600))"
done
