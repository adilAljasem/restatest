{
    'name': 'POS Deferred Invoice PDF',
    'version': '19.0.1.0.0',
    'category': 'Sales/Point of Sale',
    'summary': 'Generate POS invoice PDFs after payment using the account send cron',
    'author': 'Custom Development',
    'license': 'LGPL-3',
    'depends': ['pos_config_controls', 'l10n_sa_edi'],
    'data': ['views/res_config_settings_views.xml'],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_deferred_invoice_pdf/static/src/app/deferred_invoice_pdf.js',
        ],
        'web.assets_unit_tests': [
            'pos_deferred_invoice_pdf/static/tests/unit/**/*',
        ],
        'web.assets_tests': [
            'pos_deferred_invoice_pdf/static/tests/tours/**/*',
        ],
        'point_of_sale.assets_debug': [
            'pos_deferred_invoice_pdf/static/tests/tours/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
