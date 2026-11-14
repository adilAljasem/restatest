{
    "name": "Resta SaaS - Restaurant Base",
    "summary": "Restaurant-only Odoo 19: hides retail POS options, KSA defaults, two onboarding editions",
    "version": "19.0.1.1.0",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": [
        # Core apps of the Resta SaaS (accounting, payroll and barcode built on Community)
        "resta_account",
        "resta_payroll",
        "resta_barcode",
        "l10n_sa",
        "l10n_sa_edi_pos",
        "stock",
        "purchase_stock",
        "sale_management",
        "mrp",
        "pos_mrp",
        # Resta brand everywhere (backend, POS, kitchen screen, self-order, portal, reports, sign-in)
        "resta_sidebar_theme",
        "resta_theme_suite",
        "resta_settings",
        "resta_brand",
        # Restaurant POS stack
        "point_of_sale",
        "pos_restaurant",
        "pos_hr",
        "pos_loyalty",
        "pos_sale",
        "pos_self_order",
        # Resta kitchen screen + customer order-status screen (built on Community)
        "resta_kds",
    ],
    "data": [
        "data/pos_preset_data.xml",
        "data/ir_config_parameter_data.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_saas_base/static/src/backend/**/*",
        ],
        # the POS dashboard it patches is not part of the frontend unit-test bundle
        "web.assets_unit_tests_setup": [
            ("remove", "resta_saas_base/static/src/backend/**/*"),
        ],
    },
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
