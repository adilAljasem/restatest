from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_enable_product_modifiers = fields.Boolean(
        related="pos_config_id.enable_product_modifiers",
        readonly=False,
    )
    pos_modifier_display_type = fields.Selection(
        related="pos_config_id.modifier_display_type",
        readonly=False,
    )
    pos_show_modifier_images = fields.Boolean(
        related="pos_config_id.show_modifier_images",
        readonly=False,
    )
