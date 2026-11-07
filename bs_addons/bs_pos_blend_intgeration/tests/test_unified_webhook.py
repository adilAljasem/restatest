import uuid
from unittest.mock import patch

import requests

from odoo import Command
from odoo.exceptions import ValidationError, UserError
from odoo.tests.common import tagged

from ..services import outbound
from ..services.exceptions import BlendAPIError
from ..services.menu import BlendMenuService
from ..services.pos_api import BlendPOSAPIService
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendUnifiedWebhook(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.url = "https://blend.example.test/integration/webhook/pos/test-brand"
        category = self.env["pos.category"].create({"name": "Unified test catalogue"})
        self.config.write({
            "blend_outbound_webhook_url": self.url, "blend_schema": "primary",
            "limit_categories": True, "iface_available_categ_ids": [Command.set(category.ids)],
        })
        self.product = self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Unified tracked item", "available_in_pos": True, "sale_ok": True,
            "type": "consu", "is_storable": True, "pos_categ_ids": [Command.set(category.ids)],
            "company_id": self.env.company.id,
        }).with_env(self.env)
        self.location = self.config.picking_type_id.default_location_src_id
        self.logs = self.env["blend.integration.log"]
        self._mark()

    def _mark(self):
        self.last_id = self.logs.search([], order="id desc", limit=1).id or 0

    def _new(self, event=None):
        domain = [("id", ">", self.last_id), ("direction", "=", "outbound"), ("config_id", "=", self.config.id)]
        if event:
            domain.append(("event", "=", event))
        return self.logs.search(domain, order="id")

    def _add(self, amount):
        with patch.object(outbound.requests, "post") as send:
            self.env["stock.quant"]._update_available_quantity(self.product.product_variant_id, self.location, amount)
        send.assert_not_called()

    def _send(self, logs=None):
        logs = self._new() if logs is None else logs
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as send:
            for log in logs:
                log._process_outbound()
        return send

    def test_four_events_share_url_and_legacy_creation_stays_separate(self):
        for event in ("menu.updated", "item.updated", "store.updated", "order.updated"):
            self.assertEqual(self.logs._event_endpoint(self.config, event), (self.url, "post"))
        self.assertEqual(self.logs._event_endpoint(self.config, "order_created"), (self.config.blend_order_url, "post"))
        self.assertEqual(self.logs._event_endpoint(self.config, "order_updated"), (self.config.blend_status_url, "post"))

    def test_price_change_sends_complete_menu_event(self):
        self.product.list_price = 42
        log = self._new()
        self.assertEqual(log.event, "menu.updated")
        self.assertEqual(set(log.request_payload), {"brand_id", "event_time", "event", "entity"})
        self.assertEqual(log.request_payload["entity"], {"id": str(self.product.id), "type": "product"})
        send = self._send()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0], self.url)
        self.assertEqual(send.call_args.kwargs["headers"]["Authorization"], "Bearer %s" % self.outbound_token)
        self.assertEqual(log.request_headers["Authorization"], "Bearer ***")

    def test_depletion_and_restock_send_item_booleans_without_menu_event(self):
        for amount, expected, calls in ((2, True, 1), (-2, False, 1), (-1, False, 0), (2, True, 1)):
            with self.subTest(amount=amount):
                self._mark()
                self._add(amount)
                log = self._new()
                self.assertEqual(log.event, "item.updated")
                send = self._send()
                self.assertEqual(send.call_count, calls)
                if calls:
                    payload = send.call_args.kwargs["json"]
                    self.assertEqual(set(payload), {"brand_id", "event_time", "event", "branch_id", "product_id", "is_stock"})
                    self.assertIs(payload["is_stock"], expected)
                    self.assertEqual(payload["product_id"], str(self.product.id))
                    self.assertEqual(payload["branch_id"], self.config.blend_branch_id)

    def test_retry_rebuilds_boolean_after_newer_stock_change(self):
        self._add(1)
        self._send()
        self._mark()
        self._add(-1)
        log = self._new()
        with patch.object(outbound.requests, "post", side_effect=requests.exceptions.Timeout("test timeout")):
            log._process_outbound()
        self.assertEqual(log.state, "retry")
        self.assertIs(log.request_payload["is_stock"], False)
        self._add(1)
        send = self._send(log)
        self.assertIs(send.call_args.kwargs["json"]["is_stock"], True)
        self.assertEqual(log.state, "success")

    def test_manual_retry_keeps_uncertain_delivery_after_attempt_counter_reset(self):
        self._add(1)
        self._send()
        self._mark()
        self._add(-1)
        log = self._new()
        with patch.object(outbound.requests, "post", side_effect=requests.exceptions.Timeout("test timeout")):
            log._process_outbound()
        self._add(1)
        log.action_retry()
        self.assertEqual(log.attempt_count, 0)
        send = self._send(log)
        self.assertEqual(send.call_count, 1)
        self.assertIs(send.call_args.kwargs["json"]["is_stock"], True)

    def test_reservation_exhaustion_sends_false_and_release_sends_true(self):
        self._add(2)
        self._send()
        for reserved, expected in ((2, False), (0, True)):
            self._mark()
            self.env["stock.quant"].search([
                ("product_id", "=", self.product.product_variant_id.id),
                ("location_id", "=", self.location.id),
            ]).write({"reserved_quantity": reserved})
            log = self._new()
            self.assertEqual(log.event, "item.updated")
            self.assertEqual(self._send().call_count, 1)
            self.assertIs(log.request_payload["is_stock"], expected)

    def test_store_toggle_sends_boolean_and_refreshes_item_availability(self):
        self._add(1)
        self._send()
        for available in (False, True):
            self._mark()
            self.config.blend_available = available
            self.assertEqual(set(self._new().mapped("event")), {"item.updated", "store.updated"})
            self._send()
            store = self._new("store.updated")
            self.assertIs(store.request_payload["status"], available)
            self.assertEqual(set(store.request_payload), {"brand_id", "event_time", "event", "branch_id", "status"})
            self.assertIs(self._new("item.updated").request_payload["is_stock"], available)

    def test_no_echo_for_inbound_branch_or_product_updates(self):
        self._add(1)
        self._send()
        self._mark()
        BlendMenuService(self.env, self.config).set_branch_availability(False)
        self.product.with_context(blend_inbound_sync=True).write({
            "blend_unavailable_config_ids": [Command.link(self.config.id)],
        })
        self.assertFalse(self._new())

    def test_manual_product_toggle_uses_item_event(self):
        self._add(1)
        self._send()
        self._mark()
        self.product.blend_unavailable_config_ids = [Command.link(self.config.id)]
        log = self._new()
        self.assertEqual(log.event, "item.updated")
        self._send()
        self.assertIs(log.request_payload["is_stock"], False)

    def test_changed_destination_blocks_until_explicit_retry(self):
        self.product.list_price = 43
        log = self._new()
        new_url = "https://blend.example.test/integration/webhook/pos/reconfigured"
        self.config.blend_outbound_webhook_url = new_url
        self.assertEqual(self._send(log).call_count, 0)
        self.assertEqual(log.state, "retry")
        self.assertIn("Reset and Retry", log.error_message)
        self.assertEqual(log.endpoint_url, self.url)
        log.action_retry()
        self.assertEqual(log.endpoint_url, new_url)
        self.assertEqual(self._send(log).call_args.args[0], new_url)
        with self.assertRaises(UserError):
            log.action_retry()

    def test_invalid_unified_urls_are_rejected(self):
        for url in ("http://blend.example.test/hook", "https://blend.example.test", "https://user:pass@blend.example.test/hook"):
            with self.subTest(url=url), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.config.blend_outbound_webhook_url = url

    def test_ambiguous_identity_blocks_menu_orders_and_webhook_not_stock_write(self):
        self._add(1)
        self._send()
        self.env["product.template"].with_context(blend_inbound_sync=True).create({
            "name": "Conflicting imported item", "available_in_pos": True,
            "blend_config_id": self.config.id, "blend_external_id": "0%s" % self.product.id,
            "pos_categ_ids": [Command.set(self.product.pos_categ_ids.ids)],
        })
        self._mark()
        self._add(-1)
        self.assertEqual(self._send().call_count, 0)
        self.assertEqual(set(self._new().mapped("state")), {"retry"})
        api = BlendPOSAPIService(self.env, self.config)
        with self.assertRaisesRegex(BlendAPIError, "Ambiguous"):
            api.get_menu()
        with self.assertRaisesRegex(BlendAPIError, "Ambiguous"):
            api._find_product(self.config, str(self.product.id))

    def _create_incoming_order(self):
        self._add(1)
        self._send()
        session = self.env["pos.session"].create({"config_id": self.config.id})
        session.set_opening_control(0, "")
        reference = uuid.uuid4().hex
        result = BlendPOSAPIService(self.env, self.config).create_order({
            "branch_id": self.config.blend_branch_id, "brand_id": self.config.blend_brand_id,
            "partner_reference": "jahez: %s" % reference,
            "aggregator_order_id": reference, "aggregator": "jahez", "order_type": "delivery",
            "status": "PENDING", "subtotal": 10, "tax": 0, "total": 10,
            "products": [{"id": str(self.product.id), "quantity": 1, "total": 10, "modifiers": []}],
        })
        return self.env["pos.order"].search([("uuid", "=", result["order_id"])])

    def test_unified_url_overrides_primary_schema_for_acceptance(self):
        order = self._create_incoming_order()
        self._mark()
        order.action_blend_accept_order()
        log = self._new()
        self.assertEqual(log.event, "order.updated")
        self.assertEqual(log.endpoint_url, self.url)
        self.assertEqual(log.request_payload["status"], "ACCEPTED")
        self.assertEqual(set(log.request_payload), {"brand_id", "event_time", "event", "branch_id", "order_id", "status"})
        self.assertEqual(self._send(log).call_count, 1)

    def test_unified_url_overrides_primary_schema_for_rejection(self):
        order = self._create_incoming_order()
        self._mark()
        self.env["blend.order.reject.wizard"].create({
            "order_id": order.id, "reason": "Unavailable for this test",
        }).action_confirm_rejection()
        log = self._new()
        self.assertEqual(log.event, "order.updated")
        self.assertEqual(log.request_payload["status"], "REJECTED")
        self.assertEqual(log.endpoint_url, self.url)
        self.assertEqual(self._send(log).call_count, 1)

    def test_unified_url_overrides_primary_schema_for_auto_acceptance(self):
        self.config.blend_auto_accept = True
        order = self._create_incoming_order()
        log = self.logs.search([("order_id", "=", order.id), ("direction", "=", "outbound")])
        self.assertEqual(log.event, "order.updated")
        self.assertEqual(log.request_payload["status"], "ACCEPTED")
        self.assertEqual(log.endpoint_url, self.url)
        self.assertEqual(order.blend_status, "accepted")
        self.assertEqual(order.state, "draft")
        self.assertEqual(self._send(log).call_count, 1)

    def test_configuration_view_separates_unified_and_legacy_endpoints(self):
        arch = self.env.ref("bs_pos_blend_intgeration.pos_config_view_form_blend")._get_combined_arch()
        unified = arch.xpath("//field[@name='blend_outbound_webhook_url']")
        self.assertEqual(len(unified), 1)
        schema = arch.xpath("//field[@name='blend_schema']")[0]
        self.assertEqual(schema.getparent().get("invisible"), "blend_outbound_webhook_url")
        self.assertEqual(
            arch.xpath("//field[@name='blend_order_url']")[0].getparent().get("invisible"),
            "not blend_legacy_order_creation_enabled",
        )
