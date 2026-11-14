{
    'name': 'POS Kitchen Ticket Design',
    'version': '19.0.1.4.0',
    'category': 'Point of Sale',
    'summary': 'Configurable kitchen / preparation ticket: order reference, order number, '
               'type, guests, device, date and time, delivery platform, bilingual '
               '(Arabic / English) item names with options, modifiers, combo items and notes.',
    'description': """
POS Kitchen Ticket Design
=========================
Replaces the design of the kitchen (preparation printer) ticket of Odoo 19.
Every part of the ticket can be shown or hidden per Point of Sale in
Point of Sale > Configuration > Kitchen Ticket.
""",
    'author': "Resta",
    "website": "https://resta.sa",
    'license': 'LGPL-3',
    'depends': ['point_of_sale'],
    'data': [
        'views/pos_config_kitchen_ticket_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_kitchen_ticket_custom/static/src/scss/kitchen_ticket.scss',
            'pos_kitchen_ticket_custom/static/src/xml/kitchen_ticket.xml',
            'pos_kitchen_ticket_custom/static/src/js/kitchen_modifiers.js',
            'pos_kitchen_ticket_custom/static/src/js/kitchen_ticket.js',
            'pos_kitchen_ticket_custom/static/src/js/kitchen_print.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
