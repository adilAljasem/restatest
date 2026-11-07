"""Blend modifier totals use Bunbastic POS modifier records."""

from odoo import Command

from .test_blend_adapter import TestBlendAdapter, sample_menu
from .test_blend_core_intake import TestBlendCoreIntake
from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService
from ..services.menu_notifications import BlendMenuNotificationService


class TestBlendBunbasticMenu(TestBlendAdapter):
    def test_removed_topping_is_disabled_in_pos(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["pos.modifier.group"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        product = group._blend_parent_products()
        later = sample_menu(self.branch_id, event_time="2026-09-01T10:01:00Z")
        later["payload"]["toppings"] = []
        self.webhook(later)
        self.assertFalse(product.modifier_group_line_ids)
        self.assertFalse(product.allow_modifiers)
        self.assertFalse(group.active)
        self.webhook(sample_menu(self.branch_id, event_time="2026-09-01T10:02:00Z"))
        self.assertTrue(group.active)
        self.assertTrue(product.allow_modifiers)
        self.assertEqual(product.modifier_group_line_ids.modifier_group_id, group)

    def test_modifier_option_price_edit_queues_parent_menu_refresh(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["pos.modifier.group"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        product = group._blend_parent_products()
        self.assertEqual(product.blend_external_id, "item-100")
        group.option_ids.extra_price = 12
        self.assertTrue(self.env["bs.delivery.menu.version"].search([
            ("connection_id", "=", self.connection.id),
            ("blend_entity_type", "=", "product"),
            ("blend_entity_id", "=", str(product.id)),
        ]))

    def test_modifier_group_removal_clears_pos_options(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["pos.modifier.group"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        product = group._blend_parent_products().filtered(
            lambda item: item.blend_external_id == "item-100"
        )
        self.assertTrue(product.modifier_group_line_ids)
        group.unlink()
        self.assertFalse(product.modifier_group_line_ids)

    def test_modifier_group_option_edit_refreshes_pos_selectors(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["pos.modifier.group"].search([
            ("blend_config_id", "=", self.config.id),
            ("blend_external_id", "=", "group-20"),
        ], limit=1)
        product = group._blend_parent_products().filtered(
            lambda item: item.blend_external_id == "item-100"
        )
        option = self.env["product.product"].create({
            "name": "Extra Blend option", "list_price": 3,
            "available_in_pos": True,
        })
        self.env["pos.modifier.option"].create({
            "name": option.name,
            "group_id": group.id,
            "product_id": option.id,
            "option_type": "product",
            "price_mode": "fixed_extra",
            "extra_price": 3,
            "max_qty": 2,
        })
        group.max_qty = 3
        self.assertEqual(len(product.modifier_group_line_ids.modifier_group_id.option_ids), 2)
        self.assertEqual(product.modifier_group_line_ids.modifier_group_id.max_qty, 3)

    def test_modifier_group_edit_queues_parent_menu_refresh(self):
        self.webhook(sample_menu(self.branch_id))
        group = self.env["pos.modifier.group"].search([
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

    def test_get_menu_exposes_bunbastic_modifier_group(self):
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
            "name": "Blend Bunbastic menu prices",
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

    def _native_shared_group(self):
        self.connection.menu_pricelist_id = self.env["product.pricelist"].create({
            "name": "Blend product-scoped modifier prices",
            "currency_id": self.env.company.currency_id.id,
        })
        products = self.env["product.product"].with_context(blend_inbound_sync=True)
        cheese = products.create({
            "name": "Scoped cheese slice", "available_in_pos": True, "list_price": 3,
        })
        group = self.env["pos.modifier.group"].with_context(blend_inbound_sync=True).create({
            "name": "Dine in Burger Note",
            "company_id": self.env.company.id,
            "selection_type": "multiple",
            "max_qty": 3,
            "option_ids": [
                Command.create({
                    "name": "Without onions", "option_type": "instruction", "sequence": 1,
                }),
                Command.create({
                    "name": "Without pickles", "option_type": "instruction", "sequence": 2,
                }),
                Command.create({
                    "name": "Cheese", "option_type": "product", "product_id": cheese.id,
                    "price_mode": "fixed_extra", "extra_price": 3, "sequence": 3,
                }),
            ],
        })
        burgers = products.create([
            {"name": "Shared group burger %s" % index, "available_in_pos": True, "list_price": 30}
            for index in (1, 2)
        ])
        for burger in burgers:
            burger.product_tmpl_id.with_context(blend_inbound_sync=True).write({
                "allow_modifiers": True,
                "modifier_group_line_ids": [Command.create({"modifier_group_id": group.id})],
            })
        return group, burgers

    def _override(self, product, option, **values):
        group_line = product.product_tmpl_id.modifier_group_line_ids.filtered(
            lambda line: line.modifier_group_id == option.group_id
        )
        return self.env["pos.product.modifier.option.line"].create({
            "group_line_id": group_line.id,
            "product_tmpl_id": product.product_tmpl_id.id,
            "option_id": option.id,
            "name": option.name,
            "product_id": option.product_id.id,
            "price_mode": option.price_mode,
            "extra_price": option.extra_price,
            "max_qty": option.max_qty,
            **values,
        })

    def _menu_row(self, product, **filters):
        response = BlendPOSAPIService(self.env, self.config).get_menu(
            filters={"product_id": str(product.product_tmpl_id.id), **filters},
        )
        rows = [
            row for category in response["categories"] for row in category["products"]
            if row["id"] == product.product_tmpl_id.id
        ]
        return rows[0] if rows else None

    def test_each_product_gets_its_own_blend_group_and_modifier_ids(self):
        group, (plain, custom) = self._native_shared_group()
        onions, pickles, cheese = group.option_ids.sorted("sequence")
        self._override(custom, pickles, available=False)
        plain_line = plain.product_tmpl_id.modifier_group_line_ids
        custom_line = custom.product_tmpl_id.modifier_group_line_ids
        identities = self.env["bs.blend.modifier.identity"].sudo()

        plain_group = self._menu_row(plain)["modifier_groups"][0]
        custom_group = self._menu_row(custom)["modifier_groups"][0]

        self.assertEqual(plain_group["id"], BlendPOSAPIService.GROUP_ID_BASE + plain_line.id)
        self.assertEqual(custom_group["id"], BlendPOSAPIService.GROUP_ID_BASE + custom_line.id)
        plain_ids = [modifier["id"] for modifier in plain_group["modifiers"]]
        custom_ids = [modifier["id"] for modifier in custom_group["modifiers"]]
        expected = identities._ids_for([(plain_line.id, option.id) for option in (onions, pickles, cheese)])
        self.assertEqual(plain_ids, [
            BlendPOSAPIService.MODIFIER_ID_BASE + expected[(plain_line.id, option.id)]
            for option in (onions, pickles, cheese)
        ])
        # Closed on this product only; nothing is shared with the other product.
        self.assertEqual(len(custom_ids), 2)
        self.assertFalse(set(plain_ids) & set(custom_ids))
        self.assertFalse({plain_group["id"], custom_group["id"]} & set(plain_ids + custom_ids))
        # IDs are stable across menu requests.
        self.assertEqual(
            [modifier["id"] for modifier in self._menu_row(plain)["modifier_groups"][0]["modifiers"]],
            plain_ids,
        )
        filtered = self._menu_row(custom, modifier_group_id=str(custom_group["id"]))
        self.assertEqual([item["id"] for item in filtered["modifier_groups"]], [custom_group["id"]])
        self.assertIsNone(self._menu_row(plain, modifier_group_id=str(custom_group["id"])))

    def test_product_option_override_keeps_its_own_price(self):
        group, (plain, custom) = self._native_shared_group()
        cheese = group.option_ids.sorted("sequence")[-1]
        self._override(custom, cheese, name="Double cheese", extra_price=5, max_qty=1)

        plain_cheese = self._menu_row(plain)["modifier_groups"][0]["modifiers"][-1]
        custom_cheese = self._menu_row(custom)["modifier_groups"][0]["modifiers"][-1]

        self.assertEqual((plain_cheese["price"], plain_cheese["name"]["en"]), (3, "Cheese"))
        self.assertEqual((custom_cheese["price"], custom_cheese["name"]["en"]), (5, "Double cheese"))
        self.assertNotEqual(plain_cheese["id"], custom_cheese["id"])

    def test_menu_preview_explains_each_option(self):
        group, (burger, _other) = self._native_shared_group()
        products = self.env["product.product"].with_context(blend_inbound_sync=True)
        tracked = products.create({
            "name": "Tracked cheese slices", "available_in_pos": False,
            "is_storable": True, "list_price": 2,
        })
        imported = products.create({
            "name": "Old Blend pickle", "available_in_pos": False, "list_price": 2,
            "blend_external_id": "old-pickle", "blend_is_available": False,
        })
        options = self.env["pos.modifier.option"].with_context(blend_inbound_sync=True)
        options.create([
            {"name": "Tracked cheese", "group_id": group.id, "option_type": "product",
             "product_id": tracked.id, "price_mode": "fixed_extra", "extra_price": 2},
            {"name": "Old pickle", "group_id": group.id, "option_type": "product",
             "product_id": imported.id, "price_mode": "fixed_extra", "extra_price": 2},
        ])
        self._override(burger, group.option_ids.sorted("sequence")[1], available=False)

        action = burger.product_tmpl_id.action_blend_menu_preview()
        html = str(self.env[action["res_model"]].browse(action["res_id"]).result_html)

        # A tracked product without stock stays available.
        self.assertNotIn("Out of stock", html)
        self.assertEqual(html.count("Shown in Blend"), 3)
        self.assertIn("&#39;Available in Blend&#39; is off", html)
        self.assertIn("&#39;Available&#39; is unchecked for this product", html)
        self.assertIn("Shown in Blend", html)

    def test_product_option_availability_queues_menu_refresh(self):
        group, (_plain, custom) = self._native_shared_group()
        domain = [
            ("connection_id", "=", self.connection.id),
            ("blend_entity_type", "=", "product"),
            ("blend_entity_id", "=", str(custom.product_tmpl_id.id)),
        ]
        versions = self.env["bs.delivery.menu.version"]
        self.assertFalse(versions.search(domain))
        override = self._override(custom, group.option_ids[0])
        self.assertTrue(versions.search(domain))
        versions.search(domain).unlink()
        override.available = False
        self.assertTrue(versions.search(domain))

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
        group_lines = product.modifier_group_line_ids.filtered(
            lambda line: line.modifier_group_id.blend_external_id == "group-20"
        )
        self.assertEqual(len(group_lines), 1)
        group = group_lines.modifier_group_id
        self.assertEqual(group.min_qty, 1)
        self.assertEqual(group.max_qty, 2)
        self.assertEqual(group.option_ids.blend_external_id, "option-200")
        self.assertEqual(group.option_ids.extra_price, 10)


class TestBlendBunbasticModifiers(TestBlendCoreIntake):
    def test_hidden_local_modifier_is_paid_and_invoiced(self):
        option = self.env["product.product"].create({
            "name": "Hidden local Blend extra", "available_in_pos": False,
            "blend_is_available": False, "is_storable": False,
            "list_price": 2, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        group = self._attach_blend_modifier_group(self.product, option)
        group.option_ids.blend_external_id = False
        option_id = str(group.option_ids.id)
        self._enable_auto_accept()
        payload = self._blend_request(
            "JAHEZ-HIDDEN-LOCAL-EXTRA", subtotal="22.00", total="22.00",
            products=[{
                "id": "MEAL", "quantity": 1, "total": "20.00",
                "modifiers": [{"id": option_id, "quantity": 1, "total": "2.00"}],
            }],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-HIDDEN-LOCAL-EXTRA"),
        ])
        pos = order.pos_order_id
        modifier = pos.lines.filtered(lambda line: line.product_id == option)
        self.assertEqual(modifier.qty, 1)
        self.assertEqual(modifier.price_unit, 2)
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.amount_paid, 22)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(pos.account_move.amount_total, 22)

    def test_free_modifier_is_paid_and_invoiced(self):
        option = self.env["product.product"].create({
            "name": "Free Blend extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "FREE-EXTRA",
            "product_id": option.id,
        })
        group = self._attach_blend_modifier_group(self.product, option)
        self._enable_auto_accept()
        payload = self._blend_request(
            "JAHEZ-FREE-EXTRA", subtotal="20.00", total="20.00",
            products=[{
                "id": "MEAL", "quantity": 2, "total": "20.00",
                "modifiers": [{"id": "FREE-EXTRA", "quantity": 1, "total": "0.00"}],
            }],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-FREE-EXTRA"),
        ])
        pos = order.pos_order_id
        modifier = pos.lines.filtered(lambda line: line.product_id == option)
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(modifier.modifier_qty_per_parent, 1)
        self.assertFalse(modifier.modifier_is_included)
        self.assertEqual(group.option_ids.price_mode, "fixed_extra")
        self.assertEqual(group.option_ids.extra_price, 2)

    def test_included_catalog_option_uses_paid_blend_price_without_allowance(self):
        option = self.env["product.product"].create({
            "name": "Included Blend extra", "available_in_pos": True,
            "list_price": 0, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "INCLUDED-EXTRA",
            "product_id": option.id,
        })
        group = self._attach_blend_modifier_group(self.product, option)
        group.option_ids.price_mode = "included"
        self._enable_auto_accept()
        payload = self._blend_request(
            "JAHEZ-PAID-INCLUDED-EXTRA", subtotal="22.00", total="22.00",
            products=[{
                "id": "MEAL", "quantity": 1, "total": "20.00",
                "modifiers": [{"id": "INCLUDED-EXTRA", "quantity": 1, "total": "2.00"}],
            }],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-PAID-INCLUDED-EXTRA"),
        ])
        pos = order.pos_order_id
        modifier = pos.lines.filtered(lambda line: line.product_id == option)
        self.assertFalse(modifier.modifier_is_included)
        self.assertEqual(modifier.price_unit, 2)
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.amount_paid, 22)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(pos.account_move.amount_total, 22)

    def test_modifier_uses_blend_price_in_paid_pos_and_invoice(self):
        option = self.env["product.product"].create({
            "name": "Fixed price Blend extra", "available_in_pos": True,
            "list_price": 2.5, "taxes_id": [Command.clear()],
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "FIXED-EXTRA",
            "product_id": option.id,
        })
        group = self._attach_blend_modifier_group(self.product, option)
        self._enable_auto_accept()
        payload = self._blend_request(
            "JAHEZ-PRICE-MISMATCH", subtotal="32.00", total="32.00",
            products=[{
                "id": "MEAL", "quantity": 2, "total": "20.00",
                "modifiers": [{"id": "FIXED-EXTRA", "quantity": 2, "total": "6.00"}],
            }],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-PRICE-MISMATCH"),
        ])
        pos = order.pos_order_id
        modifier = pos.lines.filtered(lambda line: line.product_id == option)
        self.assertEqual(group.option_ids.extra_price, 2.5)
        self.assertEqual(modifier.qty, 4)
        self.assertEqual(modifier.price_unit, 3)
        self.assertEqual(modifier._expected_modifier_unit_price(), 2.5)
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.amount_paid, 32)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(pos.account_move.amount_total, 32)

    def test_unavailable_modifier_is_rejected(self):
        option = self.env["product.product"].create({
            "name": "Unavailable Blend extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
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

    def test_jahez_372_requires_375_total(self):
        products = {"1483": self.product}
        for external_id in ("1485", "44", "28", "14", "12"):
            products[external_id] = self.env["product.product"].create({
                "name": "Blend product %s" % external_id,
                "available_in_pos": True,
                "list_price": {"14": 2, "12": 1.5}.get(external_id, 1),
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
        group_14 = self._attach_blend_modifier_group(products["44"], products["14"])
        group_12 = self._attach_blend_modifier_group(products["28"], products["12"])
        self.assertEqual(group_14.option_ids.extra_price, 2)
        self.assertEqual(group_12.option_ids.extra_price, 1.5)
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
            "name": "Blend Bunbastic extra", "available_in_pos": True,
            "list_price": 2.5, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "EXTRA", "product_id": extra.id,
        })
        group = self._attach_blend_modifier_group(self.product, extra)
        payload = self._blend_request(
            "JAHEZ-BUNBASTIC-EXTRAS", subtotal="30.00", total="30.00",
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
            ("external_order_id", "=", "JAHEZ-BUNBASTIC-EXTRAS"),
        ])
        self.assertEqual(order.line_ids.filtered("is_modifier").quantity, 4)
        order.transition_to(
            "accepted", source="automatic", expected_state="received",
            idempotency_key="accept:JAHEZ-BUNBASTIC-EXTRAS",
        )
        pos = order.pos_order_id
        parent = pos.lines.filtered(lambda line: line.product_id == self.product)
        modifier = pos.lines.filtered(lambda line: line.product_id == extra)
        self.assertEqual(modifier.qty, 4)
        self.assertEqual(modifier.price_unit, 2.5)
        self.assertTrue(modifier.is_modifier_line)
        self.assertEqual(modifier.modifier_parent_line_id, parent)
        self.assertEqual(modifier.modifier_qty_per_parent, 2)
        self.assertEqual(parent.modifier_selection_data, {
            str(group.option_ids.id): 2.0,
        })
        self.assertEqual(pos.amount_total, 30)
        self.assertEqual(pos.amount_paid, 30)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(pos.account_move.amount_total, 30)


    def _native_meal_group(self, extra):
        group = self.env["pos.modifier.group"].create({
            "name": "Native meal choices", "company_id": self.company.id,
            "selection_type": "multiple", "min_qty": 0, "max_qty": 3,
            "option_ids": [
                Command.create({
                    "name": "Extra", "option_type": "product", "product_id": extra.id,
                    "price_mode": "fixed_extra", "extra_price": 2, "max_qty": 2,
                }),
                Command.create({"name": "Without onions", "option_type": "instruction"}),
            ],
        })
        group_line = self.env["pos.product.modifier.group.line"].create({
            "product_tmpl_id": self.product.product_tmpl_id.id,
            "modifier_group_id": group.id,
            "company_id": self.company.id,
        })
        self.product.product_tmpl_id.allow_modifiers = True
        return group, group_line

    def _menu_modifier_id(self, group_line, option):
        identity = self.env["bs.blend.modifier.identity"].sudo()._ids_for(
            [(group_line.id, option.id)]
        )[(group_line.id, option.id)]
        return str(BlendPOSAPIService.MODIFIER_ID_BASE + identity)

    def _order_with_modifiers(self, reference, modifiers, *, total="22.00"):
        return self._blend_request(
            reference, subtotal=total, total=total,
            products=[{
                "id": "MEAL", "quantity": 1, "total": "20.00", "modifiers": modifiers,
            }],
        )

    def test_per_product_modifier_id_resolves_exact_option(self):
        extra = self.env["product.product"].create({
            "name": "Per-product Blend extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        group, group_line = self._native_meal_group(extra)
        option, instruction = group.option_ids.sorted("id")
        self._enable_auto_accept()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._order_with_modifiers("JAHEZ-PER-PRODUCT", [
                {"id": self._menu_modifier_id(group_line, option), "quantity": 1, "total": "2.00"},
                {"id": self._menu_modifier_id(group_line, instruction), "quantity": 1, "total": "0.00"},
            ]),
            "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-PER-PRODUCT"),
        ])
        pos = order.pos_order_id
        modifier = pos.lines.filtered(lambda line: line.product_id == extra)
        self.assertTrue(modifier.is_modifier_line)
        self.assertEqual(modifier.modifier_option_id, option)
        self.assertEqual(pos.amount_paid, 22)
        # The preparation note reaches the ordered item instead of rejecting the order.
        parent = order.line_ids.filtered(lambda line: not line.is_modifier)
        self.assertIn("Without onions", parent.customer_note)

    def test_modifier_above_odoo_limit_is_prepared_as_ordered(self):
        """The customer paid on the platform: Odoo's limits must not drop the order."""
        extra = self.env["product.product"].create({
            "name": "Limited Blend extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        group, group_line = self._native_meal_group(extra)
        option = group.option_ids.sorted("id")[0]
        option.max_qty = 1
        group.max_qty = 1
        self._enable_auto_accept()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._order_with_modifiers("JAHEZ-ABOVE-LIMIT", [
                {"id": self._menu_modifier_id(group_line, option), "quantity": 3, "total": "2.00"},
            ], total="26.00"),
            "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-ABOVE-LIMIT"),
        ])
        self.assertEqual(order.state, "accepted")
        modifier = order.pos_order_id.lines.filtered(lambda line: line.product_id == extra)
        self.assertEqual(modifier.qty, 3)
        self.assertAlmostEqual(order.pos_order_id.amount_total, 26.0, places=2)

    def test_per_product_modifier_closed_on_product_is_rejected(self):
        extra = self.env["product.product"].create({
            "name": "Closed per-product extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
        })
        group, group_line = self._native_meal_group(extra)
        option = group.option_ids.sorted("id")[0]
        modifier_id = self._menu_modifier_id(group_line, option)
        self.env["pos.product.modifier.option.line"].create({
            "group_line_id": group_line.id,
            "product_tmpl_id": self.product.product_tmpl_id.id,
            "option_id": option.id, "name": option.name, "product_id": extra.id,
            "price_mode": "fixed_extra", "extra_price": 2, "max_qty": 2,
            "available": False,
        })
        with self.assertRaisesRegex(BlendAPIError, "not uniquely assigned"):
            self.env["bs.delivery.blend.intake"].receive_order(
                self._order_with_modifiers("JAHEZ-PER-PRODUCT-CLOSED", [
                    {"id": modifier_id, "quantity": 1, "total": "2.00"},
                ]),
                "Bearer blend-core-test-token",
            )

    def test_legacy_option_line_modifier_id_is_still_accepted(self):
        extra = self.env["product.product"].create({
            "name": "Legacy per-product extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        group, group_line = self._native_meal_group(extra)
        option = group.option_ids.sorted("id")[0]
        override = self.env["pos.product.modifier.option.line"].create({
            "group_line_id": group_line.id,
            "product_tmpl_id": self.product.product_tmpl_id.id,
            "option_id": option.id, "name": "Meal extra", "product_id": extra.id,
            "price_mode": "fixed_extra", "extra_price": 2, "max_qty": 2,
        })
        legacy_id = str(BlendPOSAPIService.LEGACY_OPTION_LINE_BASE + override.id)
        self._enable_auto_accept()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._order_with_modifiers("JAHEZ-LEGACY-LINE", [
                {"id": legacy_id, "quantity": 1, "total": "2.00"},
            ]),
            "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
