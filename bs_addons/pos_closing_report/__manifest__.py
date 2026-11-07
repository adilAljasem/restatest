{
    "name": "POS Closing Report",
    "version": "19.0.2.1.0",
    "summary": "Cashier closing report and branch manager business day report, printed with the kitchen ticket settings, with language, sold items, refunds and cancellations options",
    "description": """
POS Closing Report
==================
* Removes the "Print Report" button from the Point of Sale menu and closing popup.
* Automatically prints a closing receipt (employee, point of sale, expected, counted and differences
  for Cash and Bank payment methods) when the register is closed.
* Stores every closing report so managers can review them per employee, including reports
  generated retroactively for previously closed sessions.
* Settings per Point of Sale (Point of Sale > Configuration > Closing Report):
  report language at print time (Arabic, English, both, or the user language),
  sold items, refunds and cancellations, sales summary.
* Two reports:

  - Cashier report: one session, printed when the register is closed.
  - Branch manager report: the whole business day, every session, from the
    Business Day Start Hour (pos_business_day) to the same hour the next day.
    Printed from the POS menu by managers or from the backend.

* Printed with the kitchen ticket settings (pos_kitchen_ticket_custom):
  font size, printer width and crisp black printing.
* Fully translated (English / Arabic).
""",
    "category": "Sales/Point of Sale",
    "author": "Custom",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "pos_kitchen_ticket_custom", "pos_business_day"],
    "data": [
        "security/ir.model.access.csv",
        "report/closing_report.xml",
        "views/pos_closing_report_views.xml",
        "views/generate_wizard_views.xml",
        "views/pos_config_closing_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_closing_report/static/src/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
