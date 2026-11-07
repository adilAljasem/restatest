#!/usr/bin/env python3
"""Assemble Basic Solution modules for the Resta SaaS into one addons folder.

Picks one copy per module (rules below), copies it to OUT, writes bundles.json.
"""
import json, os, shutil, sys

INV = json.load(open("/home/claude/out/bs_inventory.json"))
OUT = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/bs_addons"
EXTRA = {  # modules delivered separately by the user (newer than repo copies)
    "resta_sidebar_theme": "/home/claude/clients/theme/resta_sidebar_theme",
    "resta_sidebar_theme_enterprise": "/home/claude/clients/theme/resta_sidebar_theme_enterprise",
}
HR_ROOT = "/home/claude/clients/hr/bs_hr_ess"

# Same version, different code: explicit choice (+ graft migrations folder)
PICK = {
    "inventory_stock_adjustments": ("pino", "bunbastic"),   # backdated batch apply + more tests
    "stock_request": ("pino", "bunbastic"),                 # request-line domain fix
    "pos_config_controls": ("pino", None),                  # extra picking security rule
    "pos_analytic_account": ("puls", None),                 # move date = session business day
}

COMMON = [
    "resta_sidebar_theme", "resta_sidebar_theme_enterprise",
    "pos_business_day", "pos_closing_report", "pos_closing_report_hr", "pos_kitchen_ticket_custom",
    "pino_pos_receipt", "odoo_pos_custom_branding", "bi_pos_multi_layout", "pos_control_button_resize",
    "pos_ticket_pager_ipad", "pos_set_default_customer", "pos_config_controls",
    "dk_pos_employee_access_rights", "employee_loyalty_limit", "kds_order_timer", "pos_kds_sound",
    "l10n_sa_edi_pos_async", "pos_analytic_account", "hide_menu_user",
    "inventory_stock_adjustments", "stock_request", "the_dash",
    "bs_hr_ess",
]
DINEIN = [  # Pino (Beno) production set, minus Beno-only customisations
    "product_modifiers", "bs_pos_blend_intgeration", "bs_pos_blend_intgeration_zatca",
    "bs_pos_session_handover", "pos_shared_floor_sync", "pos_table_opened_by",
    "bs_pos_kitchen_flow", "bs_pos_kitchen_bilingual", "bs_pos_products_by_category_only",
    "bs_pos_customer_search_autofill", "bs_pos_refund_guard", "pos_ticket_partner_phone",
    "pos_hide_products", "exclusive_customer_discount", "loyalty_reward_description_kanban",
    "pos_analytic_tag_custom", "stock_quant_qty_guard", "pos_bonat_loyalty_online",
]
QSR = [  # Bunbastic + Puls production sets
    "pos_restaurant_combo_modifiers", "bs_pos_delivery_core", "bs_pos_delivery_blend",
    "bs_pos_delivery_tryorder", "bs_pos_kitchen_resend", "pos_kitchen_send_before_payment",
    "pos_call_center", "pos_disable_decimal", "pos_order_tracking_nps", "pos_order_tracking_nps_hr",
    "pos_bonat_loyalty_online", "pos_cash_management", "pos_blind_closing", "pos_cashier_shortage_report",
    "bs_pos_payment_clearing", "pos_deferred_invoice_pdf", "batch_scrap_orders", "mrp_enhancement",
    "mrp_bom_consumption_adjustment", "custom_stock_ageing_report", "product_category_inventory_location",
    "bs_recurring_purchase_agreement", "bs_analytic_center_profit_loss", "bs_analytic_distribution_rate",
    "bs_company_vendor_bill_validation", "petty_cash", "employee_loan_management", "uom_aretx",
]
EXCLUDED = {
    "pino_panel": "Beno-only panel (recruiting, invitations); needs CRM/helpdesk/sign/slides",
    "pino_theme": "Beno brand identity, replaced by resta_sidebar_theme",
    "pino_backend_custom": "Beno production customisations; needs sign",
    "pino_pos_custom": "Beno production POS customisations",
    "pino_dash_guard": "Guard for the Beno board snapshot",
    "pino_calories": "Calories computed for the Beno menu (candidate to generalise for SFDA)",
    "bi_hide_show_menu_app": "Duplicate of hide_menu_user (23 MB)",
    "codusic_pos_order_types": "Odoo 19 presets replace order types",
    "order_connector": "Old TryOrder connector, replaced by bs_pos_delivery_tryorder (Connect)",
}


def version_key(v):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def choose(name):
    per = INV[name]
    if name in PICK:
        repo, graft = PICK[name]
        return per[repo]["path"], (per[graft]["path"] if graft else None), repo
    repo = max(per, key=lambda r: version_key(per[r]["version"]))
    return per[repo]["path"], None, repo


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    origin = {}
    wanted = sorted(set(COMMON + DINEIN + QSR) - {"bs_hr_ess"} - set(EXTRA))
    for name in wanted:
        src, graft, repo = choose(name)
        shutil.copytree(src, os.path.join(OUT, name), ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        if graft and os.path.isdir(os.path.join(graft, "migrations")) and not os.path.isdir(os.path.join(OUT, name, "migrations")):
            shutil.copytree(os.path.join(graft, "migrations"), os.path.join(OUT, name, "migrations"))
        origin[name] = f"{repo} {INV[name][repo]['version']}"
    for name, src in EXTRA.items():
        shutil.copytree(src, os.path.join(OUT, name), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        origin[name] = "upload resta_sidebar_theme_odoo19.zip"
    for name in os.listdir(HR_ROOT):
        if os.path.isfile(os.path.join(HR_ROOT, name, "__manifest__.py")):
            shutil.copytree(os.path.join(HR_ROOT, name), os.path.join(OUT, name), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            origin[name] = "upload bs_hr_ess_odoo19.zip"
    json.dump({"common": COMMON, "dinein": DINEIN, "qsr": QSR, "excluded": EXCLUDED, "origin": origin},
              open("/home/claude/out/bundles.json", "w"), indent=1, ensure_ascii=False)
    print("copied", len(origin), "modules to", OUT)


if __name__ == "__main__":
    main()
