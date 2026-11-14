{
    "name": "POS Blind Closing",
    "summary": "Hide all expected amounts in the POS closing popup; the session is closed on the cashier's counted amounts only",
    "version": "19.0.1.1.0",
    "category": "Sales/Point of Sale",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_blind_closing/static/src/closing_popup/closing_popup.js",
            "pos_blind_closing/static/src/closing_popup/closing_popup.xml",
        ],
    },
    "installable": True,
    "application": False,
}
