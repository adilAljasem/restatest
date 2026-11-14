{
    "name": "Resta - المحاسبة والتقارير المالية",
    "summary": "Full accounting on Odoo Community: financial statements, cash flow, VAT return, bank "
               "reconciliation and statement import, fixed assets, budgets, deferred entries, payment "
               "follow-up, period locking",
    "description": """
المحاسبة الكاملة على Odoo Community: القوائم المالية والتدفقات النقدية وإقرار الضريبة، التسوية البنكية
واستيراد كشوف البنك، الأصول الثابتة، الموازنات، المصروفات والإيرادات المؤجلة، ومتابعة التحصيل.
التفاصيل في README.md.
""",
    "version": "19.0.1.1.0",
    "category": "Accounting/Accounting",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "OPL-1",
    "depends": ["account", "mail"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "report/partner_statement.xml",
        "views/views.xml",
        "views/accounting_extra.xml",
        "views/menus.xml",
        "data/cron.xml",
        "data/followup_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "resta_account/static/src/scss/*.scss",
            "resta_account/static/src/report/*",
            "resta_account/static/src/bank_rec/*",
            "resta_account/static/src/followup/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
