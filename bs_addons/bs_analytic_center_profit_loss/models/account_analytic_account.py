from odoo import fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = "account.analytic.account"

    bs_center_type = fields.Selection(
        selection=[
            ("cost", "Cost Center"),
            ("profit", "Profit Center"),
            ("revenue", "Revenue Center"),
        ],
        string="Center Type",
        index=True,
    )

