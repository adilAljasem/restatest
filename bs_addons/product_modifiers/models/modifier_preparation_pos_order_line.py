from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    modifier_parent_uuid = fields.Char(
        string="Modifier Parent UUID",
        copy=False,
        index=True,
        help="Stable UUID of the parent POS order line for a modifier line.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)
        if "modifier_parent_uuid" not in fields_to_load:
            fields_to_load.append("modifier_parent_uuid")
        return fields_to_load
