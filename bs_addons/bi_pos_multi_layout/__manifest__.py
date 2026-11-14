# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.
{
    "name": "Multi Layout in POS",
    "version": "19.0.0.0.0",
    "category": "Point of Sale",
    'summary': 'POS Screen layout POS Button Layout POS multi Layout POS Screen multi Layout POS screen and button layout POS Multi Column Option pos multiple layout option pos multi screen layout pos multi button layout pos screen button layout multiple layout on pos',
    "description": """

        Multi Layout in POS in odoo,
        POS Multi Layout in odoo,
        Button Layout in odoo,
        Screen Layout for Button in odoo,
        Multi Column Option in odoo,

    """,
    "author": "Resta",
    "website": "https://resta.sa",
    "depends": ['base', 'sale', 'point_of_sale'],
    "data": [
        'views/custom_pos_view.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'bi_pos_multi_layout/static/src/app/screens/product_screen/product_screen.xml',
            'bi_pos_multi_layout/static/src/app/screens/product_screen/product_screen.js',
            'bi_pos_multi_layout/static/src/css/custom_product_screen.css',
        ],
    },
    'license': 'OPL-1',
    "auto_install": False,
    "installable": True,
    "images": ['static/description/Banner.gif'],
}
