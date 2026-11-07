from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_tracking_employee_ids = fields.Many2many(related='pos_config_id.tracking_employee_ids', readonly=False)
