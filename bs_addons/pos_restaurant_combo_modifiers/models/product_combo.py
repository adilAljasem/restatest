from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductCombo(models.Model):
    """Extension point for the native Odoo combo model."""

    _inherit = "product.combo"

    qty_min = fields.Integer(
        string="Minimum Quantity",
        default=0,
        help="Minimum number of items that must be selected from this combo choice.",
    )
    display_type = fields.Selection(
        selection=[
            ("grid", "Grid"),
            ("list", "List"),
        ],
        string="Display Type",
        required=True,
        default="grid",
    )
    @api.constrains("qty_min", "qty_max", "combo_item_ids")
    def _check_quantity_rules(self):
        self._validate_quantity_rules()

    def _validate_quantity_rules(self, check_minimum=True):
        for combo in self:
            total_default_qty = sum(combo.combo_item_ids.mapped("default_qty"))
            if combo.qty_min < 0:
                raise ValidationError(_("Minimum Quantity cannot be negative."))
            if combo.qty_min > combo.qty_max:
                raise ValidationError(
                    _("Minimum Quantity cannot be greater than Maximum Quantity.")
                )
            if total_default_qty > combo.qty_max or (
                check_minimum and total_default_qty < combo.qty_min
            ):
                raise ValidationError(
                    _(
                        "Total Default Quantity must be between Minimum Quantity "
                        "and Maximum Quantity."
                    )
                )

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "qty_min", "display_type", "sequence",
        ]
