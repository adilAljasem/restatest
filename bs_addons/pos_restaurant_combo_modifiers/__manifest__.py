{
    "name": "POS Restaurant Combo & Modifiers",
    "summary": "Restaurant combos, modifiers, and kitchen integration for Point of Sale",
    "version": "19.0.2.1.1",
    "category": "Sales/Point of Sale",
    "author": "Mohamed Elmalky",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
        "pos_restaurant",
        "pos_restaurant_preparation_display",
    ],
    "data": [
        "security/pos_modifier_security.xml",
        "security/ir.model.access.csv",
        "views/pos_modifier_group_views.xml",
        "views/product_template_views.xml",
        "views/product_combo_views.xml",
        "views/pos_order_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "pos_restaurant_combo_modifiers/static/src/backend/**/*",
        ],
        "point_of_sale._assets_pos": [
            "pos_restaurant_combo_modifiers/static/src/app/**/*",
        ],
        "pos_preparation_display.assets": [
            "pos_restaurant_combo_modifiers/static/src/kitchen/**/*",
        ],
        "web.assets_unit_tests": [
            "pos_restaurant_combo_modifiers/static/tests/unit/**/*",
        ],
        "web.assets_tests": [
            "pos_restaurant_combo_modifiers/static/tests/tours/**/*",
        ],
        "point_of_sale.assets_debug": [
            "pos_restaurant_combo_modifiers/static/tests/tours/**/*",
        ],
        "pos_preparation_display.assets_tour_tests": [
            "pos_restaurant_combo_modifiers/static/tests/kitchen_tours/**/*",
        ],
    },
    "application": True,
    "installable": True,
}
