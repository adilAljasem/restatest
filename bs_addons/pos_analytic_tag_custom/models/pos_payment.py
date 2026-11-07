
from odoo import fields, models


class PosPayment(models.Model):
    """To add a field in pos payment related to pos session"""
    _inherit = 'pos.payment'

    pos_analytic_account_id = fields.Many2one(
        related='session_id.pos_analytic_account_id',
        string="PoS Analytic Account", help="PoS Analytic account in "
                                            "pos payment")
