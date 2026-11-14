{
    "name": "POS Kitchen Bilingual Product Names",
    "version": "19.0.1.0.0",
    "category": "Point of Sale",
    "summary": "Arabic and English product names on KOT receipts (the Resta kitchen screen shows both natively)",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "bs_pos_kitchen_bilingual/static/src/utils/bilingual_names.js",
            "bs_pos_kitchen_bilingual/static/src/overrides/pos_order.js",
            "bs_pos_kitchen_bilingual/static/src/overrides/pos_store.js",
            "bs_pos_kitchen_bilingual/static/src/xml/order_change_receipt.xml",
            "bs_pos_kitchen_bilingual/static/src/scss/kitchen_receipt.scss",
        ],
        "web.assets_unit_tests": [
            "bs_pos_kitchen_bilingual/static/tests/unit/**/*.test.js",
        ],
    },
    "installable": True,
    "application": False,
}
