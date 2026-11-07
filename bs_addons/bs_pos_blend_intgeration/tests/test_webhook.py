from datetime import datetime

import pytz

from odoo.tests.common import tagged

from ..services.exceptions import BlendAPIError, BlendAuthenticationError
from ..services.pos_api import BlendPOSAPIService
from ..services.webhook import BlendWebhookService
from .common import BlendTransactionCase, sample_menu


@tagged("post_install", "-at_install")
class TestBlendWebhookService(BlendTransactionCase):
    def test_native_product_modifier_semantics_are_supported(self):
        option = self.env["product.template"].create(
            {
                "name": "Native Modifier Option",
                "list_price": 2.0,
                "available_in_pos": True,
                "is_modifier": False,
                "use_as_modifier": True,
            }
        )
        group = self.env["modifiers.groups"].create(
            {
                "name": "Native Modifier Group",
                "modifier_ids": [(6, 0, option.ids)],
            }
        )
        product = self.env["product.template"].create(
            {
                "name": "Native Product With Modifiers",
                "list_price": 10.0,
                "available_in_pos": True,
                "is_modifier": True,
                "use_as_modifier": False,
                "modifier_grp_ids": [(6, 0, group.ids)],
            }
        )
        service = BlendPOSAPIService(self.env, self.config)

        self.assertEqual(
            service._find_product(self.config, str(product.id)),
            product,
        )
        self.assertEqual(
            service._find_product(self.config, str(option.id), modifier=True),
            option,
        )
        prepared = service._validate_modifiers(
            product,
            self.config,
            [{"id": str(option.id), "quantity": 1, "total": 2.0}],
        )
        self.assertEqual(prepared[0]["product"], option)
        self.assertEqual(prepared[0]["group"], group)
        serialized_groups = service._modifier_groups(product, self.config)
        self.assertEqual(serialized_groups[0]["max_selection"], 1)
        self.assertGreater(serialized_groups[0]["max_selection"], 0)

        option.with_context(blend_inbound_sync=True).available_in_pos = False
        self.assertFalse(service._modifier_groups(product, self.config))

    def test_menu_sync_rejects_non_positive_topping_maximum(self):
        payload = sample_menu(self.branch_id)
        payload["payload"]["toppings"][0]["max"] = 0

        with self.assertRaisesRegex(BlendAPIError, "selection limits"):
            self.webhook(payload)

    def test_menu_sync_writes_ids_on_native_records_and_is_idempotent(self):
        result = self.sync_sample_menu()

        self.assertEqual(result["processed_branches"], 1)
        self.assertEqual(result["result"], {
            "categories": 1,
            "items": 1,
            "toppings": 1,
            "options": 1,
        })
        product = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "100"),
                ("use_as_modifier", "=", False),
            ]
        )
        category = self.env["pos.category"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "10"),
            ]
        )
        modifier_group = self.env["modifiers.groups"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "20"),
            ]
        )
        modifier_option = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "200"),
                ("use_as_modifier", "=", True),
            ]
        )
        self.assertEqual(product.blend_external_id, "100")
        self.assertEqual(product.list_price, 150.0)
        self.assertTrue(product.is_modifier)
        self.assertFalse(product.use_as_modifier)
        self.assertEqual(category.blend_external_id, "10")
        self.assertIn(category, product.pos_categ_ids)
        self.assertIn(modifier_group, product.modifier_grp_ids)
        self.assertTrue(modifier_group.blend_is_required)
        self.assertEqual(modifier_group.blend_min_selection, 1)
        self.assertEqual(modifier_group.blend_max_selection, 2)
        self.assertEqual(modifier_group.blend_max_per_quantity, 2)
        self.assertEqual(modifier_option.list_price, 10.0)
        self.assertFalse(modifier_option.is_modifier)
        self.assertTrue(modifier_option.use_as_modifier)
        modifier_line = product.modifier_ids.filtered(
            lambda line: line.name.product_tmpl_id == modifier_option
        )
        self.assertEqual(modifier_line.blend_group_external_id, "20")
        self.assertEqual(modifier_line.blend_min_selection, 1)
        self.assertEqual(modifier_line.blend_max_selection, 2)

        duplicate = self.webhook(sample_menu(self.branch_id))
        self.assertEqual(duplicate["processed_branches"], 0)
        self.assertEqual(duplicate["duplicate_branches"], 1)
        self.assertEqual(
            self.env["blend.integration.log"].search_count(
                [("config_id", "=", self.config.id), ("event", "=", "menu_sync")]
            ),
            1,
        )

    def test_menu_resync_deactivates_removed_records_and_relations(self):
        self.sync_sample_menu()
        product = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "100"),
                ("use_as_modifier", "=", False),
            ]
        )
        modifier_group = self.env["modifiers.groups"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "20"),
            ]
        )
        modifier_option = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "200"),
                ("use_as_modifier", "=", True),
            ]
        )
        empty_menu = sample_menu(self.branch_id, event_time="2026-09-01T11:00:00Z")
        empty_menu["payload"].update({"categories": [], "items": [], "toppings": []})

        self.webhook(empty_menu)

        product.invalidate_recordset()
        modifier_group.invalidate_recordset()
        modifier_option.invalidate_recordset()
        self.assertFalse(product.blend_menu_active)
        self.assertFalse(product.blend_is_available)
        self.assertFalse(product.modifier_grp_ids)
        self.assertFalse(modifier_group.blend_menu_active)
        self.assertFalse(modifier_option.blend_menu_active)
        self.assertFalse(modifier_option.blend_is_available)

    def test_primary_availability_events(self):
        self.sync_sample_menu()
        product = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "100"),
                ("use_as_modifier", "=", False),
            ]
        )
        option = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "200"),
                ("use_as_modifier", "=", True),
            ]
        )

        item_result = self.webhook({
            "event": "item_availability",
            "event_time": "2026-09-01T10:01:00Z",
            "branch_id": self.branch_id,
            "payload": {"item_id": "100", "is_available": False},
        })
        modifier_result = self.webhook({
            "event": "modifier_availability",
            "event_time": "2026-09-01T10:02:00Z",
            "branch_id": self.branch_id,
            "payload": {"modifier_id": "200", "is_available": False},
        })
        branch_result = self.webhook({
            "event": "branch_availability",
            "event_time": "2026-09-01T10:03:00Z",
            "branch_id": self.branch_id,
            "payload": {"branch_id": self.branch_id, "is_available": False},
        })

        product.invalidate_recordset()
        option.invalidate_recordset()
        self.config.invalidate_recordset()
        self.assertFalse(product.blend_is_available)
        self.assertFalse(option.blend_is_available)
        self.assertFalse(self.config.blend_available)
        self.assertFalse(item_result["result"]["is_available"])
        self.assertFalse(modifier_result["result"]["is_available"])
        self.assertFalse(branch_result["result"]["is_available"])

    def test_pos_schema_aliases(self):
        self.sync_sample_menu()
        item_result = self.webhook({
            "brand_id": self.brand_id,
            "event_time": "2026-09-01T10:10:00Z",
            "event": "item.updated",
            "branch_id": self.branch_id,
            "product_id": "100",
            "is_stock": False,
        })
        store_result = self.webhook({
            "brand_id": self.brand_id,
            "event_time": "2026-09-01T10:11:00Z",
            "event": "store.updated",
            "branch_id": self.branch_id,
            "status": False,
        })

        self.assertFalse(item_result["result"]["is_available"])
        self.assertFalse(store_result["result"]["is_available"])

    def test_menu_updated_is_outbound_only(self):
        with self.assertRaisesRegex(BlendAPIError, "outbound-only"):
            self.webhook({
                "brand_id": self.brand_id,
                "event_time": "2026-09-01T10:20:00Z",
                "event": "menu.updated",
                "entity": {"type": "product", "id": "100"},
            })

    def test_flat_availability_requires_json_booleans(self):
        for payload in (
            {
                "brand_id": self.brand_id,
                "event_time": "2026-09-01T10:21:00Z",
                "event": "item.updated",
                "branch_id": self.branch_id,
                "product_id": "100",
                "is_stock": "false",
            },
            {
                "brand_id": self.brand_id,
                "event_time": "2026-09-01T10:22:00Z",
                "event": "store.updated",
                "branch_id": self.branch_id,
                "status": 0,
            },
        ):
            with self.subTest(event=payload["event"]):
                with self.assertRaisesRegex(BlendAPIError, "JSON boolean"):
                    self.webhook(payload)

    def test_order_status_primary_and_pos_alias(self):
        order = self.create_order()
        order.blend_order_id = "blend-order-900"

        self.webhook({
            "event": "order_updated",
            "event_time": "2026-09-01T10:30:00Z",
            "branch_id": self.branch_id,
            "payload": {"order_id": "blend-order-900", "status": "prepared"},
        })
        order.invalidate_recordset()
        self.assertEqual(order.blend_status, "prepared")

        self.webhook({
            "brand_id": self.brand_id,
            "event_time": "2026-09-01T10:31:00Z",
            "event": "order.updated",
            "branch_id": self.branch_id,
            "order_id": "blend-order-900",
            "status": "ACCEPTED",
        })
        order.invalidate_recordset()
        self.assertEqual(order.blend_status, "accepted")

    def test_authentication_validation_and_failed_event_log(self):
        payload = sample_menu(self.branch_id)
        service = BlendWebhookService(self.env)
        with self.assertRaises(BlendAuthenticationError):
            service.handle(payload, "")
        with self.assertRaises(BlendAuthenticationError):
            service.handle(payload, "Bearer incorrect")

        unsupported = {
            "event": "not_supported",
            "event_time": "2026-09-01T10:40:00Z",
            "branch_id": self.branch_id,
            "payload": {},
        }
        try:
            self.webhook(unsupported)
        except BlendAPIError:
            pass
        else:
            self.fail("Unsupported Blend events must raise BlendAPIError.")
        failed_log = self.env["blend.integration.log"].search(
            [("config_id", "=", self.config.id), ("event", "=", "not_supported")]
        )
        self.assertEqual(failed_log.state, "failed")
        self.assertEqual(failed_log.http_status, 400)
        self.assertGreaterEqual(failed_log.duration_ms, 0)

    def test_schedule_visibility(self):
        self.sync_sample_menu()
        product = self.env["product.template"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "100"),
                ("use_as_modifier", "=", False),
            ]
        )
        visible_monday = pytz.UTC.localize(datetime(2026, 9, 7, 10, 0, 0))
        hidden_monday = pytz.UTC.localize(datetime(2026, 9, 7, 23, 30, 0))
        self.assertTrue(product._blend_is_visible_now(self.config, visible_monday))
        self.assertFalse(product._blend_is_visible_now(self.config, hidden_monday))

    def test_menu_and_availability_are_isolated_per_branch(self):
        self.sync_sample_menu()
        other_config = self.env["pos.config"].create(
            {
                "name": "Other Blend Branch",
                "blend_enabled": True,
                "blend_branch_id": "%s-other" % self.branch_id,
                "blend_brand_id": self.brand_id,
                "blend_inbound_token": "other-token",
                "blend_sync_images": False,
                "payment_method_ids": [(6, 0, [])],
            }
        )
        other_menu = sample_menu(other_config.blend_branch_id)
        other_menu["event_time"] = "2026-09-01T12:00:00Z"
        BlendWebhookService(self.env).handle(other_menu, "Bearer other-token")

        products = self.env["product.template"].search(
            [
                ("blend_config_id", "in", [self.config.id, other_config.id]),
                ("blend_external_id", "=", "100"),
                ("use_as_modifier", "=", False),
            ]
        )
        self.assertEqual(len(products), 2)
        first_product = products.filtered(lambda product: product.blend_config_id == self.config)
        other_product = products.filtered(lambda product: product.blend_config_id == other_config)
        self.assertTrue(first_product)
        self.assertTrue(other_product)

        self.webhook(
            {
                "event": "item_availability",
                "event_time": "2026-09-01T12:01:00Z",
                "branch_id": self.branch_id,
                "payload": {"item_id": "100", "is_available": False},
            }
        )
        self.assertFalse(first_product.blend_is_available)
        self.assertTrue(other_product.blend_is_available)

        empty_menu = sample_menu(self.branch_id, event_time="2026-09-01T12:02:00Z")
        empty_menu["payload"].update({"categories": [], "items": [], "toppings": []})
        self.webhook(empty_menu)
        self.assertFalse(first_product.blend_menu_active)
        self.assertTrue(other_product.blend_menu_active)

    def test_modifier_group_availability_is_supported(self):
        self.sync_sample_menu()
        result = self.webhook(
            {
                "event": "modifier_availability",
                "event_time": "2026-09-01T12:10:00Z",
                "branch_id": self.branch_id,
                "payload": {"modifier_id": "20", "is_available": False},
            }
        )
        group = self.env["modifiers.groups"].search(
            [
                ("blend_config_id", "=", self.config.id),
                ("blend_external_id", "=", "20"),
            ]
        )
        lines = self.env["product.modifiers"].search(
            [
                ("product_id.blend_config_id", "=", self.config.id),
                ("blend_group_external_id", "=", "20"),
            ]
        )
        self.assertFalse(group.blend_is_available)
        self.assertTrue(lines)
        self.assertFalse(any(lines.mapped("blend_group_available")))
        self.assertEqual(result["result"]["modifier_type"], "group")

    def test_event_time_and_flat_contract_are_validated(self):
        with self.assertRaises(BlendAPIError):
            self.webhook(
                {
                    "event": "store.updated",
                    "brand_id": self.brand_id,
                    "branch_id": self.branch_id,
                    "status": True,
                }
            )
        with self.assertRaises(BlendAPIError):
            self.webhook(
                {
                    "event": "item.updated",
                    "event_time": "not-a-date",
                    "brand_id": self.brand_id,
                    "branch_id": self.branch_id,
                    "product_id": "100",
                    "is_stock": True,
                }
            )

    def test_mapping_models_are_not_registered(self):
        self.assertNotIn("blend.product.mapping", self.env.registry)
        self.assertNotIn("blend.category.mapping", self.env.registry)
        self.assertNotIn("blend.modifier.group.mapping", self.env.registry)
        self.assertNotIn("blend.modifier.option.mapping", self.env.registry)
