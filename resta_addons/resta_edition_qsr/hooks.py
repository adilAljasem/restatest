from odoo.addons.resta_saas_base.hooks import _param_list, run_when_chart_ready, soft_install

# Bun Fastic / Puls module bundle for the QSR & cafe edition. Edit the system
# parameter "resta.qsr_modules" to change it without touching code.
QSR_BS_MODULES = [
    "pos_restaurant_combo_modifiers",
    "bs_pos_delivery_core",
    "bs_pos_delivery_blend",
    "bs_pos_delivery_tryorder",
    "bs_pos_kitchen_resend",
    "pos_kitchen_send_before_payment",
    "pos_call_center",
    "pos_disable_decimal",
    "pos_order_tracking_nps",
    "pos_order_tracking_nps_hr",
    "pos_bonat_loyalty_online",
    "pos_cash_management",
    "pos_blind_closing",
    "pos_cashier_shortage_report",
    "bs_pos_payment_clearing",
    "pos_deferred_invoice_pdf",
    "batch_scrap_orders",
    "mrp_enhancement",
    "mrp_bom_consumption_adjustment",
    "custom_stock_ageing_report",
    "product_category_inventory_location",
    "bs_recurring_purchase_agreement",
    "bs_analytic_center_profit_loss",
    "bs_analytic_distribution_rate",
    "bs_company_vendor_bill_validation",
    "petty_cash",
    "employee_loan_management",
    "uom_aretx",
]


def post_init_hook(env):
    soft_install(env, _param_list(env, "resta.qsr_modules", QSR_BS_MODULES), "qsr")
    run_when_chart_ready(env, _create_pos)


def _create_pos(env):
    lang = "ar_001" if env["res.lang"]._lang_get("ar_001") else env.lang
    Config = env["pos.config"].with_context(lang=lang)
    if not Config.search_count([("resta_edition", "=", "qsr")]):
        Config.load_onboarding_resta_qsr_scenario()
