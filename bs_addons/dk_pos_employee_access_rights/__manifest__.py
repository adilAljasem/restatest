# -*- coding: utf-8 -*-
{
    'name': 'POS Employee Access Rights',
    'version': '19.0.1.4.0',
    'author': "Resta",
    "website": "https://resta.sa",
    'category': 'Point of Sale',
    'summary': 'Configure granular POS permissions per employee and cashier.',
    'description': """
POS Employee Access Rights
==========================

Assign fine-grained Point of Sale permissions to each employee linked as a
POS cashier. Administrators control which buttons, menus, and actions are
visible or disabled directly from the employee form.

**Order management**
- New order, delete order, customer selection, actions, and payment
- Prevent removing / decreasing lines already sent to the kitchen

**Payment screen**
- Customer, invoice, validate, ship later, tip, cashbox, and payment methods

**Numpad**
- Show/hide numpad, quantity, price, discount, and plus/minus controls

**Navigation menu**
- Install app, orders, backend, cash in/out, close register, and debug tools

**Closing register**
- Payment totals, cash count, counted amounts, and differences

**Actions popup**
- General note, customer note, pricelist, refund, fiscal position, cancel order, product info
    """,
    'depends': ['pos_hr', 'pos_restaurant'],
    'data': [
        'views/hr_employee_views.xml',
        'views/pos_order_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'dk_pos_employee_access_rights/static/src/**/*',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
    'pre_init_hook': 'pre_init_hook',
}
