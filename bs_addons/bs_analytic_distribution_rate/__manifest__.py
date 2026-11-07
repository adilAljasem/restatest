{
    "name": "BS Analytic Distribution Rate",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Refresh analytic distribution rates from analytic line amounts",
    "author": "Bunbastic",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "data/ir_cron_data.xml",
        "views/account_analytic_distribution_model_views.xml",
        "views/account_account_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
}
