#!/usr/bin/env bash
# Resta container entrypoint: writes odoo.conf from the environment, waits for
# PostgreSQL, optionally prepares the platform on first start, then runs Odoo.
#   CMD odoo            -> the web application (default)
#   CMD resta-backup    -> the backup service
#   anything else       -> executed as-is (e.g. "bash", "resta-restore ...")
set -euo pipefail

ODOO_ROOT="${ODOO_ROOT:-/opt/odoo}"
ODOO_RC="${ODOO_RC:-/etc/odoo/odoo.conf}"
DATA_DIR="${ODOO_DATA_DIR:-/var/lib/odoo}"

: "${DB_HOST:=postgres}" "${DB_PORT:=5432}" "${DB_USER:=odoo}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${ODOO_MASTER_PASSWORD:?ODOO_MASTER_PASSWORD is required}"
: "${ODOO_WORKERS:=4}" "${ODOO_CRON_THREADS:=2}" "${ODOO_DB_MAXCONN:=16}"
: "${ODOO_MEMORY_SOFT_MB:=2048}" "${ODOO_MEMORY_HARD_MB:=2560}"
: "${ODOO_LOG_LEVEL:=info}"

export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGUSER="$DB_USER" PGPASSWORD="$DB_PASSWORD"

write_conf() {
    mkdir -p "$(dirname "$ODOO_RC")"
    # Odoo Enterprise is optional: RESTA_ENTERPRISE=0 (or an enterprise folder without modules)
    # runs Community + Resta only. Run resta-enterprise-report before switching an existing server.
    local enterprise_addons=""
    if [ "${RESTA_ENTERPRISE:-1}" != "0" ] && ls "${ODOO_ROOT}"/enterprise/*/__manifest__.py >/dev/null 2>&1; then
        enterprise_addons="${ODOO_ROOT}/enterprise,"
    fi
    cat > "$ODOO_RC" <<EOF
[options]
addons_path = ${ODOO_ROOT}/odoo/addons,${enterprise_addons}${ODOO_ROOT}/resta_addons,${ODOO_ROOT}/bs_addons
data_dir = ${DATA_DIR}
db_host = ${DB_HOST}
db_port = ${DB_PORT}
db_user = ${DB_USER}
db_password = ${DB_PASSWORD}
db_maxconn = ${ODOO_DB_MAXCONN}
admin_passwd = ${ODOO_MASTER_PASSWORD}
; apex -> RESTA_MASTER_DB (default "restaerp"); test1.restaerp.com -> "test1"
dbfilter = ^%d\$
list_db = False
proxy_mode = True
http_port = 8069
gevent_port = 8072
workers = ${ODOO_WORKERS}
max_cron_threads = ${ODOO_CRON_THREADS}
limit_memory_soft = $(( ODOO_MEMORY_SOFT_MB * 1024 * 1024 ))
limit_memory_hard = $(( ODOO_MEMORY_HARD_MB * 1024 * 1024 ))
limit_time_cpu = 600
limit_time_real = 1200
limit_time_real_cron = 1800
log_level = ${ODOO_LOG_LEVEL}
EOF
    chmod 600 "$ODOO_RC"
}

wait_for_db() {
    local tries=0
    until pg_isready -q -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres; do
        tries=$((tries + 1))
        if [ "$tries" -ge 60 ]; then
            echo "[resta] PostgreSQL not reachable at ${DB_HOST}:${DB_PORT}" >&2
            exit 1
        fi
        echo "[resta] waiting for PostgreSQL (${tries}/60)..."
        sleep 2
    done
}

write_conf
wait_for_db

case "${1:-odoo}" in
    odoo)
        if [ "${RESTA_AUTO_INIT:-true}" = "true" ]; then
            resta-init
        fi
        shift || true
        exec python3 "${ODOO_ROOT}/odoo/odoo-bin" -c "$ODOO_RC" "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
