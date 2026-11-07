from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_kitchen_send_before_payment = fields.Boolean(
        related='pos_config_id.kitchen_send_before_payment', readonly=False)
