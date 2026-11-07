from odoo import fields, models


class PosConfig(models.Model):
    """What The Dash stores on a POS: the cost center and the region it rolls up to.

    There is no per-shop token and no per-shop dashboard URL. The Dash has a
    single entry point that opens every branch, and narrowing happens inside
    the page.
    """
    _inherit = 'pos.config'

    dash_region_id = fields.Many2one(
        'the.dash.region',
        string='Region (The Dash)',
        help='Region this point of sale rolls up to in The Dash.',
    )
    dash_cost_center_id = fields.Many2one(
        'account.analytic.account',
        string='Cost Center (The Dash)',
        help='Analytic account used to roll this point of sale up into a '
             'cost center in The Dash reports.',
    )
