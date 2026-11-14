{
    "name": "POS Kitchen Flow Monitor",
    "version": "19.0.2.0.0",
    "category": "Point of Sale",
    "summary": "Trace and control POS kitchen delivery flow",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": [
        "pos_restaurant",
        "resta_kds",
    ],
    "data": [
        "security/kitchen_flow_security.xml",
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/kitchen_flow_log_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_kitchen_flow/static/src/utils/kitchen_flow_trace.js",
            "bs_pos_kitchen_flow/static/src/overrides/pos_order.js",
            "bs_pos_kitchen_flow/static/src/overrides/pos_store.js",
        ],
        "web.assets_unit_tests": [
            "bs_pos_kitchen_flow/static/tests/unit/**/*.test.js",
        ],
    },
    "installable": True,
    "application": False,
}
