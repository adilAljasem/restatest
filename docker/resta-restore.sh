#!/usr/bin/env bash
# Restore one database (and its filestore) from a Resta backup folder.
#   resta-restore <backup folder> <database> [new name] [--force]
# Examples:
#   resta-restore /backups/daily/2026-11-07_0230 r100002                 # side copy: r100002_restored
#   resta-restore /backups/daily/2026-11-07_0230 r100002 r100002 --force  # replace the live database
set -euo pipefail

SRC="${1:?backup folder}"; DB="${2:?database}"; TARGET="${3:-${DB}_restored}"; FORCE="${4:-}"
FILESTORE_DIR="${FILESTORE_DIR:-/var/lib/odoo/filestore}"
export PGHOST="${DB_HOST:-postgres}" PGPORT="${DB_PORT:-5432}" PGUSER="${DB_USER:-odoo}" PGPASSWORD="${DB_PASSWORD:-}"

[ -f "${SRC}/${DB}.dump" ] || { echo "no ${DB}.dump in ${SRC}" >&2; exit 1; }
(cd "$SRC" && grep -E " \./${DB}\.(dump|filestore\.tar\.gz)$" SHA256SUMS | sha256sum -c -) \
    || { echo "checksum mismatch - backup damaged" >&2; exit 1; }

if [ "$(psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '${TARGET}'")" = "1" ]; then
    [ "$FORCE" = "--force" ] || { echo "${TARGET} exists; add --force to replace it" >&2; exit 1; }
    psql -d postgres -qc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${TARGET}'" >/dev/null
    dropdb "$TARGET"
fi
createdb "$TARGET"
pg_restore -d "$TARGET" --no-owner --role="$PGUSER" -j 4 "${SRC}/${DB}.dump"

if [ -f "${SRC}/${DB}.filestore.tar.gz" ]; then
    rm -rf "${FILESTORE_DIR:?}/${TARGET}"
    tmp="$(mktemp -d)"
    tar -C "$tmp" -xzf "${SRC}/${DB}.filestore.tar.gz"
    mkdir -p "$FILESTORE_DIR"
    mv "${tmp}/${DB}" "${FILESTORE_DIR}/${TARGET}"
    rm -rf "$tmp"
fi
echo "restored ${DB} -> ${TARGET}"
