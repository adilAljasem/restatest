#!/usr/bin/env bash
# Update Odoo modules on the platform, the templates and every restaurant database.
#   resta-upgrade <modules>            e.g. resta-upgrade the_dash,resta_saas_tenant
#   resta-upgrade <modules> <db> ...   only the listed databases
#   resta-upgrade --install <modules>  install new modules on the templates and every
#                                      restaurant database (never on the platform database)
# Run after a new deployment that changed module code. Stops at the first failure.
set -euo pipefail

ODOO_ROOT="${ODOO_ROOT:-/opt/odoo}"
ODOO_RC="${ODOO_RC:-/etc/odoo/odoo.conf}"
MODE="-u"
if [ "${1:-}" = "--install" ]; then MODE="-i"; shift; fi
MODULES="${1:?modules to update (comma separated), or "all"}"; shift
TEMPLATES=$(psql -d postgres -Atc "SELECT datname FROM pg_database WHERE datistemplate AND datname NOT IN ('template0','template1')")
if [ "$#" -gt 0 ]; then
    DBS="$*"
else
    DBS=$(psql -d postgres -Atc "SELECT datname FROM pg_database WHERE datname NOT IN ('postgres','template0','template1') ORDER BY datistemplate DESC, datname")
fi

for db in $DBS; do
    if [ "$MODE" = "-i" ] && [ "$(psql -d "$db" -Atc "SELECT 1 FROM ir_module_module WHERE name = 'resta_saas_tenant' AND state = 'installed'" 2>/dev/null)" != "1" ]; then
        echo "[resta-upgrade] $db: not a restaurant database, skipped"
        continue
    fi
    is_tpl=false
    if echo "$TEMPLATES" | grep -qx "$db"; then
        is_tpl=true
        psql -d postgres -qc "ALTER DATABASE \"$db\" WITH IS_TEMPLATE false"
    fi
    echo "[resta-upgrade] $db: $MODE $MODULES"
    if ! python3 "${ODOO_ROOT}/odoo/odoo-bin" -c "$ODOO_RC" -d "$db" "$MODE" "$MODULES" --stop-after-init --no-http --log-level=warn; then
        echo "[resta-upgrade] FAILED on $db - fix before continuing (the remaining databases were not touched)" >&2
        $is_tpl && psql -d postgres -qc "ALTER DATABASE \"$db\" WITH IS_TEMPLATE true"
        exit 1
    fi
    $is_tpl && psql -d postgres -qc "ALTER DATABASE \"$db\" WITH IS_TEMPLATE true"
done
echo "[resta-upgrade] done"
