{
    "name": "Exclusive Customer Promotion Limits",
    "version": "19.0.1.2.0",
    "category": "Sales/Point of Sale",
    "summary": "Limit exclusive-only POS promotions per exclusive and period",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["loyalty", "pos_loyalty"],
    "data": [
        "security/employee_promotion_usage_security.xml",
        "security/ir.model.access.csv",
        "views/loyalty_program_views.xml",
        "views/employee_promotion_usage_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "exclusive_customer_discount/static/src/js/employee_promotion_limit.js",
        ],
    },
    "installable": True,
    "application": False,
}
