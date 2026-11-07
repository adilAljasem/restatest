"""Keep Blend's legacy POS fields in sync with active store mappings."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DeliveryStoreBinding(models.Model):
    _inherit = "bs.delivery.store.binding"

    blend_available_preset_ids = fields.Many2many(
        related="pos_config_id.available_preset_ids",
        string="Available POS Presets",
    )
    blend_delivery_preset_id = fields.Many2one(
        "pos.preset", string="Delivery Preset", ondelete="set null",
        help=("POS preset for incoming Blend delivery orders. "
              "Existing order prices and taxes are kept."),
    )
    blend_pickup_preset_id = fields.Many2one(
        "pos.preset", string="Pickup Preset", ondelete="set null",
        help=("POS preset for incoming Blend pickup orders. "
              "Existing order prices and taxes are kept."),
    )
    blend_dine_in_preset_id = fields.Many2one(
        "pos.preset", string="Dine In Preset", ondelete="set null",
        help=("POS preset for incoming Blend dine-in orders. "
              "Existing order prices and taxes are kept."),
    )

    @api.constrains(
        "pos_config_id", "blend_delivery_preset_id", "blend_pickup_preset_id",
        "blend_dine_in_preset_id",
    )
    def _check_blend_presets(self):
        for store in self.filtered(lambda record: record.provider_id.code == "blend"):
            for preset in (
                store.blend_delivery_preset_id,
                store.blend_pickup_preset_id,
                store.blend_dine_in_preset_id,
            ):
                store._validate_blend_preset(preset)

    def _validate_blend_preset(self, preset):
        self.ensure_one()
        if not preset:
            return
        config = self.pos_config_id
        if (not config.use_presets
                or preset not in config.available_preset_ids
                or preset.is_return):
            raise ValidationError(_(
                "Preset '%(preset)s' must be an available, non-return preset on "
                "POS branch '%(branch)s', with Use Presets enabled.",
                preset=preset.display_name,
                branch=config.display_name,
            ))

    @api.constrains("connection_id", "pos_config_id", "active")
    def _check_unique_active_blend_store(self):
        for store in self.filtered(
            lambda record: record.active and record.provider_id.code == "blend"
        ):
            if self.search_count([
                ("id", "!=", store.id),
                ("connection_id", "=", store.connection_id.id),
                ("pos_config_id", "=", store.pos_config_id.id),
                ("active", "=", True),
            ]):
                raise ValidationError(_("A Blend branch can have only one active store mapping."))

    @api.model_create_multi
    def create(self, vals_list):
        stores = super().create(vals_list)
        stores._sync_blend_branch_fields(stores.mapped("pos_config_id"))
        return stores

    def write(self, vals):
        configs = self.mapped("pos_config_id")
        result = super().write(vals)
        self._sync_blend_branch_fields(configs | self.mapped("pos_config_id"))
        return result

    def unlink(self):
        configs = self.mapped("pos_config_id")
        result = super().unlink()
        self._sync_blend_branch_fields(configs)
        return result

    def _sync_blend_branch_fields(self, configs):
        Store = self.sudo().with_context(active_test=False)
        for config in configs.sudo():
            active_store = Store.search([
                ("pos_config_id", "=", config.id),
                ("provider_id.code", "=", "blend"),
                ("active", "=", True),
            ], limit=1)
            if active_store:
                values = {
                    "blend_branch_id": active_store.remote_store_id,
                    "blend_brand_id": active_store.remote_vendor_id or False,
                    "blend_enabled": True,
                }
                values = {key: value for key, value in values.items() if config[key] != value}
            else:
                values = {"blend_enabled": False} if config.blend_enabled else {}
            if values:
                config.with_context(bs_blend_store_sync=True).write(values)
