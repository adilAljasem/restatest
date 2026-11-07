#!/usr/bin/env python3
"""Compute the module closure for the Resta SaaS slim Odoo 19 build.

Usage: depgraph.py <community_addons_dirs,...> <enterprise_dir> <custom_dir> <out_json>
"""
import ast, json, os, sys

ROOTS = [
    # --- Core apps requested ---
    "accountant", "account_accountant", "l10n_sa", "l10n_sa_edi", "l10n_sa_reports",
    "stock", "stock_enterprise", "stock_barcode", "stock_accountant", "product_expiry",
    "purchase", "purchase_stock", "purchase_accountant", "purchase_requisition",
    "sale_management", "sale_stock", "sale_enterprise",
    "mrp", "mrp_account", "mrp_account_enterprise", "mrp_accountant",
    # --- POS for restaurants ---
    "point_of_sale", "pos_restaurant", "pos_hr", "pos_hr_restaurant", "pos_enterprise",
    "pos_restaurant_preparation_display", "pos_self_order",
    "pos_self_order_preparation_display", "pos_order_tracking_display",
    "pos_loyalty", "pos_discount", "pos_sale", "pos_mrp", "pos_settle_due",
    "pos_account_reports", "pos_restaurant_loyalty",
    "l10n_sa_pos", "l10n_sa_edi_pos",
    # --- Platform / UX ---
    "web_enterprise", "web_mobile", "pos_mobile", "spreadsheet_edition",
    "spreadsheet_dashboard_account_accountant", "spreadsheet_dashboard_stock",
    "spreadsheet_dashboard_purchase_stock", "spreadsheet_dashboard_mrp_account",
    "spreadsheet_dashboard_pos_hr", "spreadsheet_dashboard_sale",
    "base_import", "auth_totp", "mail_enterprise",
    # KSA-capable online payment providers (shipped, not auto-installed)
    "payment_aps", "payment_paymob", "payment_custom",
    # HR ESS (bs_hr_ess) + portal + website, Saudi payroll
    "contacts", "portal", "website", "hr", "hr_attendance", "hr_holidays", "hr_expense", "hr_hourly_cost",
    "hr_payroll", "hr_payroll_account", "l10n_sa_hr_payroll", "l10n_sa_hr_payroll_account",
    "hr_payroll_attendance", "hr_payroll_holidays", "hr_payroll_expense", "hr_work_entry_holidays",
]

# Never ship these even if some auto_install bridge would like them.
DENY_PREFIX = ("l10n_", "test_", "website_", "iot", "pos_iot", "sale_amazon",
               "pos_urban_piper", "pos_platform_order", "pos_blackbox", "pos_restaurant_urban",
               "pos_avatax", "account_avatax", "pos_pricer", "pos_event", "pos_appointment",
               "pos_restaurant_appointment", "pos_sale_subscription", "pos_sale_stock_renting",
               "hr_appraisal", "hr_recruitment", "hr_referral", "hr_contract_salary", "hr_expense_extract", "hr_expense_stripe",
               "hr_payroll_planning", "hr_payroll_fleet", "social", "marketing", "documents", "sign", "knowledge", "helpdesk", "planning",
               "appointment", "esg", "voip", "whatsapp", "web_studio", "account_online",
               "account_bank_statement_extract", "account_invoice_extract", "account_extract",
               "pos_barcodelookup", "stock_barcode_barcodelookup", "product_unspsc", "industry_fsm",
               "project", "timesheet", "crm", "event", "survey", "im_livechat", "quality",
               "ai", "hr_skills", "hr_homeworking", "hr_gantt", "account_peppol", "purchase_edi_ubl",
               "sale_edi_ubl", "google_gmail", "microsoft_outlook", "sale_pdf_quote_builder",
               "account_loans", "product_barcodelookup", "snailmail", "google_address_autocomplete",
               "partner_autocomplete", "mail_bot_hr", "sale_sms", "stock_sms", "api_doc")
ALLOW = {"l10n_sa", "l10n_sa_edi", "l10n_sa_reports", "l10n_sa_pos", "l10n_sa_edi_pos",
         "l10n_gcc_invoice", "l10n_gcc_pos", "l10n_gcc_invoice_stock_account", "iot_base", "l10n_sa_hr_payroll", "l10n_sa_hr_payroll_account"}


def load(dirs):
    mods = {}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for m in os.listdir(d):
            mf = os.path.join(d, m, "__manifest__.py")
            if os.path.isfile(mf):
                man = ast.literal_eval(open(mf, encoding="utf-8").read())
                man["_path"] = os.path.join(d, m)
                mods[m] = man
    return mods


def denied(m):
    return m not in ALLOW and m.startswith(DENY_PREFIX)


def closure(mods, roots):
    keep, missing = set(), set()
    stack = list(roots)
    while stack:
        m = stack.pop()
        if m in keep:
            continue
        if m not in mods:
            missing.add(m)
            continue
        keep.add(m)
        stack.extend(mods[m].get("depends", []) or ["base"])
    return keep, missing


def autoinstall_fixpoint(mods, keep):
    """Add auto_install bridges whose deps are all kept (Odoo would install them)."""
    changed = True
    while changed:
        changed = False
        for m, man in mods.items():
            if m in keep or denied(m):
                continue
            ai = man.get("auto_install")
            deps = man.get("depends", [])
            if not ai:
                continue
            trigger = deps if ai is True else ai
            if all(d in keep for d in trigger):
                sub, miss = closure(mods, [m])
                if not miss and not any(denied(x) for x in sub - keep):
                    keep |= sub
                    changed = True
    return keep


def main():
    comm = sys.argv[1].split(",")
    ent, custom, out = sys.argv[2], sys.argv[3], sys.argv[4]
    mods = load(comm + [ent, custom])
    keep, missing = closure(mods, ROOTS + ["base", "web"])
    keep = autoinstall_fixpoint(mods, keep)
    res = {"keep": sorted(keep), "missing_roots": sorted(missing),
           "by_source": {}}
    for m in sorted(keep):
        src = ("enterprise" if mods[m]["_path"].startswith(ent)
               else "custom" if mods[m]["_path"].startswith(custom) else "community")
        res["by_source"].setdefault(src, []).append(m)
    json.dump(res, open(out, "w"), indent=1)
    for k, v in res["by_source"].items():
        print(k, len(v))
    print("missing roots:", res["missing_roots"])
    leaked = [m for m in keep if denied(m)]
    print("denied-but-required:", leaked)


if __name__ == "__main__":
    main()
