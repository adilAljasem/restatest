# -*- coding: utf-8 -*-
{
    'name': 'POS Call Center Dispatch',
    'version': '19.0.2.0.2',
    'category': 'Sales/Point of Sale',
    'summary': 'Call center agents take orders in a POS screen and dispatch them to an open branch: '
               'kitchen tickets / preparation display are sent automatically, the cashier only collects payment.',
    'description': """
POS Call Center Dispatch
========================
* The call center works in a regular Point of Sale screen (combos, attributes,
  notes, presets, customers... everything the POS supports).
* Instead of "Payment", the agent clicks "Send to Branch" and picks a branch
  that currently has an OPEN session, within the call center scope / zone.
* The order is moved to the branch session, appears instantly on the branch POS
  and is sent automatically to the kitchen printers / preparation display
  (the same mechanism as the native "Order" button).
* The cashier only collects the payment when the customer arrives.
* Tracking of every call center order (dispatched, in kitchen, paid, cancelled).
    """,
    'author': 'Custom',
    'license': 'LGPL-3',
    'depends': ['point_of_sale', 'pos_restaurant', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'views/pos_call_center_views.xml',
        'views/pos_order_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_call_center/static/src/app/**/*',
        ],
    },
    'installable': True,
    'application': True,
}
