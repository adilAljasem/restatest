{
    'name': "PoS Analytic Tag (DIS)",
    'version': '19.0.1.0.1',
    'category': 'Point of Sale',
    'author': "Resta",
    "website": "https://resta.sa",
    'depends': ['point_of_sale', 'analytic', 'account'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/pos_config_views.xml',
        'views/pos_session_views.xml',
        'views/pos_order_views.xml',
        'views/pos_payment_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_analytic_tag_custom/static/src/js/PosSession.js',
        ],
    },
     'images': [
        'static/description/banner.gif',
        'static/description/icon.png',
        ],
    'license': 'AGPL-3',
    'installable': True,
    'auto_install': False,
    'application': False,
}
