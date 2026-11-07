"""Blend modifier quantities and POS lines follow the Pino order contract."""

from odoo import Command

from .test_blend_adapter import TestBlendAdapter, sample_menu
from .test_blend_core_intake import TestBlendCoreIntake
from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService
from ..services.menu_notifications import BlendMenuNotificationService


class TestBlendPinoMenu(TestBlendAdapter):
    def test_modifier_group_removal_clears_pos_options(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["modifiers.groups"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        product = group._blend_parent_products().filtered(
            lambda item: item.blend_external_id == "item-100"
        )
        self.assertTrue(product.modifier_ids)
        group.unlink()
        self.assertFalse(product.modifier_grp_ids)
        self.assertFalse(product.modifier_ids)

    def test_modifier_group_option_edit_refreshes_pos_selectors(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["modifiers.groups"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        product = group._blend_parent_products().filtered(
            lambda item: item.blend_external_id == "item-100"
        )
        option = self.env["product.template"].create({
            "name": "Extra Blend option", "list_price": 3,
            "available_in_pos": True, "use_as_modifier": True,
        })
        group.write({
            "modifier_ids": [Command.link(option.id)],
            "blend_max_selection": 3,
        })
        lines = product.modifier_ids.filtered(
            lambda line: line.name.product_tmpl_id in group.modifier_ids
        )
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.apply_selector for line in lines))
        self.assertEqual(set(lines.mapped("blend_max_selection")), {3})
        self.assertEqual(set(lines.mapped("blend_group_external_id")), {"group-20"})

    def test_modifier_group_edit_queues_parent_menu_refresh(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["modifiers.groups"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        self.assertTrue(group)
        parents = group._blend_parent_products()
        self.assertIn("item-100", parents.mapped("blend_external_id"))
        product = parents.filtered(lambda item: item.blend_external_id == "item-100")
        self.assertIn(
            (self.connection.id, "product", str(product.id)),
            BlendMenuNotificationService(self.env).snapshot(parents),
        )
        self.assertFalse(group.env.context.get("blend_inbound_sync"))
        group.write({"name": "Updated Blend extras"})
        self.assertTrue(self.env["bs.delivery.menu.version"].search([
            ("connection_id", "=", self.connection.id),
            ("blend_entity_type", "=", "product"),
            ("blend_entity_id", "=", str(product.id)),
        ]))

    def test_get_menu_exposes_pino_modifier_group(self):
        menu = sample_menu(self.branch_id)
        menu["payload"]["categories"][0].update({
            "id": "10", "items": ["100"],
        })
        menu["payload"]["items"][0].update({
            "id": "100", "categories": ["10"],
        })
        menu["payload"]["toppings"][0].update({
            "id": "20", "items": ["100"],
        })
        menu["payload"]["toppings"][0]["options"][0]["id"] = "200"
        self.webhook(menu)
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend Pino menu prices",
            "currency_id": self.env.company.currency_id.id,
        })
        response = BlendPOSAPIService(self.env, self.config).get_menu(
            page=1, per=10, filters={"product_id": "100"},
        )
        products = [
            product for category in response["categories"]
            for product in category["products"]
        ]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["modifier_groups"][0]["id"], 20)
        self.assertEqual(products[0]["modifier_groups"][0]["max_selection"], 2)
        self.assertEqual(products[0]["modifier_groups"][0]["modifiers"][0]["id"], 200)
        self.assertEqual(products[0]["modifier_groups"][0]["modifiers"][0]["price"], 10)

    def test_pos_export_keeps_modifier_under_parent(self):
        order = self._create_order()
        items = order._blend_build_items(self.connection)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "item-100")
        self.assertEqual(items[0]["options"], [{
            "id": "option-200", "price": 10.0, "quantity": 1,
        }])

    def test_menu_creates_product_modifier_group(self):
        self.webhook(sample_menu(self.branch_id))
        product = self.env["product.template"].search([
            ("blend_external_id", "=", "item-100"),
        ], limit=1)
        self.assertEqual(product.type, "consu")
        group = product.modifier_grp_ids.filtered(
            lambda item: item.blend_external_id == "group-20"
        )
        self.assertEqual(len(group), 1)
        self.assertEqual(group.blend_min_selection, 1)
        self.assertEqual(group.blend_max_selection, 2)
        self.assertEqual(group.modifier_ids.blend_external_id, "option-200")
        self.assertEqual(group.modifier_ids.blend_option_price, 10)
        self.assertTrue(product.modifier_ids.filtered(
            lambda line: line.name.product_tmpl_id in group.modifier_ids
        ).apply_selector)


class TestBlendPinoModifiers(TestBlendCoreIntake):
    def test_unavailable_modifier_is_rejected(self):
        option = self.env["product.product"].create({
            "name": "Unavailable Blend extra", "available_in_pos": True,
            "list_price": 2,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "UNAVAILABLE-EXTRA",
            "product_id": option.id,
        })
        self._attach_blend_modifier_group(self.product, option)
        option.product_tmpl_id.write({
            "blend_external_id": "UNAVAILABLE-EXTRA",
            "blend_is_option": True,
            "blend_is_available": False,
        })
        payload = self._blend_request(
            "JAHEZ-UNAVAILABLE-EXTRA", subtotal="22.00", total="22.00",
            products=[{
                "id": "MEAL", "quantity": 1, "total": "20.00",
                "modifiers": [{"id": "UNAVAILABLE-EXTRA", "quantity": 1, "total": "2.00"}],
            }],
        )
        with self.assertRaisesRegex(BlendAPIError, "unavailable"):
            self.env["bs.delivery.blend.intake"].receive_order(
                payload, "Bearer blend-core-test-token",
            )

    def test_jahez_372_requires_pino_375_total(self):
        products = {"1483": self.product}
        for external_id in ("1485", "44", "28", "14", "12"):
            products[external_id] = self.env["product.product"].create({
                "name": "Blend product %s" % external_id,
                "available_in_pos": True, "list_price": 1,
                "taxes_id": [Command.clear()],
                "property_account_income_id": self.sales_account.id,
            })
        for external_id, product in products.items():
            self.env["bs.delivery.product.binding"].create({
                "connection_id": self.connection.id,
                "store_binding_id": self.store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })
        self._attach_blend_modifier_group(products["44"], products["14"])
        self._attach_blend_modifier_group(products["28"], products["12"])
        self._enable_auto_accept()
        payload = self._blend_request(
            "jahez: 742295540", subtotal="372.00", total="372.00",
            products=[
                {"id": "1483", "quantity": 2, "total": 192, "modifiers": []},
                {"id": "1485", "quantity": 1, "total": 3, "modifiers": []},
                {"id": "44", "quantity": 3, "total": 63, "modifiers": [
                    {"id": "14", "quantity": 2, "total": "2.00"},
                ]},
                {"id": "28", "quantity": 3, "total": 108, "modifiers": [
                    {"id": "12", "quantity": 2, "total": "1.00"},
                ]},
            ],
        )
        with self.assertRaisesRegex(BlendAPIError, "product totals"):
            self.env["bs.delivery.blend.intake"].receive_order(
                payload, "Bearer blend-core-test-token",
            )
        payload.update({"subtotal": "375.00", "total": "375.00"})
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "jahez: 742295540"),
        ])
        pos = order.pos_order_id
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.amount_total, 375)
        self.assertEqual(pos.amount_paid, 375)
        self.assertEqual(pos.account_move.amount_total, 375)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertCountEqual(
            [(line.product_id.id, line.qty, line.price_subtotal_incl)
             for line in pos.lines if line.product_id in
             (products["14"] | products["12"])],
            [(products["14"].id, 6, 6), (products["12"].id, 6, 3)],
        )

    def test_modifier_selection_scales_with_parent_quantity(self):
        extra = self.env["product.product"].create({
            "name": "Blend Pino extra", "available_in_pos": True,
            "list_price": 2.5, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "EXTRA", "product_id": extra.id,
        })
        group_model = self.env["modifiers.groups"]
        group_values = {
            "name": "Blend Pino extras",
            "modifier_ids": [Command.set(extra.product_tmpl_id.ids)],
        }
        if "blend_external_id" in group_model._fields:
            group_values.update({
                "blend_config_id": self.basic_config.id,
                "blend_external_id": "EXTRA-GROUP",
                "blend_max_selection": 2,
                "blend_max_per_quantity": 2,
            })
        group = group_model.create(group_values)
        self.product.product_tmpl_id.modifier_grp_ids = group
        self.product.product_tmpl_id.get_modifier()
        payload = self._blend_request(
            "JAHEZ-PINO-EXTRAS", subtotal="30.00", total="30.00",
            products=[{
                "id": "MEAL", "quantity": 2, "total": "20.00",
                "modifiers": [{"id": "EXTRA", "quantity": 2, "total": "5.00"}],
            }],
        )

        self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-PINO-EXTRAS"),
        ])
        self.assertEqual(order.line_ids.filtered("is_modifier").quantity, 4)
        order.transition_to(
            "accepted", source="automatic", expected_state="received",
            idempotency_key="accept:JAHEZ-PINO-EXTRAS",
        )
        pos = order.pos_order_id
        parent = pos.lines.filtered(lambda line: line.product_id == self.product)
        modifier = pos.lines.filtered(lambda line: line.product_id == extra)
        self.assertEqual(modifier.qty, 4)
        self.assertEqual(modifier.price_unit, 2.5)
        self.assertEqual(modifier.parent_line_id, str(parent.id))
        self.assertEqual(modifier.modifier_selection_qty, 2)
        self.assertEqual(pos.amount_total, 30)
        self.assertEqual(pos.amount_paid, 30)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(pos.account_move.amount_total, 30)
