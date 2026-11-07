from odoo import _, fields, models
from odoo.exceptions import AccessError


class PosConfig(models.Model):
    """Extension point for restaurant combo and modifier configuration."""

    _inherit = "pos.config"

    _modifier_setting_fields = {
        "enable_product_modifiers",
        "modifier_display_type",
        "show_modifier_images",
    }

    enable_product_modifiers = fields.Boolean(
        string="Enable Product Modifiers",
        default=True,
    )
    modifier_display_type = fields.Selection(
        selection=[
            ("grid", "Grid"),
            ("list", "List"),
        ],
        string="Modifier Display Type",
        required=True,
        default="grid",
    )
    show_modifier_images = fields.Boolean(
        string="Show Modifier Images",
        default=False,
    )

    def write(self, values):
        if self._modifier_setting_fields.intersection(values) and not (
            self.env.user.has_group("point_of_sale.group_pos_manager")
            or self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_("Only Point of Sale managers can change modifier settings."))
        return super().write(values)
