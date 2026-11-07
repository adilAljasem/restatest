from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ess_show_birthdays = fields.Boolean(related='company_id.ess_show_birthdays', readonly=False)
