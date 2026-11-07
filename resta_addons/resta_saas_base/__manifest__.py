{
    "name": "Resta SaaS - Restaurant Base",
    "summary": "Restaurant-only Odoo 19: hides retail POS options, KSA defaults, two onboarding editions",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Basic Solution",
    "website": "https://restaerp.com",
    "license": "OPL-1",
    "depends": [
        # Core apps of the Resta SaaS
        "account_accountant",
        "l10n_sa",
        "l10n_sa_edi_pos",
        "stock",
        "purchase_stock",
        "sale_management",
        "mrp",
        "pos_mrp",
        # Resta brand everywhere (backend, POS, KDS, self-order, website, portal, reports)
        "resta_sidebar_theme_enterprise",
        "resta_theme_suite",
        # Restaurant POS stack
        "point_of_sale",
        "pos_restaurant",
        "pos_hr",
        "pos_loyalty",
        "pos_sale",
        "pos_enterprise",
        "pos_restaurant_preparation_display",
        "pos_self_order",
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
    },
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
