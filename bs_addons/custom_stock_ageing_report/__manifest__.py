{
    "name": "Custom Stock Ageing Report",
    "summary": "Historical FIFO stock ageing by warehouse with PDF and Excel output",
    "version": "19.0.1.1.1",
    "category": "Inventory/Inventory",
    "author": "Custom Development",
    "website": "https://www.odoo.com",
    "license": "LGPL-3",
    "depends": ["stock_account"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/ir.model.access.csv",
        "report/stock_ageing_report.xml",
        "report/stock_ageing_templates.xml",
        "views/stock_ageing_wizard_views.xml",
    ],
    "application": False,
    "installable": True,
}
