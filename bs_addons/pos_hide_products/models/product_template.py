from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    hide_from_pos_self_order = fields.Boolean(
        string="Hide From POS & Self Order",
        help=(
            "Hide this product from direct selection in the POS and Self Order. "
            "The product remains available as a combo or modifier option and is "
            "still sent to kitchen displays and kitchen printers when ordered."
        ),
    )

    @api.model
    def _load_pos_data_fields(self, config):
        return [*super()._load_pos_data_fields(config), "hide_from_pos_self_order"]

    def write(self, vals):
        result = super().write(vals)
        if "hide_from_pos_self_order" in vals:
            for product in self.product_variant_ids:
                product._send_availability_status()
        return result
