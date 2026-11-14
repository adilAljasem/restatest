# -*- coding: utf-8 -*-
{
    "name": "Pino POS Products By Category Only",
    "author": "Resta",
    "website": "https://resta.sa",
    "version": "19.0.1.0.3",
    "category": "Point of Sale",
    "license": "LGPL-3",
    "summary": "Hide POS products until a product category is selected.",
    "description": """
Pino POS Products By Category Only
==================================

In the Point of Sale product screen, no product is listed until the cashier
picks a category. Searching (including "Search more") stays scoped to the
selected category instead of resetting it, which is the Odoo 19 default.
""",
    "depends": ["point_of_sale"],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_products_by_category_only/static/src/overrides/product_screen.xml",
            "bs_pos_products_by_category_only/static/src/overrides/product_screen.js",
        ],
        "web.assets_unit_tests": [
            "bs_pos_products_by_category_only/static/tests/unit/**/*.test.js",
        ],
    },
    "installable": True,
    "application": False,
}
