{
    "name": "Employee Promotion Limits",
    "version": "19.0.1.2.0",
    "category": "Sales/Point of Sale",
    "summary": "Limit employee-only POS promotions per employee and period",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["hr", "loyalty", "pos_loyalty"],
    "data": [
        "security/employee_promotion_usage_security.xml",
        "security/ir.model.access.csv",
        "views/loyalty_program_views.xml",
        "views/employee_promotion_usage_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            # Odoo 19's loyalty list XML uses this JS view.  Include the
            # provider explicitly so it is present even when the dependency's
            # wildcard assets were not rebuilt after installing this module.
            "loyalty/static/src/js/loyalty_list_view.js",
            "loyalty/static/src/xml/loyalty_templates.xml",
        ],
        "point_of_sale._assets_pos": [
            "employee_loyalty_limit/static/src/js/employee_promotion_limit.js",
        ],
    },
    "installable": True,
    "application": False,
}
