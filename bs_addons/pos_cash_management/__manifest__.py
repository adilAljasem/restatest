# -*- coding: utf-8 -*-
{
    'name': "POS Cash Management",
    'summary': "Blind cash count, bank transfer on closing, server-side "
               "difference limit and cashier partner on cash differences",
    'description': """
POS Cash Management
===================

Merges and upgrades two modules into a single one for Odoo 19:

* Blind cash count (expected amounts hidden at closing)
* Mandatory actual cash count
* Automatic cash transfer to the bank once the register is closed
* Cashier work contact set as partner on cash difference journal items

Every feature is a separate option under
*Point of Sale > Configuration > Settings > Accounting*.
All labels follow the user interface language (English / Arabic).
""",
    'version': '19.0.2.0.0',
    'category': 'Sales/Point of Sale',
    'license': 'LGPL-3',
    'author': "Resta",
    "website": "https://resta.sa",
    'depends': ['point_of_sale', 'pos_hr'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_cash_management/static/src/closing_popup.js',
            'pos_cash_management/static/src/closing_popup.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
