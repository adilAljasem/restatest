from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestModifierPreparationRouting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parent_category, cls.modifier_category = cls.env[
            "pos.category"
        ].create([
            {"name": "Parent Kitchen"},
            {"name": "Modifier Own Kitchen"},
        ])
        cls.parent_product, cls.modifier_product, cls.normal_product = cls.env[
            "product.product"
        ].create([
            {
                "name": "Parent Product",
                "available_in_pos": True,
                "pos_categ_ids": [Command.set(cls.parent_category.ids)],
            },
            {
                "name": "Modifier Product",
                "available_in_pos": True,
                "pos_categ_ids": [Command.set(cls.modifier_category.ids)],
            },
            {
                "name": "Normal Product",
                "available_in_pos": True,
                "pos_categ_ids": [Command.set(cls.modifier_category.ids)],
            },
        ])
        cls.config = cls.env["pos.config"].create({"name": "Routing Test POS"})
        cls.session = cls.env["pos.session"].create({
            "config_id": cls.config.id,
        })
        cls.parent_display = cls.env["pos.prep.display"].create({
            "name": "Parent Display",
            "pos_config_ids": [Command.set(cls.config.ids)],
            "category_ids": [Command.set(cls.parent_category.ids)],
        })
        cls.modifier_display = cls.env["pos.prep.display"].create({
            "name": "Modifier Display",
            "pos_config_ids": [Command.set(cls.config.ids)],
            "category_ids": [Command.set(cls.modifier_category.ids)],
        })
        cls.order = cls.env["pos.order"].create({
            "session_id": cls.session.id,
            "config_id": cls.config.id,
            "amount_tax": 0,
            "amount_total": 0,
            "amount_paid": 0,
            "amount_return": 0,
        })
        cls.parent_order_line = cls.env["pos.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.parent_product.id,
            "qty": 1,
            "price_unit": 10,
            "price_subtotal": 10,
            "price_subtotal_incl": 10,
        })
        cls.modifier_order_line = cls.env["pos.order.line"].create({
            "order_id": cls.order.id,
            "product_id": cls.modifier_product.id,
            "qty": 1,
            "price_unit": 2,
            "price_subtotal": 2,
            "price_subtotal_incl": 2,
            "parent_line_id": str(cls.parent_order_line.id),
            "modifier_parent_uuid": cls.parent_order_line.uuid,
        })
        cls.prep_order = cls.env["pos.prep.order"].create({
            "pos_order_id": cls.order.id,
        })

    def test_modifier_state_uses_parent_display_only(self):
        modifier_prep_line = self.env["pos.prep.line"].create({
            "prep_order_id": self.prep_order.id,
            "pos_order_line_id": self.modifier_order_line.id,
            "pos_order_line_uuid": self.modifier_order_line.uuid,
            "product_id": self.modifier_product.id,
            "quantity": 1,
        })

        self.assertTrue(modifier_prep_line.routes_by_parent)
        self.assertEqual(
            modifier_prep_line.modifier_parent_uuid,
            self.parent_order_line.uuid,
        )
        self.assertEqual(
            modifier_prep_line.routing_category_ids,
            self.parent_category,
        )

        states = self.env["pos.prep.state"].search([
            ("prep_line_id", "=", modifier_prep_line.id),
        ])
        self.assertEqual(states.stage_id.prep_display_id, self.parent_display)

        rejected_state = self.env["pos.prep.state"].create({
            "prep_line_id": modifier_prep_line.id,
            "stage_id": self.modifier_display.stage_ids[0].id,
        })
        self.assertFalse(rejected_state)

    def test_normal_line_keeps_standard_state_behavior(self):
        normal_order_line = self.env["pos.order.line"].create({
            "order_id": self.order.id,
            "product_id": self.normal_product.id,
            "qty": 1,
            "price_unit": 4,
            "price_subtotal": 4,
            "price_subtotal_incl": 4,
        })
        normal_prep_line = self.env["pos.prep.line"].create({
            "prep_order_id": self.prep_order.id,
            "pos_order_line_id": normal_order_line.id,
            "pos_order_line_uuid": normal_order_line.uuid,
            "product_id": self.normal_product.id,
            "quantity": 1,
        })
        state = self.env["pos.prep.state"].create({
            "prep_line_id": normal_prep_line.id,
            "stage_id": self.modifier_display.stage_ids[0].id,
        })

        self.assertFalse(normal_prep_line.routes_by_parent)
        self.assertTrue(state)
        self.assertEqual(state.stage_id.prep_display_id, self.modifier_display)

    def test_backend_parent_reference_fallback(self):
        fallback_modifier_line = self.env["pos.order.line"].create({
            "order_id": self.order.id,
            "product_id": self.modifier_product.id,
            "qty": 1,
            "price_unit": 2,
            "price_subtotal": 2,
            "price_subtotal_incl": 2,
            "parent_line_id": str(self.parent_order_line.id),
        })
        fallback_prep_line = self.env["pos.prep.line"].create({
            "prep_order_id": self.prep_order.id,
            "pos_order_line_id": fallback_modifier_line.id,
            "pos_order_line_uuid": fallback_modifier_line.uuid,
            "product_id": self.modifier_product.id,
            "quantity": 1,
        })

        fields_to_load = self.env["pos.order.line"]._load_pos_data_fields(
            self.config
        )
        self.assertIn("modifier_parent_uuid", fields_to_load)
        self.assertTrue(fallback_prep_line.routes_by_parent)
        self.assertEqual(
            fallback_prep_line.modifier_parent_uuid,
            self.parent_order_line.uuid,
        )

    def test_preparation_payload_contains_parent_hierarchy(self):
        parent_prep_line = self.env["pos.prep.line"].create({
            "prep_order_id": self.prep_order.id,
            "pos_order_line_id": self.parent_order_line.id,
            "pos_order_line_uuid": self.parent_order_line.uuid,
            "product_id": self.parent_product.id,
            "quantity": 1,
        })
        self.env["pos.prep.state"].create({
            "prep_line_id": parent_prep_line.id,
            "stage_id": self.parent_display.stage_ids[0].id,
        })
        modifier_prep_line = self.env["pos.prep.line"].create({
            "prep_order_id": self.prep_order.id,
            "pos_order_line_id": self.modifier_order_line.id,
            "pos_order_line_uuid": self.modifier_order_line.uuid,
            "product_id": self.modifier_product.id,
            "quantity": 1,
        })

        prep_lines = parent_prep_line | modifier_prep_line
        prep_states = self.env["pos.prep.state"].search([
            ("prep_line_id", "in", prep_lines.ids),
        ])
        payload = self.parent_display._get_preparation_display_order_additional_info(
            prep_states,
            prep_lines,
            self.prep_order,
        )
        payload_line = next(
            line
            for line in payload["pos.prep.line"]
            if line["id"] == modifier_prep_line.id
        )
        self.assertTrue(payload_line["routes_by_parent"])
        self.assertEqual(
            payload_line["modifier_parent_uuid"],
            self.parent_order_line.uuid,
        )
        self.assertEqual(
            payload_line["routing_category_ids"],
            self.parent_category.ids,
        )

    def test_frontend_assets_compile(self):
        for bundle_name in [
            "point_of_sale._assets_pos",
            "pos_preparation_display.assets",
        ]:
            bundle = self.env["ir.qweb"]._get_asset_bundle(bundle_name)
            self.assertTrue(bundle.js())
            self.assertTrue(bundle.css())
