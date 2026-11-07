from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..services.exceptions import BlendAPIError
from .test_order_accounting import BlendAccountingCommon


@tagged("post_install", "-at_install")
class TestBlendOrderPreset(BlendAccountingCommon):
    def test_incoming_order_uses_type_mapping_not_names_or_cashier_default(self):
        delivery = self.config.blend_default_preset_id
        dine_in = self.env["pos.preset"].create({"name": "Dine In"})
        pickup = self.env["pos.preset"].create({"name": "Take Away"})
        self.config.write({
            "default_preset_id": dine_in.id,
            "available_preset_ids": [Command.link(dine_in.id), Command.link(pickup.id)],
            "blend_pickup_preset_id": pickup.id, "blend_dinein_preset_id": dine_in.id,
        })
        delivery.name = "Renamed delivery preset"
        for order_type, preset in (("delivery", delivery), ("pickup", pickup), ("DINE-IN", dine_in)):
            payload = self._payload()
            payload["order_type"] = order_type
            order = self._create(payload)
            self.assertEqual(order.preset_id, preset)
            self.assertEqual(order.blend_order_type, order_type)
        self.assertEqual(self.config.default_preset_id, dine_in)

    def test_unmapped_and_unknown_types_do_not_use_delivery(self):
        for order_type in ("pickup", "dinein", "courier"):
            payload = self._payload()
            payload["order_type"] = order_type
            with self.assertRaises(BlendAPIError):
                self.service.create_order(payload)

    def test_mapping_is_per_branch(self):
        other = self.config.copy({"name": "Other preset branch"})
        preset = self.env["pos.preset"].create({"name": "Other delivery"})
        other.write({"available_preset_ids": [Command.link(preset.id)], "blend_default_preset_id": preset.id})
        self.assertEqual(other._blend_order_preset("delivery"), preset)
        self.assertNotEqual(self.config._blend_order_preset("delivery"), preset)

    def test_missing_mapping_rejects_new_order_but_keeps_existing_retry_idempotent(self):
        payload = self._payload()
        order = self._create(payload)
        self.config.blend_default_preset_id = False
        self.assertEqual(self._create(payload), order)
        with self.assertRaises(BlendAPIError):
            self.service.create_order(self._payload())

    def test_unavailable_or_return_preset_is_rejected(self):
        other = self.env["pos.preset"].create({"name": "Unconfigured"})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.config.blend_default_preset_id = other
        other.is_return = True
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.config.write({"available_preset_ids": [Command.link(other.id)], "blend_default_preset_id": other.id})

    def test_disabling_presets_with_mapping_is_rejected(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.config.use_presets = False
