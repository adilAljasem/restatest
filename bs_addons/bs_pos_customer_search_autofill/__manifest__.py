{
    "name": "POS Customer Search Autofill",
    "version": "19.0.1.0.0",
    "category": "Point of Sale",
    "summary": "Prefill new POS customers from the customer search term",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_customer_search_autofill/static/src/app/partner_search_autofill.js",
        ],
        "web.assets_unit_tests": [
            "bs_pos_customer_search_autofill/static/tests/unit/**/*.test.js",
        ],
    },
    "installable": True,
    "application": False,
}
