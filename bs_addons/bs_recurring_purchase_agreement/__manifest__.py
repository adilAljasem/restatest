{
    "name": "BS Recurring Purchase Agreement",
    "summary": "Create scheduled purchase orders from blanket agreements",
    "version": "19.0.1.0.0",
    "category": "Purchases/Purchases",
    "author": "Resta",
    "website": "https://resta.sa",
    "license": "LGPL-3",
    "depends": ["stock_request"],
    "data": [
        "data/ir_cron_data.xml",
        "views/res_config_settings_views.xml",
        "views/purchase_requisition_views.xml",
    ],
    "installable": True,
    "application": False,
}
