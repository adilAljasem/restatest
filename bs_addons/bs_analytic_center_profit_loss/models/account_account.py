from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = "account.account"

    bs_include_in_center_allocation = fields.Boolean(
        string="Include in Revenue Allocation",
        default=False,
        help=(
            "Include this income account in the Profit Center revenue total used "
            "as the allocation basis in the Profit and Loss by Center report."
        ),
    )
