from odoo.addons.resta_saas_base.hooks import _param_list, run_when_chart_ready, soft_install

# Beno (بينو) module bundle for the dine-in edition. Edit the system parameter
# "resta.dinein_modules" to change it without touching code.
DINEIN_BS_MODULES = [
    "product_modifiers",
    "bs_pos_blend_intgeration",
    "bs_pos_blend_intgeration_zatca",
    "bs_pos_session_handover",
    "pos_shared_floor_sync",
    "pos_table_opened_by",
    "bs_pos_kitchen_flow",
    "bs_pos_kitchen_bilingual",
    "bs_pos_products_by_category_only",
    "bs_pos_customer_search_autofill",
    "bs_pos_refund_guard",
    "pos_ticket_partner_phone",
    "pos_hide_products",
    "exclusive_customer_discount",
    "loyalty_reward_description_kanban",
    "pos_analytic_tag_custom",
    "stock_quant_qty_guard",
    "pos_bonat_loyalty_online",
]


def post_init_hook(env):
    soft_install(env, _param_list(env, "resta.dinein_modules", DINEIN_BS_MODULES), "dinein")
    run_when_chart_ready(env, _create_pos)


def _create_pos(env):
    lang = "ar_001" if env["res.lang"]._lang_get("ar_001") else env.lang
    Config = env["pos.config"].with_context(lang=lang)
    if not Config.search_count([("resta_edition", "=", "dinein")]):
        Config.load_onboarding_resta_dinein_scenario()
