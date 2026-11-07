from odoo import fields, models


class AccountAccount(models.Model):
    _inherit = "account.account"

    bs_include_in_analytic_rate = fields.Boolean(
        string="Include in Analytic Rate Calculation",
        help=(
            "Analytic lines linked to this financial account are included "
            "when dynamic analytic distribution rates are refreshed."
        ),
    )
