from odoo import api, fields, models


class PosPrepLine(models.Model):
    _inherit = "pos.prep.line"

    modifier_snapshot_initialized = fields.Boolean(
        string="Modifier Snapshot Initialized",
        default=False,
        copy=False,
    )
    modifier_snapshot_data = fields.Json(
        string="Modifier Snapshot",
        default=list,
        copy=False,
    )
    modifier_acknowledged_data = fields.Json(
        string="Acknowledged Modifier Snapshot",
        default=list,
        copy=False,
    )
    modifier_display_data = fields.Json(
        string="Kitchen Modifiers",
        default=list,
        copy=False,
    )

    @api.model
    def _load_pos_preparation_data_fields(self):
        loaded_fields = super()._load_pos_preparation_data_fields()
        # Preparation Display intentionally uses an empty field list to mean
        # "load every field".  Replacing that sentinel with only our fields
        # drops standard relations such as prep_order_id and product_id, which
        # leaves pos.prep.state records disconnected in the OWL data store.
        if not loaded_fields:
            return loaded_fields
        return loaded_fields + [
            "modifier_snapshot_initialized",
            "modifier_snapshot_data",
            "modifier_display_data",
        ]

    def _acknowledge_modifier_changes(self):
        for line in self.filtered("modifier_snapshot_initialized"):
            acknowledged_data = [
                {key: value for key, value in entry.items() if key != "change_status"}
                for entry in (line.modifier_snapshot_data or [])
            ]
            if line.modifier_display_data != acknowledged_data:
                line.write({
                    "modifier_acknowledged_data": acknowledged_data,
                    "modifier_display_data": acknowledged_data,
                })
