{
    "name": "Hide Products from POS and Self Order",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "Hide products from direct POS selection while keeping combo and kitchen flows",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["pos_self_order"],
    "data": [
        "views/product_template_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_hide_products/static/src/shared/**/*",
            "pos_hide_products/static/src/pos/**/*",
        ],
        "pos_self_order.assets": [
            "pos_hide_products/static/src/shared/**/*",
            "pos_hide_products/static/src/self_order/**/*",
        ],
        "web.assets_unit_tests": [
            "pos_hide_products/static/src/shared/**/*",
            "pos_hide_products/static/tests/unit/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
