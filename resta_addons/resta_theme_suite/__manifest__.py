{
    "name": "Resta Theme Suite",
    "summary": "Carries the Resta brand (resta_sidebar_theme) to every Odoo surface: POS, kitchen display, "
               "self-order kiosk/QR, customer display, order tracking, website, portal, PDF reports",
    "version": "19.0.1.0.0",
    "category": "Themes/Backend",
    "author": "Basic Solution",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["resta_sidebar_theme", "point_of_sale", "pos_self_order", "pos_enterprise", "portal", "website"],
    "assets": {
        # Brand fonts + CSS tokens in every POS-family app (POS, KDS, self-order, customer display, tracking)
        "point_of_sale.base_app": [
            "resta_sidebar_theme/static/src/scss/fonts.scss",
            "resta_sidebar_theme/static/src/scss/tokens.scss",
            "resta_theme_suite/static/src/pos/pos_skin.scss",
        ],
        # Self-order defines its own purple kiosk colour before base_app: win the !default race
        "pos_self_order.assets": [
            ("prepend", "resta_theme_suite/static/src/self_order/brand_variables.scss"),
            "resta_theme_suite/static/src/self_order/self_order_skin.scss",
        ],
        "pos_preparation_display.assets": [
            "resta_theme_suite/static/src/pos/kds_skin.scss",
        ],
        # Website palette defaults (o-color-1..5) = Resta; editable later in the website builder
        "web._assets_primary_variables": [
            "resta_theme_suite/static/src/frontend/website_palette.scss",
        ],
        "web.assets_frontend": [
            "resta_sidebar_theme/static/src/scss/tokens.scss",
            "resta_theme_suite/static/src/frontend/frontend_skin.scss",
        ],
        "web.report_assets_common": [
            "resta_sidebar_theme/static/src/scss/fonts.scss",
            "resta_theme_suite/static/src/report/report_skin.scss",
        ],
    },
    "installable": True,
}
