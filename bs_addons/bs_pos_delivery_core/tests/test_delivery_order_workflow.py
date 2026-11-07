from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestDeliveryOrderWorkflow(TransactionCase):
    """Regression coverage for the provider-neutral operational workflow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["bs.delivery.provider"].create(
            {
                "name": "Workflow Provider",
                "code": "workflow_provider",
                "connector_kind": "aggregator",
            }
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "Workflow Connection",
                "provider_id": cls.provider.id,
                "commission_pct": 20.0,
            }
        )

    def _create_order(self, suffix="1", **extra):
        values = {
            "connection_id": self.connection.id,
            "external_order_id": "WORKFLOW-%s" % suffix,
            "external_reference": "REMOTE-%s" % suffix,
            "currency_id": self.env.company.currency_id.id,
            "items_subtotal": 90.0,
            "vat_amount": 10.0,
            "amount_total": 100.0,
            "commission_rate": 20.0,
            "commission_amount": 20.0,
            "net_amount": 80.0,
            "customer_name": "Workflow Customer",
        }
        values.update(extra)
        order = (
            self.env["bs.delivery.order"]
            ._with_delivery_internal_update()
            .create(values)
        )
        return self.env["bs.delivery.order"].browse(order.id)

    def test_branch_preset_follows_fulfillment_type(self):
        Preset = self.env["pos.preset"]
        delivery = Preset.create({"name": "Workflow Delivery"})
        pickup = Preset.create({"name": "Workflow Pickup"})
        not_on_branch = Preset.create({"name": "Workflow Other Branch"})
        config = self.env["pos.config"].create({
            "name": "Preset Mapping POS",
            "use_presets": True,
            "default_preset_id": delivery.id,
            "available_preset_ids": [Command.set([delivery.id, pickup.id])],
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "PRESET-STORE",
            "delivery_preset_id": delivery.id,
            "pickup_preset_id": pickup.id,
        })
        delivery_order = self._create_order(
            "preset-delivery", store_binding_id=store.id, fulfillment_type="delivery",
        )
        pickup_order = self._create_order(
            "preset-pickup", store_binding_id=store.id, fulfillment_type="pickup",
        )
        dine_in_order = self._create_order(
            "preset-dine-in", store_binding_id=store.id, fulfillment_type="dine_in",
        )
        self.assertEqual(delivery_order._pos_order_preset(store), delivery)
        self.assertEqual(pickup_order._pos_order_preset(store), pickup)
        # No Dine In preset on the branch: platform orders fall back to Delivery.
        self.assertEqual(dine_in_order._pos_order_preset(store), delivery)
        store.pickup_preset_id = False
        self.assertEqual(pickup_order._pos_order_preset(store), delivery)
        with self.assertRaises(ValidationError):
            store.dine_in_preset_id = not_on_branch

    def test_delivery_order_finds_branch_delivery_preset_when_none_mapped(self):
        """No preset on the mapping: never fall back to the POS "Dine In"."""
        Preset = self.env["pos.preset"]
        dine_in = Preset.create({"name": "Dine In"})
        delivery = Preset.create({"name": "Delivery", "identification": "address"})
        config = self.env["pos.config"].create({
            "name": "Auto Preset POS",
            "use_presets": True,
            "default_preset_id": dine_in.id,
            "available_preset_ids": [Command.set([dine_in.id, delivery.id])],
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "AUTO-PRESET-STORE",
        })
        order = self._create_order(
            "auto-preset", store_binding_id=store.id, fulfillment_type="delivery",
        )
        self.assertEqual(store._auto_delivery_preset(), delivery)
        self.assertEqual(order._pos_order_preset(store), delivery)
        # Found by name too ("توصيل"), when no preset asks for an address.
        delivery.identification = "none"
        delivery.name = "توصيل"
        self.assertEqual(order._pos_order_preset(store), delivery)
        # A configured mapping always wins.
        store.delivery_preset_id = dine_in
        self.assertEqual(order._pos_order_preset(store), dine_in)

    def test_force_send_is_offered_only_by_provider_addons(self):
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="generic:create_order",
            request_method="POST", request_url="/generic/orders", request_payload={},
        )
        log.mark_failed("Rejected", http_status=422)
        self.assertFalse(log.can_force_send)
        with self.assertRaises(UserError):
            log.action_force_send_to_branch()

    def test_canonical_order_creates_compatibility_binding(self):
        order = self._create_order()

        self.assertTrue(order.name and order.name != "New")
        self.assertEqual(order.binding_id.connection_id, self.connection)
        self.assertEqual(order.binding_id.external_order_id, order.external_order_id)
        self.assertEqual(order.binding_id.odoo_status, "pending")

    def test_state_machine_is_audited_and_idempotent(self):
        order = self._create_order("state")

        # Isolate transition auditing from the separate paid-POS acceptance gate.
        with patch.object(type(order), "_before_delivery_state_transition", return_value=None):
            transition = order.transition_to(
                "accepted",
                source="webhook",
                expected_state="received",
                idempotency_key="accept-once",
            )
        duplicate = order.transition_to(
            "accepted",
            source="webhook",
            idempotency_key="accept-once",
        )

        self.assertEqual(duplicate, transition)
        self.assertEqual(order.state, "accepted")
        self.assertTrue(order.accepted_at)
        self.assertEqual(order.transition_ids, transition)
        self.assertEqual(order.binding_id.odoo_status, "accepted")
        self.assertEqual(order.sync_state, "not_required")
        self.assertEqual(order.pos_processing_state, "blocked")

        with self.assertRaises(AccessError):
            order.write({"state": "delivered"})
        with self.assertRaises(ValidationError):
            order.transition_to("delivered", expected_state="accepted")

    def test_rejection_requires_a_reason_and_is_terminal(self):
        order = self._create_order("reject")

        with self.assertRaises(ValidationError):
            order.transition_to("rejected", idempotency_key="reject-empty")

        order.transition_to(
            "rejected",
            reason="Store is at capacity",
            idempotency_key="reject-with-reason",
        )
        self.assertEqual(order.state, "rejected")
        self.assertEqual(order.rejection_note, "Store is at capacity")
        with self.assertRaises(ValidationError):
            order.transition_to("accepted", idempotency_key="revive-rejected")

    def test_late_search_matches_runtime_indicator(self):
        late = self._create_order(
            "late",
            received_at=fields.Datetime.now() - timedelta(minutes=30),
            late_threshold_minutes=15,
        )
        current = self._create_order(
            "current",
            received_at=fields.Datetime.now(),
            late_threshold_minutes=15,
        )

        found = self.env["bs.delivery.order"].search(
            [("id", "in", (late.id, current.id)), ("is_late", "=", True)]
        )

        self.assertEqual(found, late)
        self.assertTrue(late.is_late)
        self.assertFalse(current.is_late)

    def test_dashboard_reads_canonical_orders(self):
        self._create_order("dashboard")

        data = self.env["bs.delivery.dashboard"].get_dashboard_data({})

        self.assertGreaterEqual(data["kpis"]["today_orders"], 1)
        self.assertGreaterEqual(data["kpis"]["today_gmv"], 100.0)
        self.assertIn("state_breakdown", data)
        self.assertIn("queue_health", data)
        self.assertTrue(any(item["state"] == "received" for item in data["state_breakdown"]))

    def test_small_order_fee_cannot_be_negative(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._create_order("negative-small-order-fee", small_order_fee=-0.01)

    def test_tip_amount_cannot_be_negative(self):
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._create_order("negative-tip", tip_amount=-0.01)

    def test_dashboard_only_materializes_the_bounded_recent_order_list(self):
        for index in range(12):
            self._create_order(
                "dashboard-bounded-%s" % index,
                state="delivered",
                amount_total=10.0,
                commission_amount=2.0,
                net_amount=8.0,
            )
        order_model = self.env["bs.delivery.order"]
        original_search = type(order_model).search

        def guarded_search(records, *args, **kwargs):
            if records._name == "bs.delivery.order" and not kwargs.get("limit"):
                raise AssertionError(
                    "The dashboard must aggregate orders instead of loading an unbounded set."
                )
            return original_search(records, *args, **kwargs)

        with patch.object(type(order_model), "search", new=guarded_search):
            data = self.env["bs.delivery.dashboard"].get_dashboard_data({})

        self.assertEqual(len(data["recent_orders"]), 12)
        self.assertGreaterEqual(data["kpis"]["today_gmv"], 120.0)

    def test_delivery_customer_is_resolved_without_name_only_collision(self):
        existing = self.env["res.partner"].create(
            {"name": "Shared Delivery Name", "email": "other@example.test"}
        )
        order = self._create_order(
            "customer-partner",
            customer_name="Shared Delivery Name",
            customer_phone="0500000123",
            customer_email="CUSTOMER@EXAMPLE.TEST",
            delivery_address="Riyadh delivery address",
        )

        partner = order._resolve_or_create_customer_partner()
        same_partner = order._resolve_or_create_customer_partner()

        self.assertNotEqual(partner, existing)
        self.assertEqual(same_partner, partner)
        self.assertEqual(partner.email, "customer@example.test")
        self.assertEqual(partner.phone, "0500000123")
        self.assertEqual(partner.street, "Riyadh delivery address")
        self.assertEqual(partner.company_id, self.env.company)

    def test_legacy_mirror_preserves_mapping_discount_and_delivery_fee(self):
        config = self.env["pos.config"].create(
            {"name": "Legacy Mirror POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        products = self.env["product.product"].create(
            [
                {"name": "Legacy Platform Item", "default_code": "LOCAL-SKU"},
                {"name": "Delivery Fee Product"},
            ]
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "LEGACY-MIRROR-STORE",
                "delivery_fee_product_id": products[1].id,
            }
        )
        self.env["bs.delivery.product.binding"].create(
            {
                "connection_id": self.connection.id,
                "store_binding_id": store.id,
                "product_id": products[0].id,
                "external_product_id": "PLATFORM-SKU-100",
                "external_variant_id": "PLATFORM-VARIANT-1",
                "external_name_en": "Platform Item",
                "external_name_ar": "صنف المنصة",
                "platform_price": 50.0,
            }
        )
        pos_order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "company_id": self.env.company.id,
                "pricelist_id": config.pricelist_id.id,
                "amount_total": 110.0,
                "amount_tax": 10.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
                "lines": [
                    Command.create(
                        {
                            "product_id": products[0].id,
                            "name": products[0].display_name,
                            "full_product_name": products[0].display_name,
                            "qty": 2.0,
                            "price_unit": 50.0,
                            "discount": 10.0,
                            "price_subtotal": 90.0,
                            "price_subtotal_incl": 100.0,
                            "tax_ids": [Command.clear()],
                        }
                    ),
                    Command.create(
                        {
                            "product_id": products[1].id,
                            "name": "Delivery Fee",
                            "full_product_name": "Delivery Fee",
                            "qty": 1.0,
                            "price_unit": 10.0,
                            "price_subtotal": 10.0,
                            "price_subtotal_incl": 10.0,
                            "tax_ids": [Command.clear()],
                        }
                    ),
                ],
            }
        )

        financial = self.env["bs.delivery.order"]._legacy_financial_values(
            pos_order, self.connection, store
        )
        commands = self.env["bs.delivery.order"]._legacy_line_commands(
            pos_order,
            connection=self.connection,
            store=store,
        )
        mirror = self._create_order(
            "legacy-line-target",
            store_binding_id=store.id,
            line_ids=commands,
            **financial,
        )

        self.assertEqual(mirror.items_subtotal, 100.0)
        self.assertEqual(mirror.discount_amount, 10.0)
        self.assertEqual(mirror.delivery_fee, 10.0)
        self.assertEqual(mirror.vat_amount, 10.0)
        self.assertEqual(mirror.amount_total, 110.0)
        self.assertEqual(len(mirror.line_ids), 1)
        self.assertEqual(mirror.line_ids.external_sku, "PLATFORM-SKU-100")
        self.assertEqual(mirror.line_ids.external_variant_id, "PLATFORM-VARIANT-1")
        self.assertEqual(mirror.line_ids.name, "Platform Item")
        self.assertEqual(mirror.line_ids.name_ar, "صنف المنصة")

    def test_pos_realtime_uses_the_private_config_channel(self):
        config = self.env["pos.config"].create(
            {"name": "Delivery Realtime POS", "module_pos_restaurant": False}
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "REALTIME-STORE",
            }
        )
        order = self._create_order("realtime", store_binding_id=store.id)

        with patch.object(type(config), "_notify", autospec=True) as notify:
            order._notify_pos_board("new_order")

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], config)
        self.assertEqual(notify.call_args.args[1], "BS_DELIVERY_POS_BOARD")
        self.assertEqual(notify.call_args.args[2]["order_id"], order.id)

    def test_pos_board_uses_connected_routes_and_canonical_reject_reason(self):
        config = self.env["pos.config"].create(
            {"name": "Delivery Board POS", "module_pos_restaurant": False}
        )
        self.connection.write(
            {
                "state": "connected",
                "pos_config_ids": [Command.link(config.id)],
            }
        )
        stores = self.env["bs.delivery.store.binding"].create(
            [
                {
                    "connection_id": self.connection.id,
                    "pos_config_id": config.id,
                    "remote_store_id": "BOARD-STORE-A",
                    "sound_alert": False,
                },
                {
                    "connection_id": self.connection.id,
                    "pos_config_id": config.id,
                    "remote_store_id": "BOARD-STORE-B",
                    "paused": True,
                    "sound_alert": False,
                },
            ]
        )
        reason = self.env["bs.delivery.reject.reason"].create(
            {
                "name": "Board Capacity",
                "code": "board_capacity",
                "provider_id": self.provider.id,
                "external_code": "REMOTE_CAPACITY",
            }
        )
        order = self._create_order(
            "board-rpc",
            store_binding_id=stores[0].id,
            customer_phone="0501234500",
            delivery_address="Riyadh, King Fahd Road",
            driver_name="Delivery Driver",
            driver_phone="0509876500",
            driver_eta_minutes=12,
            fulfillment_type="delivery",
        )

        badges = self.env["bs.delivery.provider"].get_pos_provider_badges(
            config.id
        )
        provider_badge = next(
            item for item in badges["providers"] if item["id"] == self.provider.id
        )
        self.assertEqual(provider_badge["received_count"], 1)
        self.assertFalse(provider_badge["paused"])
        self.assertTrue(provider_badge["partially_paused"])
        self.assertFalse(provider_badge["sound_alert"])
        self.assertEqual(badges["polling_interval_minutes"], 2)
        self.assertEqual(
            badges["recent_received_orders"][0]["order_id"], order.id
        )

        board = self.env["bs.delivery.order"].get_pos_board_data(
            self.provider.id, config.id
        )
        self.assertIn(reason.id, [item["id"] for item in board["rejection_reasons"]])
        self.assertEqual(board["provider"]["polling_interval_minutes"], 2)
        order_values = board["orders"][0]
        self.assertEqual(order_values["customer_phone_masked"], "******4500")
        self.assertEqual(order_values["delivery_address"], "Riyadh, King Fahd Road")
        self.assertEqual(order_values["driver_phone"], "0509876500")
        self.assertEqual(order_values["driver_eta_minutes"], 12)
        forbidden_keys = {
            "api_key",
            "api_secret",
            "access_token",
            "refresh_token",
            "password",
            "webhook_secret",
        }

        def collect_keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(
                    *(collect_keys(item) for item in value.values()), set()
                )
            if isinstance(value, (list, tuple)):
                return set().union(*(collect_keys(item) for item in value), set())
            return set()

        self.assertFalse(collect_keys(badges) & forbidden_keys)
        self.assertFalse(collect_keys(board) & forbidden_keys)

        self.env["bs.delivery.order"].action_pos_transition(
            order.id,
            "rejected",
            reject_reason_id=reason.id,
            provider_id=self.provider.id,
            pos_config_id=config.id,
        )
        self.assertEqual(order.reject_reason_id, reason)
        self.assertEqual(order.transition_ids[-1].reject_reason_id, reason)

    def test_pos_badges_show_installed_provider_without_connected_route(self):
        config = self.env["pos.config"].create({
            "name": "Unconnected Provider POS",
            "module_pos_restaurant": False,
        })
        module = self.env["ir.module.module"].search([
            ("name", "=", "bs_pos_delivery_core"),
            ("state", "=", "installed"),
        ], limit=1)
        self.assertTrue(module)
        installed_provider = self.env["bs.delivery.provider"].create({
            "name": "Installed Provider",
            "code": "installed_provider_badge_test",
            "module_id": module.id,
        })
        self.connection.write({
            "state": "connected",
            "pos_config_ids": [Command.link(config.id)],
        })

        badges = self.env["bs.delivery.provider"].get_pos_provider_badges(config.id)
        provider_badge = next(
            item for item in badges["providers"] if item["id"] == installed_provider.id
        )
        self.assertFalse(provider_badge["connected"])
        self.assertEqual(provider_badge["open_count"], 0)
        connected_badge = next(
            item for item in badges["providers"] if item["id"] == self.provider.id
        )
        self.assertTrue(connected_badge["connected"])
        self.assertEqual(
            self.env["bs.delivery.order"].get_pos_board_data(
                installed_provider.id, config.id
            )["orders"],
            [],
        )

    def test_pos_user_cannot_operate_another_cashiers_branch(self):
        config = self.env["pos.config"].create(
            {"name": "Protected Delivery POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        cashier = new_test_user(
            self.env,
            login="delivery_branch_cashier",
            groups="point_of_sale.group_pos_user",
            company_id=self.env.company.id,
        )

        with self.assertRaises(AccessError):
            self.env["bs.delivery.order"].with_user(cashier).get_pos_board_data(
                self.provider.id, config.id
            )
        with self.assertRaises(AccessError):
            self.env["bs.delivery.provider"].with_user(
                cashier
            ).get_pos_provider_badges(config.id)

        session.write({"user_id": cashier.id})
        config.invalidate_recordset(["current_session_id", "current_user_id"])
        board = self.env["bs.delivery.order"].with_user(cashier).get_pos_board_data(
            self.provider.id, config.id
        )
        self.assertIn("orders", board)

    def test_feedus_uses_the_generic_kitchen_print_queue(self):
        provider = self.env["bs.delivery.provider"].search(
            [("code", "=", "feedus")], limit=1
        ) or self.env["bs.delivery.provider"].create(
            {
                "name": "FeedUs Print Regression",
                "code": "feedus",
                "connector_kind": "aggregator",
            }
        )
        connection = self.env["bs.delivery.connection"].create(
            {
                "name": "FeedUs Print Regression",
                "provider_id": provider.id,
                "auto_print_kitchen": True,
            }
        )
        config = self.env["pos.config"].create(
            {"name": "FeedUs Generic Print POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "company_id": self.env.company.id,
                "pricelist_id": config.pricelist_id.id,
                "amount_total": 0.0,
                "amount_tax": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": connection.id,
                "pos_config_id": config.id,
                "remote_store_id": str(config.id),
            }
        )
        order = (
            self.env["bs.delivery.order"]
            ._with_delivery_internal_update()
            .create(
                {
                    "connection_id": connection.id,
                    "store_binding_id": store.id,
                    "external_order_id": "FEEDUS-PRINT-1",
                    "currency_id": self.env.company.currency_id.id,
                    "state": "accepted",
                    "pos_order_id": pos_order.id,
                    "kitchen_print_state": "pending",
                    "amount_total": 0.0,
                }
            )
        )

        self.assertTrue(order._kitchen_autoprint_enabled())
        jobs = self.env["bs.delivery.order"].get_pending_kitchen_prints(config.id)
        self.assertIn(order.id, [job["order_id"] for job in jobs])

    def test_kitchen_print_job_is_available_immediately(self):
        """Regression: a freshly requested ticket must not wait for a poll.

        Dispatching used to stamp kitchen_print_last_attempt_at, which hid the
        job from get_pending_kitchen_prints() for 30 seconds, so the notified
        POS found nothing and the ticket printed about a minute late.
        """
        self.connection.auto_print_kitchen = True
        config = self.env["pos.config"].create(
            {"name": "Instant Print POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.env.company.id,
            "pricelist_id": config.pricelist_id.id,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "INSTANT-PRINT",
        })
        order = self._create_order(
            "instant-print", state="accepted", store_binding_id=store.id,
            pos_order_id=pos_order.id,
        )
        with patch.object(type(config), "notify_synchronisation", autospec=True), \
                patch.object(type(config), "_notify", autospec=True) as notify:
            order._request_kitchen_autoprint()
            self.assertEqual(order.kitchen_print_state, "pending")
            self.assertFalse(order.kitchen_print_last_attempt_at)
            # The realtime message carries the full job for a direct print.
            print_payloads = [
                call.args[2] for call in notify.call_args_list
                if call.args[1] == "BS_DELIVERY_POS_BOARD"
                and call.args[2].get("event") == "kitchen_auto_print"
            ]
            self.assertTrue(print_payloads)
            self.assertEqual(print_payloads[-1]["kitchen_job"]["order_id"], order.id)
            self.assertEqual(
                print_payloads[-1]["kitchen_job"]["pos_order_id"], pos_order.id,
            )
            notify.reset_mock()
            # No backdating: the job is pending and printable right now.
            jobs = order.get_pending_kitchen_prints(config.id)
            self.assertEqual([job["order_id"] for job in jobs], [order.id])
            # Polling must not re-broadcast (no notification storm).
            self.assertFalse([
                call for call in notify.call_args_list
                if call.args[2].get("event") == "kitchen_auto_print"
            ])
            claim = "instant-print-claim-key-0001"
            self.assertTrue(order.claim_kitchen_print(order.id, config.id, claim))
            self.assertTrue(order.kitchen_print_last_attempt_at)
            # Once claimed, no other device can get or claim the same ticket.
            self.assertEqual(order.get_pending_kitchen_prints(config.id), [])
            self.assertFalse(order.claim_kitchen_print(
                order.id, config.id, "instant-print-second-device",
            ))
            order.acknowledge_kitchen_print(order.id, config.id, claim, True)
            self.assertEqual(order.kitchen_print_state, "sent")
            self.assertEqual(order.get_pending_kitchen_prints(config.id), [])

    def test_kitchen_print_failure_is_not_retried_by_status_or_poll(self):
        self.connection.auto_print_kitchen = True
        config = self.env["pos.config"].create(
            {"name": "Print Once POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.env.company.id,
            "pricelist_id": config.pricelist_id.id,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "PRINT-ONCE",
        })
        order = self._create_order(
            "print-once", state="accepted", store_binding_id=store.id,
            pos_order_id=pos_order.id,
        )
        with patch.object(type(config), "notify_synchronisation", autospec=True), \
                patch.object(type(config), "_notify", autospec=True):
            order._request_kitchen_autoprint()
            self.assertEqual(order.kitchen_print_state, "pending")
            first_claim = "print-once-first-claim"
            self.assertTrue(order.claim_kitchen_print(order.id, config.id, first_claim))
            self.assertEqual(order.kitchen_print_state, "attempting")
            order.sudo()._with_delivery_internal_update().write({
                "kitchen_print_claim_until": fields.Datetime.subtract(
                    fields.Datetime.now(), seconds=1,
                ),
                "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                    fields.Datetime.now(), seconds=31,
                ),
            })
            self.assertNotIn(order.id, [
                job["order_id"] for job in order.get_pending_kitchen_prints(config.id)
            ])
            self.assertFalse(order.claim_kitchen_print(
                order.id, config.id, "print-once-second-device-claim",
            ))
            order.acknowledge_kitchen_print(
                order.id, config.id, first_claim, False, "Printer unavailable",
            )
            self.assertEqual(order.kitchen_print_state, "error")
            order._request_kitchen_autoprint()
            self.assertEqual(order.kitchen_print_state, "error")
            order.sudo()._with_delivery_internal_update().write({
                "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                    fields.Datetime.now(), seconds=31,
                ),
            })
            self.assertNotIn(order.id, [
                job["order_id"] for job in order.get_pending_kitchen_prints(config.id)
            ])
            self.assertEqual(order.kitchen_print_attempts, 1)

            operator = new_test_user(
                self.env, login="print_once_operator",
                groups="bs_pos_delivery_core.group_delivery_operator",
            )
            with self.assertRaises(AccessError):
                order.with_user(operator).action_retry_kitchen_print()
            order.action_retry_kitchen_print()
            self.assertEqual(order.kitchen_print_state, "pending")
            self.assertTrue(order.kitchen_manual_retry_requested)
            self.assertIn(order.id, [
                job["order_id"] for job in order.get_pending_kitchen_prints(config.id)
            ])
            retry_claim = "print-once-manual-retry-claim"
            self.assertTrue(order.claim_kitchen_print(order.id, config.id, retry_claim))
            self.assertEqual(order.kitchen_print_attempts, 2)
            order.acknowledge_kitchen_print(order.id, config.id, retry_claim, True)
            self.assertEqual(order.kitchen_print_state, "sent")
            order._request_kitchen_autoprint()
            self.assertNotIn(order.id, [
                job["order_id"] for job in order.get_pending_kitchen_prints(config.id)
            ])

    def test_previous_pending_kitchen_attempt_needs_manual_retry(self):
        self.connection.auto_print_kitchen = True
        config = self.env["pos.config"].create({
            "name": "Previous Kitchen Attempt POS", "module_pos_restaurant": False,
        })
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.env.company.id,
            "pricelist_id": config.pricelist_id.id,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "PREVIOUS-PRINT-ATTEMPT",
        })
        order = self._create_order(
            "previous-print-attempt", state="accepted", store_binding_id=store.id,
            pos_order_id=pos_order.id, kitchen_print_state="pending",
            kitchen_print_attempts=1,
        )
        with patch.object(type(config), "notify_synchronisation", autospec=True), \
                patch.object(type(config), "_notify", autospec=True):
            self.assertTrue(order.kitchen_can_retry)
            self.assertEqual(order.get_pending_kitchen_prints(config.id), [])
            order._request_kitchen_autoprint()
            self.assertEqual(order.get_pending_kitchen_prints(config.id), [])
            order.action_retry_kitchen_print()
            self.assertFalse(order.kitchen_can_retry)
            self.assertIn(order.id, [
                job["order_id"] for job in order.get_pending_kitchen_prints(config.id)
            ])

    def test_stale_kitchen_jobs_need_review_before_printing(self):
        self.connection.auto_print_kitchen = True
        config = self.env["pos.config"].create({
            "name": "Stale Kitchen Print POS", "module_pos_restaurant": False,
        })
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.env.company.id,
            "pricelist_id": config.pricelist_id.id,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "STALE-PRINT-STORE",
        })
        now = fields.Datetime.now()

        def create_pending(suffix, minutes_old):
            age = fields.Datetime.subtract(now, minutes=minutes_old)
            return self._create_order(
                suffix, state="accepted", store_binding_id=store.id,
                pos_order_id=pos_order.id, kitchen_print_state="pending",
                accepted_at=age, kitchen_print_requested_at=age,
                kitchen_print_last_attempt_at=age,
            )

        stale = create_pending("stale-print", 16)
        recent = create_pending("recent-print", 2)
        direct_claim = create_pending("stale-direct-claim", 16)
        self.assertTrue(stale._kitchen_print_is_stale())
        self.assertFalse(recent._kitchen_print_is_stale())
        with patch.object(type(config), "notify_synchronisation", autospec=True), \
                patch.object(type(config), "_notify", autospec=True):
            jobs = stale.get_pending_kitchen_prints(config.id)
            self.assertEqual([job["order_id"] for job in jobs], [recent.id])
            self.assertEqual(stale.kitchen_print_state, "error")
            self.assertEqual(stale.kitchen_print_attempts, 0)
            self.assertTrue(stale.kitchen_can_retry)
            self.assertFalse(direct_claim.claim_kitchen_print(
                direct_claim.id, config.id, "stale-direct-claim-12345",
            ))
            self.assertEqual(direct_claim.kitchen_print_state, "error")
            stale.action_retry_kitchen_print()
            self.assertEqual(stale.kitchen_print_state, "pending")
            self.assertIn(stale.id, [
                job["order_id"] for job in stale.get_pending_kitchen_prints(config.id)
            ])
            self.assertTrue(stale.claim_kitchen_print(
                stale.id, config.id, "manual-stale-print-12345",
            ))
            self.assertEqual(stale.kitchen_print_attempts, 1)

        # Accepted while the branch POS was closed: the print request is made
        # only when the session opens, so the ticket must still be printable.
        resumed = self._create_order(
            "resumed-session-print", state="accepted", store_binding_id=store.id,
            pos_order_id=pos_order.id, kitchen_print_state="pending",
            accepted_at=fields.Datetime.subtract(now, minutes=40),
            kitchen_print_requested_at=fields.Datetime.now(),
        )
        self.assertFalse(resumed._kitchen_print_is_stale())
        with patch.object(type(config), "notify_synchronisation", autospec=True), \
                patch.object(type(config), "_notify", autospec=True):
            self.assertTrue(resumed.claim_kitchen_print(
                resumed.id, config.id, "resumed-session-print-12345",
            ))
            self.assertEqual(resumed.kitchen_print_state, "attempting")

    def test_kitchen_sync_includes_hidden_order_products(self):
        self.connection.auto_print_kitchen = True
        config = self.env["pos.config"].create({
            "name": "Hidden Kitchen Product POS", "module_pos_restaurant": False,
        })
        session = self.env["pos.session"].create({"config_id": config.id})
        product = self.env["product.product"].create({
            "name": "Hidden Kitchen Discount",
            "available_in_pos": False,
            "sale_ok": False,
        })
        pos_order = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.env.company.id,
            "pricelist_id": config.pricelist_id.id,
            "amount_total": 0.0,
            "amount_tax": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "lines": [Command.create({
                "product_id": product.id,
                "name": product.name,
                "qty": 1.0,
                "price_unit": 0.0,
                "price_subtotal": 0.0,
                "price_subtotal_incl": 0.0,
            })],
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": "HIDDEN-KITCHEN-PRODUCT",
        })
        order = self._create_order(
            "hidden-kitchen-product", state="accepted", store_binding_id=store.id,
            pos_order_id=pos_order.id,
        )
        with patch.object(type(config), "_notify", autospec=True) as notify:
            order._request_kitchen_autoprint()
        sync_payload = next(
            call.args[2] for call in notify.call_args_list
            if call.args[1] == "SYNCHRONISATION"
        )
        records = sync_payload["static_records"]
        self.assertIn(product.id, [row["id"] for row in records["product.product"]])
        self.assertIn(product.product_tmpl_id.id, [
            row["id"] for row in records["product.template"]
        ])
        self.assertEqual(records["pos.order.line"][0]["product_id"], product.id)
        self.assertLess(
            list(records).index("product.product"), list(records).index("pos.order.line")
        )

    def test_same_platform_item_can_have_global_and_store_overrides(self):
        config = self.env["pos.config"].create(
            {"name": "Delivery Mapping POS", "module_pos_restaurant": False}
        )
        stores = self.env["bs.delivery.store.binding"].create(
            [
                {
                    "connection_id": self.connection.id,
                    "pos_config_id": config.id,
                    "remote_store_id": "MAPPING-A",
                },
                {
                    "connection_id": self.connection.id,
                    "pos_config_id": config.id,
                    "remote_store_id": "MAPPING-B",
                },
            ]
        )
        products = self.env["product.product"].create(
            [
                {"name": "Global Product"},
                {"name": "Store A Product"},
                {"name": "Store B Product"},
            ]
        )
        mapping_model = self.env["bs.delivery.product.binding"]

        mappings = mapping_model.create(
            [
                {
                    "connection_id": self.connection.id,
                    "external_product_id": "SHARED-SKU",
                    "product_id": products[0].id,
                },
                {
                    "connection_id": self.connection.id,
                    "store_binding_id": stores[0].id,
                    "external_product_id": "SHARED-SKU",
                    "product_id": products[1].id,
                },
                {
                    "connection_id": self.connection.id,
                    "store_binding_id": stores[1].id,
                    "external_product_id": "SHARED-SKU",
                    "product_id": products[2].id,
                },
            ]
        )

        self.assertEqual(len(mappings), 3)
        # UniqueIndex violations are converted to the configured validation
        # message at the RPC boundary; direct ORM calls expose PostgreSQL's
        # integrity error, as in Odoo's own constraint tests.
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            mapping_model.create(
                {
                    "connection_id": self.connection.id,
                    "store_binding_id": stores[0].id,
                    "external_product_id": "SHARED-SKU",
                    "product_id": products[2].id,
                }
            )

    def test_connection_credentials_are_ciphertext_at_rest(self):
        connection = self.env["bs.delivery.connection"].create(
            {
                "name": "Encrypted Workflow Connection",
                "provider_id": self.provider.id,
                "api_key": "api-key-secret",
                "api_secret": "oauth-client-secret",
                "password": "basic-password",
                "access_token": "oauth-access-token",
                "webhook_secret": "webhook-hmac-secret",
            }
        )
        self.env.cr.execute(
            "SELECT api_key, api_secret, password, access_token, webhook_secret, "
            "webhook_token, webhook_token_digest "
            "FROM bs_delivery_connection WHERE id = %s",
            [connection.id],
        )
        raw = self.env.cr.fetchone()

        self.assertEqual(connection.api_key, "api-key-secret")
        self.assertEqual(connection.api_secret, "oauth-client-secret")
        self.assertEqual(connection.password, "basic-password")
        self.assertEqual(connection.access_token, "oauth-access-token")
        self.assertEqual(connection.webhook_secret, "webhook-hmac-secret")
        self.assertTrue(all(value.startswith("bsenc:v1:") for value in raw[:6]))
        self.assertFalse(any("secret" in value or "password" in value for value in raw[:6]))
        self.assertEqual(
            raw[6],
            connection._webhook_token_digest_value(connection.webhook_token),
        )
        cleartext_values = {
            "api_key": connection.api_key,
            "api_secret": connection.api_secret,
            "password": connection.password,
            "access_token": connection.access_token,
            "webhook_secret": connection.webhook_secret,
            "webhook_token": connection.webhook_token,
        }
        connection.invalidate_recordset([*cleartext_values, "webhook_token_digest"])
        for field_name, cleartext in cleartext_values.items():
            self.assertEqual(connection[field_name], cleartext)
        self.assertEqual(
            connection.webhook_token_digest,
            connection._webhook_token_digest_value(connection.webhook_token),
        )
        self.assertEqual(
            self.env["bs.delivery.connection"].sudo().search(
                [
                    (
                        "webhook_token_digest",
                        "=",
                        connection._webhook_token_digest_value(
                            connection.webhook_token
                        ),
                    )
                ],
                limit=1,
            ),
            connection,
        )
        self.assertEqual(
            connection.read(["api_secret"])[0]["api_secret"],
            "********",
        )

    def test_kitchen_print_job_is_leased_to_one_pos_tab(self):
        config = self.env["pos.config"].create(
            {"name": "Delivery Print POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "company_id": self.env.company.id,
                "pricelist_id": config.pricelist_id.id,
                "amount_total": 0.0,
                "amount_tax": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "PRINT-STORE",
                "auto_print_kitchen": True,
            }
        )
        order = self._create_order(
            "print-lease",
            state="accepted",
            store_binding_id=store.id,
            pos_order_id=pos_order.id,
            kitchen_print_state="pending",
            kitchen_print_requested_at=fields.Datetime.now() - timedelta(minutes=2),
            kitchen_print_last_attempt_at=fields.Datetime.now() - timedelta(minutes=2),
        )

        with patch.object(
            type(config), "notify_synchronisation", autospec=True
        ), patch.object(type(config), "_notify", autospec=True):
            jobs = self.env["bs.delivery.order"].get_pending_kitchen_prints(
                config.id
            )
        self.assertEqual([job["order_id"] for job in jobs], [order.id])

        first_claim = "pos-tab-11111111-1111-1111-1111-111111111111"
        second_claim = "pos-tab-22222222-2222-2222-2222-222222222222"
        self.assertTrue(
            self.env["bs.delivery.order"].claim_kitchen_print(
                order.id, config.id, first_claim
            )
        )
        self.assertFalse(
            self.env["bs.delivery.order"].claim_kitchen_print(
                order.id, config.id, second_claim
            )
        )
        with self.assertRaises(AccessError):
            self.env["bs.delivery.order"].acknowledge_kitchen_print(
                order.id, config.id, second_claim, True
            )
        self.env["bs.delivery.order"].acknowledge_kitchen_print(
            order.id, config.id, first_claim, True
        )
        self.assertEqual(order.kitchen_print_state, "sent")
        self.assertTrue(order.kitchen_print_sent_at)

    def test_kitchen_print_claim_rejects_ineligible_orders(self):
        config = self.env["pos.config"].create(
            {"name": "Delivery Print Eligibility POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "company_id": self.env.company.id,
                "pricelist_id": config.pricelist_id.id,
                "amount_total": 0.0,
                "amount_tax": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "PRINT-ELIGIBILITY-STORE",
                "auto_print_kitchen": True,
            }
        )
        claim_key = "pos-tab-eligibility-1111-1111-111111111111"
        wrong_state = self._create_order(
            "print-wrong-order-state",
            state="received",
            store_binding_id=store.id,
            pos_order_id=pos_order.id,
            kitchen_print_state="pending",
        )
        not_requested = self._create_order(
            "print-not-requested",
            state="accepted",
            store_binding_id=store.id,
            pos_order_id=pos_order.id,
            kitchen_print_state="not_requested",
        )

        self.assertFalse(
            self.env["bs.delivery.order"].claim_kitchen_print(
                wrong_state.id, config.id, claim_key
            )
        )
        self.assertFalse(
            self.env["bs.delivery.order"].claim_kitchen_print(
                not_requested.id, config.id, claim_key
            )
        )
        with self.assertRaises(AccessError):
            self.env["bs.delivery.order"].acknowledge_kitchen_print(
                not_requested.id, config.id, claim_key, True
            )

    def test_kitchen_print_ack_requires_boolean_result(self):
        config = self.env["pos.config"].create(
            {"name": "Delivery Print Result POS", "module_pos_restaurant": False}
        )
        session = self.env["pos.session"].create({"config_id": config.id})
        pos_order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "company_id": self.env.company.id,
                "pricelist_id": config.pricelist_id.id,
                "amount_total": 0.0,
                "amount_tax": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
            }
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "PRINT-RESULT-STORE",
                "auto_print_kitchen": True,
            }
        )
        order = self._create_order(
            "print-result",
            state="accepted",
            store_binding_id=store.id,
            pos_order_id=pos_order.id,
            kitchen_print_state="pending",
        )
        claim_key = "pos-tab-result-1111-1111-1111-111111111111"
        self.assertTrue(
            self.env["bs.delivery.order"].claim_kitchen_print(
                order.id, config.id, claim_key
            )
        )
        with self.assertRaises(ValidationError):
            self.env["bs.delivery.order"].acknowledge_kitchen_print(
                order.id, config.id, claim_key, "true"
            )
        self.assertEqual(order.kitchen_print_state, "attempting")
