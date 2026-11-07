#!/usr/bin/env bash
# Resta backup service: every database (platform, templates, each restaurant) +
# its filestore, daily / weekly / monthly retention, optional off-site copy with
# rclone (Backblaze B2, S3, ...) and a "heartbeat" ping (Uptime Kuma push URL).
#   resta-backup          -> loop forever, one run per day at BACKUP_TIME
#   resta-backup now      -> one run, then exit
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
FILESTORE_DIR="${FILESTORE_DIR:-/var/lib/odoo/filestore}"
BACKUP_TIME="${BACKUP_TIME:-02:30}"            # local time (TZ)
KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
KEEP_WEEKS="${BACKUP_KEEP_WEEKS:-4}"
KEEP_MONTHS="${BACKUP_KEEP_MONTHS:-6}"
WEEKLY_DAY="${BACKUP_WEEKLY_DAY:-5}"           # 1=Mon ... 5=Fri ... 7=Sun
RCLONE_REMOTE="${BACKUP_RCLONE_REMOTE:-}"      # e.g. b2:resta-backups/prod
NOTIFY_URL="${BACKUP_NOTIFY_URL:-}"            # pinged on success; "?status=down" appended on failure
export PGHOST="${DB_HOST:-postgres}" PGPORT="${DB_PORT:-5432}" PGUSER="${DB_USER:-odoo}" PGPASSWORD="${DB_PASSWORD:-}"

log() { echo "[resta-backup $(date '+%F %T')] $*"; }

notify() {  # $1 = up|down, $2 = message
    [ -n "$NOTIFY_URL" ] || return 0
    local sep="?"; [[ "$NOTIFY_URL" == *\?* ]] && sep="&"
    curl -fsS -m 15 --retry 3 -o /dev/null \
        "${NOTIFY_URL}${sep}status=$1&msg=$(printf '%s' "$2" | sed 's/ /%20/g')" || log "notify failed"
}

run_backup() {
    local stamp dir dbs count=0
    stamp="$(date +%F_%H%M)"
    dir="${BACKUP_DIR}/daily/${stamp}"
    mkdir -p "$dir"
    dbs="$(psql -d postgres -Atc "SELECT datname FROM pg_database
            WHERE datallowconn AND datname NOT IN ('postgres','template0','template1') ORDER BY 1")"
    for db in $dbs; do
        pg_dump -d "$db" -Fc -Z 6 -f "${dir}/${db}.dump"
        if [ -d "${FILESTORE_DIR}/${db}" ]; then
            tar -C "$FILESTORE_DIR" -czf "${dir}/${db}.filestore.tar.gz" "$db"
        fi
        count=$((count + 1))
    done
    (cd "$dir" && sha256sum ./* > SHA256SUMS)
    log "daily ${stamp}: ${count} databases, $(du -sh "$dir" | cut -f1)"

    if [ "$(date +%u)" = "$WEEKLY_DAY" ]; then
        mkdir -p "${BACKUP_DIR}/weekly" && cp -al "$dir" "${BACKUP_DIR}/weekly/${stamp}"
    fi
    if [ "$(date +%d)" = "01" ]; then
        mkdir -p "${BACKUP_DIR}/monthly" && cp -al "$dir" "${BACKUP_DIR}/monthly/${stamp}"
    fi

    prune daily "$KEEP_DAYS"
    prune weekly $(( KEEP_WEEKS * 7 ))
    prune monthly $(( KEEP_MONTHS * 31 ))

    if [ -n "$RCLONE_REMOTE" ]; then
        log "off-site copy to ${RCLONE_REMOTE}"
        rclone sync "$BACKUP_DIR" "$RCLONE_REMOTE" --transfers 4 --checkers 8 --exclude '.last_*'
    fi
    date '+%F %T' > "${BACKUP_DIR}/.last_success"
    notify up "${count} databases backed up"
}

prune() {  # $1 = folder, $2 = max age in days
    [ -d "${BACKUP_DIR}/$1" ] || return 0
    find "${BACKUP_DIR}/$1" -mindepth 1 -maxdepth 1 -type d -mtime +"$2" -print -exec rm -rf {} + \
        | sed "s/^/[resta-backup] pruned /"
}

safe_run() {
    if ! run_backup; then
        log "BACKUP FAILED"
        notify down "backup failed"
        return 1
    fi
}

seconds_until() {  # $1 = HH:MM local
    local now target
    now=$(date +%s)
    target=$(date -d "today $1" +%s)
    [ "$target" -le "$now" ] && target=$(date -d "tomorrow $1" +%s)
    echo $(( target - now ))
}

if [ "${1:-}" = "now" ]; then
    safe_run
    exit $?
fi

log "backup service started: daily at ${BACKUP_TIME}, keep ${KEEP_DAYS}d/${KEEP_WEEKS}w/${KEEP_MONTHS}m, off-site: ${RCLONE_REMOTE:-none}"
if [ ! -f "${BACKUP_DIR}/.last_success" ] && [ "${BACKUP_ON_START:-true}" = "true" ]; then
    safe_run || true
fi
while true; do
    sleep "$(seconds_until "$BACKUP_TIME")"
    safe_run || true
done
