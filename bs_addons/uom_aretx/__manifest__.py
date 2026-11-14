# -*- coding: utf-8 -*-
{
    'name': 'Allow Product UoM Change',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Allow changing product Unit of Measure even when used in posted Journal Entries',
    'description': """
        By default, Odoo blocks changing a product's Unit of Measure (UoM) if the product
        is already used in posted Journal Entries. This module removes that restriction so
        you can change the UoM at any time, even when the product is used in invoices,
        moves, or elsewhere. Use with care: historical documents keep their original UoM
        on the lines; only the product's default UoM is updated for future use.
    """,
    'author': "Resta",
    'website': "https://resta.sa",
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [],

    'images': [
        'static/description/images/module-banner_19.png',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
