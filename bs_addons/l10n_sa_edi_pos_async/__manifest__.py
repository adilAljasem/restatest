{
    'name': 'Saudi Arabia - POS Async ZATCA Reporting',
    'summary': 'Instant POS checkout: sign simplified invoices locally, report them to ZATCA in the background',
    'description': """
إرسال فواتير نقطة البيع المبسطة إلى زاتكا في الخلفية
=====================================================
In standard Odoo 19 every POS payment waits for the ZATCA reporting API
(typically 3-9 seconds). Simplified (B2C) invoices only need to be *reported*
within 24 hours, so this module:

* signs the invoice, builds the Phase-2 QR code and chains it (ICV/PIH) at checkout,
  without any network call, so the receipt prints immediately and is legal;
* lets the standard EDI cron report the invoices to ZATCA in chain order;
* keeps standard (B2B) invoices on the normal synchronous clearance flow.

Can be enabled/disabled per Point of Sale (Settings > Point of Sale > Accounting).
""",
    'version': '19.0.1.0.2',
    'category': 'Accounting/Localizations/Point of Sale',
    'author': 'Custom',
    'license': 'LGPL-3',
    'countries': ['sa'],
    'depends': ['l10n_sa_edi_pos'],
    'data': [
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
}
