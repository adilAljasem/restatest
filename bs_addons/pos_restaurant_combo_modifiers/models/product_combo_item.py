from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductComboItem(models.Model):
    """Extension point for native Odoo combo choices."""

    _inherit = "product.combo.item"

    default_qty = fields.Integer(
        string="Default Quantity",
        default=0,
        help="Quantity selected automatically when the combo configurator opens.",
    )
    qty_max = fields.Integer(
        string="Maximum Item Quantity",
        default=0,
        help="Maximum quantity for this item. Set to 0 to use only the combo maximum.",
    )

    @api.constrains("default_qty", "qty_max")
    def _check_item_quantity_rules(self):
        for item in self:
            if item.default_qty < 0:
                raise ValidationError(_("Default Quantity cannot be negative."))
            if item.qty_max < 0:
                raise ValidationError(_("Maximum Item Quantity cannot be negative."))
            if item.qty_max and item.default_qty > item.qty_max:
                raise ValidationError(
                    _(
                        "Default Quantity cannot be greater than "
                        "Maximum Item Quantity."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        items.combo_id._validate_quantity_rules(check_minimum=False)
        return items

    def write(self, vals):
        previous_combos = self.combo_id
        result = super().write(vals)
        if "default_qty" in vals or "combo_id" in vals:
            (previous_combos | self.combo_id).exists()._validate_quantity_rules()
        return result

    def unlink(self):
        combos = self.combo_id
        result = super().unlink()
        combos.exists()._validate_quantity_rules()
        return result

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["default_qty", "qty_max"]
