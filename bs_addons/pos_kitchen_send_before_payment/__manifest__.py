{
    'name': 'POS Send to Kitchen Before Payment (No Restaurant Mode)',
    'version': '19.0.1.2.0',
    'category': 'Sales/Point of Sale',
    'summary': 'Send orders to kitchen printers / preparation display before payment '
               'without enabling Bar/Restaurant mode (drive-thru, counter service).',
    'description': """
Adds a "Send" button next to "Payment" on the POS product screen when the
Point of Sale is NOT in Bar/Restaurant mode. The button sends only the new
or changed lines to the preparation printers / preparation display, so the
kitchen can start before the customer pays (drive-thru, counter service).

Enable it from: Point of Sale > Configuration > Settings > Preparation >
"Send to Kitchen Before Payment".
""",
    'depends': ['point_of_sale'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_kitchen_send_before_payment/static/src/**/*',
        ],
        'web.assets_tests': [
            'pos_kitchen_send_before_payment/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'author': 'Custom',
    'license': 'LGPL-3',
}
