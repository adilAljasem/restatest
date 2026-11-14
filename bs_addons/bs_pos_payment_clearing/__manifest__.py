{
    "name": "POS Payment Clearing",
    "summary": "Cash and bank clearing accounts with centralized POS journal entries",

    'author': "Resta",
    "website": "https://resta.sa",

    "version": "19.0.1.4.0",
    "category": "Sales/Point of Sale",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "security/collection_security.xml",
        "security/ir.model.access.csv",
        "views/pos_payment_method_views.xml",
        "views/account_move_views.xml",
        "views/collection_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_payment_clearing/static/src/app/**/*",
        ],
        "web.assets_unit_tests": [
            "bs_pos_payment_clearing/static/tests/unit/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
