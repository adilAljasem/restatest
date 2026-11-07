from datetime import timedelta
from unittest.mock import patch

import requests

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from ..services import outbound
from ..services.pos_api import BlendPOSAPIService
from ..services.stock_availability import BlendStockAvailabilityService
from ..services.exceptions import BlendAPIError
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendStockAvailability(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("ar_001")
        self.config.write({"blend_branch_id": "95001", "blend_brand_id": "95000"})
        self.location_a, self.location_b = self.env["stock.location"].create([
            {"name": name, "usage": "internal", "company_id": self.env.company.id}
            for name in ("Blend stock branch A", "Blend stock branch B")
        ])
        self.child = self.env["stock.location"].create({
            "name": "Blend shelf A", "usage": "internal", "location_id": self.location_a.id,
            "company_id": self.env.company.id,
        })
        self.other_config = self.env["pos.config"].create({
            "name": "Blend stock branch B", "blend_enabled": True,
            "blend_branch_id": "95002", "blend_brand_id": "95000",
            "blend_inbound_token": "blend-stock-other-test-token",
            "blend_event_url": "https://blend.example.test/events",
            "payment_method_ids": [Command.clear()],
        })
        for config, location in ((self.config, self.location_a), (self.other_config, self.location_b)):
            operation = self.env["stock.picking.type"].create({
                "name": "Blend stock outgoing", "code": "outgoing", "sequence_code": "BLST",
                "warehouse_id": config.picking_type_id.warehouse_id.id,
                "default_location_src_id": location.id,
                "default_location_dest_id": self.env.ref("stock.stock_location_customers").id,
            })
            config.picking_type_id = operation
        self.product = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Blend stock item", "available_in_pos": True, "sale_ok": True,
            "type": "consu", "is_storable": True, "company_id": self.env.company.id,
        }).with_env(self.env)
        self.quant = self.env["stock.quant"]
        self.logs = self.env["blend.integration.log"]
        self._mark()

    def _mark(self):
        self.last_id = self.logs.search([], order="id desc", limit=1).id or 0

    def _new_logs(self):
        return self.logs.search([("id", ">", self.last_id), ("stock_check", "!=", False)], order="id")

    def _add(self, quantity, location=None, product=None, **kwargs):
        return self.quant._update_available_quantity(
            (product or self.product).product_variant_id, location or self.location_a, quantity, **kwargs,
        )

    def _available(self, config=None, product=None):
        config = config or self.config
        return BlendPOSAPIService(self.env, config)._product_available(product or self.product, config)

    def _process(self, logs=None):
        logs = self._new_logs() if logs is None else logs
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as send:
            for log in logs:
                log._process_outbound()
        return send

    def test_menu_boolean_stock_is_branch_local_and_includes_shelves(self):
        self._add(2, self.child)
        self.assertTrue(self._available())
        self.assertFalse(self._available(self.other_config))
        service = BlendPOSAPIService(self.env, self.config | self.other_config)
        menu = service.get_menu(filters={"product_id": str(self.product.id)})
        row = next(row for category in menu["categories"] for row in category["products"])
        self.assertIs(row["stock"], True)
        self.assertEqual({item["branch_id"]: item["is_stock"] for item in row["available_branches"]}, {
            "95001": True, "95002": False,
        })
        self._mark()
        self._add(-2, self.child)
        self.assertFalse(self._available())
        self.assertEqual(self._new_logs().config_id, self.config)
        send = self._process()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs["json"]["event"], "menu.updated")
        self.assertEqual(send.call_args.kwargs["json"]["entity"], {"type": "product", "id": str(self.product.id)})
        self.assertNotIn("stock_check", send.call_args.kwargs["json"])

    def test_restock_reservation_and_unreserve_change_availability(self):
        self.assertFalse(self._available())
        self._add(5)
        self.assertTrue(self._available())
        self.assertEqual(self._process().call_count, 1)
        for amount, expected in ((5, False), (-5, True)):
            self._mark()
            self.quant._update_reserved_quantity(self.product.product_variant_id, self.location_a, amount)
            self.assertIs(self._available(), expected)
            self.assertEqual(self._process().call_count, 1)

    def test_no_http_during_stock_write_and_no_notification_while_still_positive(self):
        self._add(5)
        self._mark()
        with patch.object(outbound.requests, "post") as send:
            self._add(-1)
        send.assert_not_called()
        logs = self._new_logs()
        self.assertTrue(logs)
        self.assertEqual(set(logs.mapped("state")), {"pending"})
        self.assertEqual(self._process(logs).call_count, 0)
        self.assertTrue(all(log.response_payload["skipped"] for log in logs))
        self.assertEqual(set(logs.mapped("attempt_count")), {0})

    def test_zero_negative_and_untracked_products(self):
        self._add(-2)
        self.assertFalse(self._available())
        self.assertEqual(self._process().call_count, 0)
        self.product.with_context(blend_inbound_sync=True).write({"is_storable": False})
        self.assertTrue(self._available())
        service = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Untracked service", "type": "service", "available_in_pos": True,
        })
        self.assertTrue(self._available(product=service))

    def test_manual_and_branch_disabling_win_over_restock(self):
        self.product.blend_unavailable_config_ids = [Command.link(self.config.id)]
        self._mark()
        self._add(3)
        self.assertFalse(self._available())
        self.assertEqual(self._process().call_count, 0)
        self.assertIn(self.config, self.product.blend_unavailable_config_ids)
        self.product.blend_unavailable_config_ids = [Command.clear()]
        self.assertTrue(self._available())
        self.config.blend_available = False
        self._mark()
        self._add(-3)
        self._add(4)
        self.assertFalse(self._available())
        self.assertEqual(self._process().call_count, 0)

    def test_external_item_manual_flag_and_schedule_are_preserved(self):
        self.product.with_context(blend_inbound_sync=True).write({
            "blend_external_id": "95555", "blend_config_id": self.config.id,
            "blend_is_available": False,
        })
        self._add(1)
        self.assertFalse(self._available())
        self.assertEqual(self._process().call_count, 0)
        self.product.blend_is_available = True
        self.assertTrue(self._available())
        self.config.blend_schedule_json = {
            day: [] for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        }
        self.assertFalse(self._available())

    def test_rollbacks_remove_stock_jobs(self):
        with self.assertRaises(ValueError), self.env.cr.savepoint():
            self._add(1)
            self.assertTrue(self._new_logs())
            raise ValueError("Rollback stock operation")
        self.assertFalse(self._new_logs())
        self.assertFalse(self._available())

    def test_transient_balance_is_rechecked_after_transaction(self):
        self._add(1)
        self._mark()
        self._add(-1)
        logs = self._new_logs()
        self._add(1)
        self.assertEqual(self._process(logs).call_count, 0)
        self.assertTrue(self._available())

    def test_later_concurrent_depletion_is_not_missed_by_the_outbox(self):
        self._add(2)
        self._mark()
        self._add(-1)
        first_change = self._new_logs()
        # The first change alone remains positive, but its durable check must
        # detect a later transaction consuming the last unit before delivery.
        self._add(-1)
        self.assertFalse(self._available())
        self.assertEqual(self._process(first_change).call_count, 1)

    def test_last_delivery_wins_over_stale_writer_baseline(self):
        service = BlendStockAvailabilityService(self.env)
        # Simulate two writers with the same original out-of-stock snapshot.
        stale_snapshot = service.capture({(self.product.product_variant_id.id, self.location_a.id)})
        self._add(1)
        self.assertEqual(self._process().call_count, 1)
        self._add(-1)
        self._mark()
        service.enqueue(stale_snapshot)
        # Comparing only before=False and now=False would incorrectly skip it.
        self.assertEqual(self._process().call_count, 1)

    def test_repeated_stock_checks_coalesce_to_last_delivered_state(self):
        self._add(1)
        self._add(1)
        self.assertEqual(self._process().call_count, 1)

    def test_native_stock_move_and_return_are_observed(self):
        self._add(1)
        self._mark()
        customer = self.env.ref("stock.stock_location_customers")
        for source, destination, expected in (
            (self.location_a, customer, False), (customer, self.location_a, True),
        ):
            self._mark()
            move = self.env["stock.move"].create({
                "product_id": self.product.product_variant_id.id, "product_uom_qty": 1,
                "product_uom": self.product.uom_id.id,
                "location_id": source.id, "location_dest_id": destination.id,
                "company_id": self.env.company.id,
            })
            move._action_confirm()
            move._action_assign()
            move.quantity = 1
            move.picked = True
            move._action_done()
            self.assertIs(self._available(), expected)
            self.assertGreaterEqual(self._process().call_count, 1)

    def test_branch_transfer_notifies_both_affected_branches(self):
        self._add(1)
        self._mark()
        self._add(-1)
        self._add(1, self.location_b)
        self.assertFalse(self._available())
        self.assertTrue(self._available(self.other_config))
        self.assertEqual(set(self._new_logs().config_id.ids), {self.config.id, self.other_config.id})
        self.assertEqual(self._process().call_count, 2)

    def test_stock_take_deletion_and_inbound_context(self):
        self._add(3)
        quant = self.quant.search([
            ("product_id", "=", self.product.product_variant_id.id), ("location_id", "=", self.location_a.id),
        ], limit=1)
        self._mark()
        quant.with_context(inventory_mode=True, blend_inbound_sync=True).write({"inventory_quantity": 0})
        quant.with_context(blend_inbound_sync=True).action_apply_inventory()
        self.assertFalse(self._available())
        self.assertGreaterEqual(self._process().call_count, 1)
        self._mark()
        self._add(2)
        self.assertEqual(self._process().call_count, 1)
        self._mark()
        quant.sudo().unlink()
        self.assertFalse(self._available())
        self.assertEqual(self._process().call_count, 1)

    def test_foreign_company_and_unrelated_locations_do_not_affect_branch(self):
        company = self.env["res.company"].create({"name": "Blend foreign stock company"})
        location = self.env["stock.location"].create({
            "name": "Foreign stock", "usage": "internal", "company_id": company.id,
        })
        self.product.company_id = False
        self._mark()
        self._add(10, location)
        self.assertFalse(self._available())
        self.assertFalse(self._new_logs())
        self._add(10, self.env.ref("stock.stock_location_customers"))
        self.assertFalse(self._available())
        self.assertFalse(self._new_logs())

    def test_no_global_stock_fallback_for_missing_branch_source(self):
        self._add(5, self.location_b)
        self.config.picking_type_id.default_location_src_id = False
        self.assertFalse(self._available())

    def test_modifier_stock_invalidates_parent_menu_product(self):
        option = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Tracked topping", "type": "consu", "is_storable": True,
            "available_in_pos": True, "use_as_modifier": True,
        }).with_env(self.env)
        group = self.env["modifiers.groups"].with_context(blend_inbound_sync=True).create({
            "name": "Stock toppings", "modifier_ids": [Command.set(option.ids)],
        })
        self.product.with_context(blend_inbound_sync=True).modifier_grp_ids = [Command.set(group.ids)]
        self._mark()
        self._add(1, product=option)
        self.assertEqual(self._new_logs().mapped("external_reference"), [str(self.product.id)])
        self.assertEqual(self._process().call_count, 1)

    def test_stock_user_keeps_permissions_and_unauthorized_write_has_no_outbox(self):
        user = new_test_user(self.env, login="blend_stock_operator", groups="stock.group_stock_user")
        with patch.object(outbound.requests, "post") as send:
            self.quant.with_user(user)._update_available_quantity(self.product.product_variant_id, self.location_a, 1)
        send.assert_not_called()
        self.assertTrue(self._new_logs())
        self._mark()
        portal = new_test_user(self.env, login="blend_stock_portal", groups="base.group_portal")
        quant = self.quant.search([("product_id", "=", self.product.product_variant_id.id)], limit=1)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            quant.with_user(portal).write({"quantity": 0})
        self.assertFalse(self._new_logs())

    def test_retries_use_existing_queue_and_never_send_internal_metadata(self):
        self._add(1)
        log = self._new_logs()
        with patch.object(outbound.requests, "post", side_effect=requests.exceptions.Timeout("Timeout")):
            self.assertFalse(log._process_outbound())
        self.assertEqual(log.state, "retry")
        self.assertTrue(log.next_retry_at)
        self._add(-1)
        # An attempted delivery may already have reached Blend. Retry even if
        # stock has since reverted so Blend is prompted to read the latest menu.
        send = self._process(log)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(set(send.call_args.kwargs["json"]), {"brand_id", "event_time", "event", "entity"})
        self.assertEqual(log.state, "success")

    def test_disabled_branch_missing_url_and_non_menu_products_are_ignored(self):
        self.other_config.blend_enabled = False
        self.config.blend_event_url = False
        self._add(1)
        self.assertFalse(self._new_logs())
        self.config.blend_event_url = "https://blend.example.test/events"
        self.product.with_context(blend_inbound_sync=True).available_in_pos = False
        self._add(-1)
        self.assertFalse(self._new_logs())

    def _menu_branch_stock(self, product=None):
        product = product or self.product
        menu = BlendPOSAPIService(self.env, self.config | self.other_config).get_menu(
            filters={"product_id": str(product.id)},
        )
        row = next(row for category in menu["categories"] for row in category["products"])
        result = {branch["branch_id"]: branch["is_stock"] for branch in row["available_branches"]}
        self.assertTrue(all(type(value) is bool for value in result.values()))
        return result

    def test_depletion_below_zero_and_restock_publish_latest_menu_booleans(self):
        self._add(2)
        self._add(5, self.location_b)
        self._process()
        for delta, available, sends in ((-2, False, 1), (-3, False, 0), (3, False, 0), (1, True, 1)):
            with self.subTest(delta=delta):
                self._mark()
                self._add(delta)
                self.assertEqual(self._menu_branch_stock(), {"95001": available, "95002": True})
                self.assertEqual(self._process().call_count, sends)

    def test_positive_to_negative_crossing_sends_out_of_stock_invalidation(self):
        self._add(1)
        self._process()
        self._mark()
        self._add(-4)
        self.assertFalse(self._menu_branch_stock()["95001"])
        send = self._process()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0], self.config.blend_event_url)
        self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Bearer %s" % self.outbound_token)
        self.assertEqual(send.call_args.kwargs["json"]["brand_id"], "95000")

    def test_split_quants_and_reserved_stock_use_total_branch_balance(self):
        self._add(2)
        self._add(3, self.child)
        self._process()
        self._mark()
        self.quant._update_reserved_quantity(self.product.product_variant_id, self.location_a, 2)
        self.quant._update_reserved_quantity(self.product.product_variant_id, self.child, 3)
        self.assertFalse(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)
        self._mark()
        self.quant._update_reserved_quantity(self.product.product_variant_id, self.child, -1)
        self.assertTrue(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)

    def test_negative_quant_offsets_positive_shelf_stock(self):
        self._add(2, self.child)
        self._process()
        self._mark()
        self._add(-3)
        self.assertFalse(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)

    def test_native_reservation_and_cancellation_restore_menu_availability(self):
        self._add(2)
        self._process()
        move = self.env["stock.move"].create({
            "product_id": self.product.product_variant_id.id, "product_uom_qty": 2,
            "product_uom": self.product.uom_id.id,
            "location_id": self.location_a.id,
            "location_dest_id": self.env.ref("stock.stock_location_customers").id,
            "company_id": self.env.company.id,
        })
        move._action_confirm()
        self._mark()
        move._action_assign()
        self.assertEqual(move.state, "assigned")
        self.assertFalse(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)
        self._mark()
        move._action_cancel()
        self.assertTrue(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)

    def _create_stock_pos_order(self, *, at_closing=False):
        self.env.company.point_of_sale_update_stock_quantities = "closing" if at_closing else "real"
        session = self.env["pos.session"].create({"config_id": self.config.id})
        session.set_opening_control(0, "")
        return self.env["pos.order"].create({
            "session_id": session.id, "amount_tax": 0, "amount_total": 10,
            "amount_paid": 10, "amount_return": 0,
            "lines": [Command.create({
                "product_id": self.product.product_variant_id.id, "qty": 1,
                "price_unit": 10, "price_subtotal": 10, "price_subtotal_incl": 10,
                "tax_ids": [Command.clear()],
            })],
        })

    def test_native_pos_realtime_picking_depletes_stock_and_notifies(self):
        self._add(1)
        self._process()
        order = self._create_stock_pos_order()
        self._mark()
        with patch.object(outbound.requests, "post") as send:
            order._create_order_picking()
        send.assert_not_called()
        self.assertTrue(order.picking_ids)
        self.assertEqual(set(order.picking_ids.mapped("state")), {"done"})
        self.assertFalse(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)

    def test_native_pos_deferred_stock_waits_for_session_picking(self):
        self._add(1)
        self._process()
        order = self._create_stock_pos_order(at_closing=True)
        self._mark()
        order._create_order_picking()
        self.assertFalse(order.picking_ids)
        self.assertTrue(self._menu_branch_stock()["95001"])
        self.assertFalse(self._new_logs())
        order.with_context(blend_skip_status_sync=True).write({"state": "paid"})
        order.session_id._create_picking_at_end_of_session()
        self.assertFalse(self._menu_branch_stock()["95001"])
        self.assertEqual(self._process().call_count, 1)

    def test_stock_outbox_is_invisible_to_other_transactions_before_commit(self):
        self._add(1)
        log = self._new_logs()
        self.assertTrue(log)
        self.env.flush_all()
        # A real, independent transaction cannot deliver uncommitted stock jobs.
        with self.registry.cursor() as other_cr:
            other_cr.execute("SELECT id FROM blend_integration_log WHERE id = ANY(%s)", [log.ids])
            self.assertFalse(other_cr.fetchall())

    def test_cron_sends_depletion_and_does_not_resend_completed_stock_jobs(self):
        self._add(1)
        self._process()
        self._mark()
        self._add(-1)
        logs = self._new_logs()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as send:
            self.logs._cron_process_outbound()
            self.logs._cron_process_outbound()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(set(logs.mapped("state")), {"success"})
        self.assertFalse(self._menu_branch_stock()["95001"])

    def test_depletion_http_failures_exhaust_retries_and_manual_retry_recovers(self):
        self._add(1)
        self._process()
        self._mark()
        self._add(-1)
        log = self._new_logs()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(503, {"status": "failed"})) as send:
            for attempt in range(1, self.config.blend_retry_max + 1):
                self.assertFalse(log._process_outbound())
                self.assertEqual(log.attempt_count, attempt)
                expected = "failed" if attempt == self.config.blend_retry_max else "retry"
                self.assertEqual(log.state, expected)
            self.logs._cron_process_outbound()
        self.assertEqual(send.call_count, self.config.blend_retry_max)
        self.assertFalse(log.next_retry_at)
        self.assertFalse(self._menu_branch_stock()["95001"])
        log.action_retry()
        self.assertEqual(log.state, "pending")
        self.assertEqual(self._process(log).call_count, 1)
        self.assertEqual(log.state, "success")

    def test_cron_honors_retry_time_and_recovers_interrupted_stock_delivery(self):
        self._add(1)
        self._process()
        self._mark()
        self._add(-1)
        log = self._new_logs()
        with patch.object(outbound.requests, "post", side_effect=requests.exceptions.ConnectionError("Offline")):
            log._process_outbound()
        log.next_retry_at = fields.Datetime.now() + timedelta(hours=1)
        with patch.object(outbound.requests, "post") as send:
            self.logs._cron_process_outbound()
        send.assert_not_called()
        log.write({
            "state": "processing", "last_attempt_at": fields.Datetime.now() - timedelta(minutes=11),
        })
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as send:
            self.logs._cron_process_outbound()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(log.state, "success")

    def test_shared_modifier_notifies_every_parent_when_stock_reaches_zero(self):
        option = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Shared tracked topping", "type": "consu", "is_storable": True,
            "available_in_pos": True, "use_as_modifier": True,
        }).with_env(self.env)
        group = self.env["modifiers.groups"].with_context(blend_inbound_sync=True).create({
            "name": "Shared toppings", "modifier_ids": [Command.set(option.ids)],
        })
        parent = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Second menu parent", "available_in_pos": True,
            "modifier_grp_ids": [Command.set(group.ids)],
        })
        self.product.with_context(blend_inbound_sync=True).modifier_grp_ids = [Command.set(group.ids)]
        self._add(1, product=option)
        self._process()
        self._mark()
        self._add(-1, product=option)
        self.assertEqual(set(self._new_logs().mapped("external_reference")), {str(self.product.id), str(parent.id)})
        self.assertEqual(self._process().call_count, 2)

    def test_regression_duplicate_api_ids_must_not_mask_out_of_stock(self):
        # A native template ID and an imported Blend ID share one API namespace.
        # Ensure the second product cannot lend its availability to the first.
        self._add(1)
        other = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Different available imported product", "available_in_pos": True,
            "type": "consu", "is_storable": False,
            "blend_external_id": str(self.product.id), "blend_config_id": self.config.id,
            "pos_sequence": self.product.pos_sequence + 1,
        })
        self._process()
        self._mark()
        self._add(-1)
        self.assertFalse(self._available())
        self.assertTrue(self._available(product=other))
        self.assertEqual(self._process().call_count, 0)
        self.assertEqual(set(self._new_logs().mapped("state")), {"retry"})
        with self.assertRaisesRegex(BlendAPIError, "Ambiguous Blend product ID"):
            BlendPOSAPIService(self.env, self.config).get_menu(filters={"product_id": str(self.product.id)})
