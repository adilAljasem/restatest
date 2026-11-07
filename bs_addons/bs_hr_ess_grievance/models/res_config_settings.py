from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_appeal_days = fields.Integer(related='company_id.ess_appeal_days', readonly=False)
