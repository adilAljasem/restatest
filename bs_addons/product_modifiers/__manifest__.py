# -*- coding: utf-8 -*-
# Powered by Kanak Infosystems LLP.
# © 2020 Kanak Infosystems LLP. (<https://www.kanakinfosystems.com>).
{
    'name': "Basic Solution - POS Product Modifiers",
    'version': '19.0.1.0.5',
    'summary': 'POS product modifiers, add-ons and sides for restaurants',
    'license': 'OPL-1',
    'author': "Resta",
    'website': "https://resta.sa",
    'category': 'Point of Sale',
    'depends': ['sale', 'point_of_sale', 'pos_restaurant', 'resta_kds', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/modifires_view.xml',
        'views/menu_items.xml',
        'views/pos_category_views.xml',
        'views/product_attribute_views.xml',
        'views/product_variant_views.xml',
        'views/product_template_views.xml',

    ],
    'assets': {
        'point_of_sale._assets_pos': [
            '/product_modifiers/static/src/css/style.css',
            '/product_modifiers/static/src/js/**/*',
            '/product_modifiers/static/src/overrides/**/*',
            '/product_modifiers/static/src/utils/**/*',
            '/product_modifiers/static/src/xml/**/*',
            '/product_modifiers/static/src/modifier_preparation_routing/kitchen_print.js',
            '/product_modifiers/static/src/modifier_preparation_routing/kitchen_receipt.xml',
            '/product_modifiers/static/src/modifier_preparation_routing/pos_order_line.js',
        ],
        'web.assets_unit_tests': [
            '/product_modifiers/static/tests/unit/**/*.test.js',
        ],
        'web.assets_tests': [
            '/product_modifiers/static/tests/tours/**/*.js',
        ],
    },
    'installable': True,
    'application': True,
}
