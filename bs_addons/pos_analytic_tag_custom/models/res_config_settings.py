
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """To add a field in pos configuration settings"""
    _inherit = 'res.config.settings'

    pos_analytic_account_id = fields.Many2one(
        'account.analytic.account',
        related='pos_config_id.analytic_account_id',
        string='Analytic Account(PoS)', readonly=False,
        help="Add analytic account for the pos session")
