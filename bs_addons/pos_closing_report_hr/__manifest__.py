{
    "name": "POS Closing Report - Employees",
    "version": "19.0.1.0.0",
    "summary": "Choose the employees (POS employee login) allowed to print the business day report",
    "description": """
POS Closing Report - Employees
==============================
Installed automatically with pos_closing_report and pos_hr.
Adds "Employees Allowed to Print the Business Day Report" to the closing report
settings of each Point of Sale.
""",
    "category": "Sales/Point of Sale",
    "author": "Custom",
    "license": "LGPL-3",
    "depends": ["pos_closing_report", "pos_hr"],
    "data": ["views/pos_config_closing_views.xml"],
    "installable": True,
    "auto_install": True,
    "application": False,
}
