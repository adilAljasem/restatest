{
    "name": "POS Configuration Controls",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "Per-POS invoice, customer, and user access controls",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "security/pos_config_controls_security.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_config_controls/static/src/app/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
