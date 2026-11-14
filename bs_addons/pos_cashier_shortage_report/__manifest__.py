{
    "name": "POS Cashier Shortage Report",
    "summary": "Track cash counted differences per POS session and cash journal",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "license": "LGPL-3",
    "author": "Resta",
    "website": "https://resta.sa",
    "depends": ["point_of_sale", "pos_hr"],
    "data": [
        "security/pos_cashier_shortage_security.xml",
        "security/ir.model.access.csv",
        "views/pos_cashier_shortage_report_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_cashier_shortage_report/static/src/app/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
