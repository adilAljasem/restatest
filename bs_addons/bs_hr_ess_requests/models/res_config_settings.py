from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_leave_max_concurrent = fields.Integer(related='company_id.ess_leave_max_concurrent', readonly=False)
