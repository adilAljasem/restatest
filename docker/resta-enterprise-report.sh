#!/usr/bin/env bash
# Read-only: lists, for every restaurant database, the Odoo Enterprise modules still
# installed. Run it before setting RESTA_ENTERPRISE=0 (Community-only image).
#   docker exec -it <odoo container> resta-enterprise-report
set -euo pipefail
ODOO_ROOT="${ODOO_ROOT:-/opt/odoo}"
export PGHOST="${DB_HOST:-postgres}" PGPORT="${DB_PORT:-5432}" PGUSER="${DB_USER:-odoo}" PGPASSWORD="${DB_PASSWORD:-}"

if ! ls "${ODOO_ROOT}"/enterprise/*/__manifest__.py >/dev/null 2>&1; then
    echo "[resta] no Enterprise modules in this image: nothing to check."
    exit 0
fi
names=$(for m in "${ODOO_ROOT}"/enterprise/*/__manifest__.py; do basename "$(dirname "$m")"; done | sed "s/.*/'&'/" | paste -sd, -)
clean=0
for db in $(psql -d postgres -Atc "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1"); do
    has_odoo=$(psql -d "$db" -Atc "SELECT to_regclass('public.ir_module_module') IS NOT NULL" 2>/dev/null || echo f)
    [ "$has_odoo" = "t" ] || continue
    installed=$(psql -d "$db" -Atc "SELECT string_agg(name, ', ' ORDER BY name) FROM ir_module_module
                                    WHERE state IN ('installed', 'to upgrade', 'to install') AND name IN (${names})")
    if [ -n "$installed" ]; then
        echo "$db: $installed"
    else
        clean=$((clean + 1))
    fi
done
echo "[resta] databases without Enterprise modules: ${clean}"
