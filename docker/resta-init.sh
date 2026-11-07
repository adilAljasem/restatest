#!/usr/bin/env bash
# First-start preparation of the Resta platform (idempotent: safe on every start).
#   1. the platform database   (resta_saas_master)  - name = first label of RESTA_DOMAIN
#   2. the two edition templates (dine-in / QSR), flagged IS_TEMPLATE so that crons,
#      the dbfilter and customers never touch them
#   3. platform settings (restaurant URL pattern, platform URL)
set -euo pipefail

ODOO_ROOT="${ODOO_ROOT:-/opt/odoo}"
ODOO_RC="${ODOO_RC:-/etc/odoo/odoo.conf}"
: "${RESTA_DOMAIN:?RESTA_DOMAIN is required (e.g. resta.sa)}"
MASTER_DB="${RESTA_MASTER_DB:-${RESTA_DOMAIN%%.*}}"
TPL_DINEIN="${RESTA_TEMPLATE_DINEIN:-resta_tpl_dinein}"
TPL_QSR="${RESTA_TEMPLATE_QSR:-resta_tpl_qsr}"
SCHEME="${RESTA_SCHEME:-https}"
ODOO_BIN=(python3 "${ODOO_ROOT}/odoo/odoo-bin")
ODOO=("${ODOO_BIN[@]}" -c "$ODOO_RC" --no-http --stop-after-init --load-language=ar_001)

db_exists() {
    [ "$(psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$1'")" = "1" ]
}

install_db() {  # $1 = database, $2 = modules
    if db_exists "$1"; then
        echo "[resta-init] $1 already exists"
        return 0
    fi
    echo "[resta-init] creating $1 with $2 (several minutes)..."
    if ! "${ODOO[@]}" -d "$1" -i "$2" --without-demo=all --log-level=warn; then
        echo "[resta-init] FAILED to create $1 - dropping the half-built database" >&2
        dropdb --if-exists "$1"
        return 1
    fi
}

install_db "$MASTER_DB" "resta_saas_master,l10n_sa" &
p1=$!
install_db "$TPL_DINEIN" "resta_edition_dinein,resta_saas_tenant" &
p2=$!
install_db "$TPL_QSR" "resta_edition_qsr,resta_saas_tenant" &
p3=$!
status=0
for p in $p1 $p2 $p3; do wait "$p" || status=1; done
[ "$status" -eq 0 ] || { echo "[resta-init] initialisation failed" >&2; exit 1; }

for tpl in "$TPL_DINEIN" "$TPL_QSR"; do
    psql -d postgres -qc "ALTER DATABASE \"$tpl\" WITH IS_TEMPLATE true"
done

echo "[resta-init] platform settings for ${SCHEME}://${RESTA_DOMAIN}"
"${ODOO_BIN[@]}" shell -c "$ODOO_RC" -d "$MASTER_DB" --no-http --log-level=error <<PY >/dev/null
ICP = env['ir.config_parameter'].sudo()
ICP.set_param('resta.tenant_url_pattern', '${SCHEME}://{db}.${RESTA_DOMAIN}')
ICP.set_param('resta.master_url', '${SCHEME}://${RESTA_DOMAIN}')
ICP.set_param('web.base.url', '${SCHEME}://${RESTA_DOMAIN}')
ICP.set_param('web.base.url.freeze', 'True')
ICP.set_param('resta.template_dinein', '${TPL_DINEIN}')
ICP.set_param('resta.template_qsr', '${TPL_QSR}')
env.cr.commit()
PY
echo "[resta-init] ready"
