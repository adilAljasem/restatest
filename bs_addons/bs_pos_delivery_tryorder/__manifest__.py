# -*- coding: utf-8 -*-
{
    'name': 'BS POS Delivery TryOrder',
    'version': '19.0.1.3.0',
    'category': 'Point of Sale',
    'summary': 'TryOrder adapter for the BS POS Delivery framework',
    'author': 'TryOrder',
    'maintainer': 'TryOrder',
    'website': 'https://tryorder.com',
    'support': 'support@tryorder.com',
    'license': 'LGPL-3',
    'depends': [
        'bs_pos_delivery_core', 'pos_restaurant_combo_modifiers',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'images': ['static/description/icon.png'],
    'data': [
        'data/delivery_provider_data.xml',
        'views/delivery_connection_views.xml',
        'views/pos_order_views.xml',
    ],
}
