from odoo import fields, models


class PosConfig(models.Model):
    """To add a field in pos config"""
    _inherit = 'pos.config'

    analytic_account_id = fields.Many2one('account.analytic.account',
                                          string="Analytic account",
                                          help="Add Analytic account for the current session")

