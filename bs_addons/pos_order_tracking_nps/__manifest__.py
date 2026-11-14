{
    'name': 'POS Order Tracking & NPS',
    'version': '19.0.2.0.0',
    'category': 'Sales/Point of Sale',
    'summary': 'QR on the receipt opens a live order-status page: ready alert with sound, then an NPS survey saved in Odoo',
    'description': """
Customer order tracking for restaurants (digital pager)
=======================================================
* A QR code is printed on the customer receipt.
* Scanning it opens a mobile page (no app, no login) showing the order number and its live status.
* Status follows the Resta kitchen screen (resta_kds) chosen in the POS settings:
  when the cook taps «Done» (or the order reaches the screen's "Ready" stage) the customer
  gets a notification, sound and vibration.
* Background (lock-screen) web-push notifications where the browser supports them.
* N minutes after the order is ready (30 by default) the customer is asked the NPS question (0-10)
  and the answer is stored in Odoo with NPS reporting.
""",
    'author': "Resta",
    'website': "https://resta.sa",
    'license': 'OPL-1',
    'depends': ['point_of_sale', 'resta_kds', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/pos_order_nps_views.xml',
        'views/pos_order_views.xml',
        'views/res_config_settings_views.xml',
        'views/tracking_templates.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_order_tracking_nps/static/src/pos/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
