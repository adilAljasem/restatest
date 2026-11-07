from odoo import fields, models


class TheDashRegion(models.Model):
    """Region a point of sale belongs to (Riyadh, Jeddah, Eastern...).

    Used to roll branches up in The Dash: sales, collections, closing
    differences and the multi-layer report can be grouped by region.
    """
    _name = 'the.dash.region'
    _description = 'The Dash region'
    _order = 'sequence, name'

    name = fields.Char(string='Region', required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    config_ids = fields.One2many('pos.config', 'dash_region_id', string='Points of Sale')
