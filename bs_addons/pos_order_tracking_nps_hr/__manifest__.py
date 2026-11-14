{
    'name': 'POS Order Tracking & NPS - Employees',
    'version': '19.0.1.0.0',
    'category': 'Sales/Point of Sale',
    'summary': 'Limit the order tracking QR to selected employees when the POS uses employee login',
    'author': "Resta",
    'website': "https://resta.sa",
    'license': 'OPL-1',
    'depends': ['pos_order_tracking_nps', 'pos_hr'],
    'auto_install': True,
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
}
