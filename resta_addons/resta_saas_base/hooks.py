import logging

_logger = logging.getLogger(__name__)

# Basic Solution modules shared by both editions. Installed only if present in the
# addons path (soft dependency), so the SaaS image still builds without them.
# Override with system parameter "resta.common_modules" (comma separated).
COMMON_BS_MODULES = [
    "pos_business_day",
    "pos_closing_report",
    "pos_closing_report_hr",
    "pos_kitchen_ticket_custom",
    "pino_pos_receipt",
    "odoo_pos_custom_branding",
    "bi_pos_multi_layout",
    "pos_control_button_resize",
    "pos_ticket_pager_ipad",
    "pos_set_default_customer",
    "pos_config_controls",
    "dk_pos_employee_access_rights",
    "employee_loyalty_limit",
    "kds_order_timer",
    "pos_kds_sound",
    "l10n_sa_edi_pos_async",
    "pos_analytic_account",
    "hide_menu_user",
    "inventory_stock_adjustments",
    "stock_request",
    "the_dash",
    "bs_hr_ess",
]


def _param_list(env, key, default):
    raw = env["ir.config_parameter"].sudo().get_param(key)
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(default)


def soft_install(env, names, label):
    """Mark the given modules 'to install' when available; log the missing ones.

    The Odoo 19 loader loops while modules are 'to install', so modules marked
    from a post_init_hook are installed in the same run.
    """
    Module = env["ir.module.module"].sudo()
    found = Module.search([("name", "in", names)])
    to_install = found.filtered(lambda m: m.state == "uninstalled")
    missing = sorted(set(names) - set(found.mapped("name")))
    if to_install:
        to_install.button_install()
        _logger.info("Resta %s: installing %s", label, ", ".join(to_install.mapped("name")))
    if missing:
        _logger.warning("Resta %s: modules not in addons path, skipped: %s", label, ", ".join(missing))
    env["ir.config_parameter"].sudo().set_param("resta.missing_modules.%s" % label, ",".join(missing))
    return missing


def _load_sa_chart(env):
    company = env.ref("base.main_company")
    if company.chart_template == "sa" or company._existing_accounting():
        return
    company.write({"country_id": env.ref("base.sa").id})
    env["account.chart.template"].try_loading("sa", company, install_demo=False)


def run_when_chart_ready(env, callback):
    """Run callback(env) once the company has its chart of accounts.

    On a fresh database the account module defers loading the chart to the end
    of the registry load (registry._auto_install_template); chain after it.
    """
    registry = env.registry
    previous = getattr(registry, "_auto_install_template", None)
    if previous:
        def chained(env2):
            previous(env2)
            callback(env2)
        registry._auto_install_template = chained
    else:
        callback(env)


def _setup_company(env):
    """KSA defaults for the main company: country, currency, chart of accounts, Arabic."""
    company = env.ref("base.main_company")
    sar = env.ref("base.SAR")
    if not sar.active:
        sar.active = True
    if not company.country_id:
        company.country_id = env.ref("base.sa")
    registry = env.registry
    if getattr(registry, "_auto_install_template", None):
        # Replace the deferred generic chart by the Saudi one
        registry._auto_install_template = _load_sa_chart
    else:
        try:
            _load_sa_chart(env)
        except Exception:  # noqa: BLE001 - never block the install on CoA
            _logger.exception("Resta: could not load the Saudi chart of accounts")
    lang = env["res.lang"].with_context(active_test=False).search([("code", "=", "ar_001")], limit=1)
    if lang and not lang.active:
        env["res.lang"]._activate_lang("ar_001")
    if lang:
        lang.write({"week_start": "7"})


def post_init_hook(env):
    _setup_company(env)
    soft_install(env, _param_list(env, "resta.common_modules", COMMON_BS_MODULES), "common")
