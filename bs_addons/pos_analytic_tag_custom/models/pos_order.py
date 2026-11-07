
from odoo import fields, models


class PosOrder(models.Model):
    """To add a field in pos order related to pos session"""
    _inherit = 'pos.order'

    pos_analytic_account_id = fields.Many2one(
        string='Pos Analytic Tag', readonly=True,
        related='session_id.pos_analytic_account_id',
        help="Add PoS analytic account for pos order")
