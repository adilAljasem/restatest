# -*- coding: utf-8 -*-
{
    "name": "BS POS Session Handover",
    "summary": "Transfer unpaid restaurant table orders between POS cashier sessions.",
    "version": "19.0.1.3.0",
    "category": "Point of Sale",
    "author": "Pino",
    "license": "LGPL-3",
    "depends": [
        "pos_hr",
        "pos_restaurant",
    ],
    "data": [
        "views/pos_order_views.xml",
        "views/pos_session_views.xml",
        "views/res_config_settings_views.xml",
        "views/pino_guest_stay_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_session_handover/static/src/overrides/**/*.js",
            "bs_pos_session_handover/static/src/overrides/**/*.xml",
            "bs_pos_session_handover/static/src/overrides/**/*.scss",
        ],
    },
    "installable": True,
    "application": False,
}
