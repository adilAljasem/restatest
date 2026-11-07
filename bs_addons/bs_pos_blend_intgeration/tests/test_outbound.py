from unittest.mock import patch
from datetime import timedelta

import requests

from odoo import fields
from odoo.tests.common import tagged
from odoo.exceptions import ValidationError

from ..services import outbound
from .common import BlendTransactionCase


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


@tagged("post_install", "-at_install")
class TestBlendOutboundIntegration(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        # These tests exercise the explicitly enabled legacy contract.
        self.config.blend_legacy_order_creation_enabled = True

    def _enqueue_order(self, order):
        payload = order._blend_build_order_created_payload()
        log = self.env["blend.integration.log"].enqueue(
            self.config,
            "order_created",
            payload,
            order=order,
            idempotency_key="test-order-created:%s" % order.uuid,
            external_reference=order.uuid,
        )
        return log, payload

    def test_order_created_payload_matches_document_and_success_is_saved(self):
        order = self.create_order()
        log, payload = self._enqueue_order(order)
        body = payload["payload"]

        self.assertEqual(payload["event"], "order_created")
        self.assertEqual(payload["branch_id"], self.branch_id)
        self.assertEqual(
            set(body),
            {
                "id",
                "payment_method",
                "order_type",
                "note",
                "subtotal",
                "tax",
                "discount",
                "total",
                "is_schedule",
                "schedule_date",
                "car_data",
                "items",
                "customer",
            },
        )
        self.assertEqual(body["items"][0]["id"], "100")
        self.assertEqual(body["items"][0]["options"], [
            {"id": "200", "price": 10.0, "quantity": 1.0}
        ])
        self.assertEqual(body["customer"]["name"], "Blend Customer")

        response = FakeResponse(200, {"order_id": "blend-order-1000", "status": "success"})
        with patch.object(outbound.requests, "post", return_value=response) as mock_post:
            self.assertTrue(log._process_outbound())

        log.invalidate_recordset()
        order.invalidate_recordset()
        self.assertEqual(log.state, "success")
        self.assertEqual(log.attempt_count, 1)
        self.assertEqual(log.http_status, 200)
        self.assertEqual(log.response_payload["order_id"], "blend-order-1000")
        self.assertGreaterEqual(log.duration_ms, 0)
        self.assertEqual(log.request_headers["Authorization"], "Bearer ***")
        self.assertEqual(order.blend_order_id, "blend-order-1000")
        call = mock_post.call_args
        self.assertEqual(call.args[0], self.config.blend_order_url)
        self.assertEqual(call.kwargs["json"], payload)
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer %s" % self.outbound_token)
        self.assertEqual(call.kwargs["timeout"], 5)

    def test_order_created_payload_uses_stored_blend_discount(self):
        order = self.create_order()
        order.blend_discount_amount = 10.0

        payload = order._blend_build_order_created_payload()

        self.assertEqual(payload["payload"]["discount"], 10.0)

    def test_order_created_enqueue_is_idempotent(self):
        order = self.create_order()
        order._blend_enqueue_order_created()
        order._blend_enqueue_order_created()
        logs = self.env["blend.integration.log"].search(
            [
                ("config_id", "=", self.config.id),
                ("direction", "=", "outbound"),
                ("event", "=", "order_created"),
                ("order_id", "=", order.id),
            ]
        )
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs.request_payload["payload"]["id"], order.uuid)
        self.assertEqual(order.blend_payload, logs.request_payload)

    def test_order_payload_is_not_replaced_by_status_payload(self):
        order = self.create_order()
        order._blend_enqueue_order_created()
        order.invalidate_recordset(["blend_payload"])
        order_payload = order.blend_payload

        self.assertEqual(order_payload["event"], "order_created")
        self.assertEqual(order_payload["payload"]["id"], order.uuid)

        order._blend_enqueue_status("delivered")
        order.invalidate_recordset(["blend_payload"])
        self.assertEqual(order.blend_payload, order_payload)

    def test_http_error_retries_and_preserves_response(self):
        order = self.create_order()
        log, _payload = self._enqueue_order(order)
        response = FakeResponse(503, {"status": "failed", "message": "Unavailable"})

        with patch.object(outbound.requests, "post", return_value=response):
            self.assertFalse(log._process_outbound())

        log.invalidate_recordset()
        self.assertEqual(log.state, "retry")
        self.assertEqual(log.attempt_count, 1)
        self.assertEqual(log.http_status, 503)
        self.assertEqual(log.response_payload["message"], "Unavailable")
        self.assertTrue(log.next_retry_at)
        self.assertIn("HTTP 503", log.error_message)

    def test_blend_failed_response_is_retried(self):
        order = self.create_order()
        log, _payload = self._enqueue_order(order)
        response = FakeResponse(200, {"order_id": "blend-order-failed", "status": "failed"})

        with patch.object(outbound.requests, "post", return_value=response):
            self.assertFalse(log._process_outbound())

        log.invalidate_recordset()
        self.assertEqual(log.state, "retry")
        self.assertEqual(log.http_status, 200)
        self.assertIn("status=failed", log.error_message)

    def test_network_timeout_retries_then_stops_at_maximum(self):
        order = self.create_order()
        log, _payload = self._enqueue_order(order)
        self.config.blend_retry_max = 2

        with patch.object(
            outbound.requests,
            "post",
            side_effect=requests.exceptions.Timeout("Blend timeout"),
        ):
            self.assertFalse(log._process_outbound())
            log.write({"next_retry_at": False})
            self.assertFalse(log._process_outbound())

        log.invalidate_recordset()
        self.assertEqual(log.state, "failed")
        self.assertEqual(log.attempt_count, 2)
        self.assertFalse(log.next_retry_at)
        self.assertFalse(log.http_status)
        self.assertIn("Blend timeout", log.error_message)

    def test_manual_retry_resets_attempts_and_can_succeed(self):
        order = self.create_order()
        log, _payload = self._enqueue_order(order)
        self.config.blend_retry_max = 1
        with patch.object(outbound.requests, "post", return_value=FakeResponse(500, {"error": "x"})):
            log._process_outbound()
        self.assertEqual(log.state, "failed")

        log.action_retry()
        self.assertEqual(log.state, "pending")
        self.assertEqual(log.attempt_count, 0)
        with patch.object(
            outbound.requests,
            "post",
            return_value=FakeResponse(200, {"order_id": "blend-recovered", "status": "success"}),
        ):
            log._process_outbound()
        self.assertEqual(log.state, "success")
        self.assertEqual(log.attempt_count, 1)

    def test_cron_recovers_interrupted_processing_attempt(self):
        order = self.create_order()
        log, _payload = self._enqueue_order(order)
        log.write(
            {
                "state": "processing",
                "last_attempt_at": fields.Datetime.now() - timedelta(minutes=11),
            }
        )
        self.env["blend.integration.log"].search(
            [
                ("id", "!=", log.id),
                ("direction", "=", "outbound"),
                ("state", "in", ("pending", "retry", "processing")),
            ]
        ).write({"state": "failed", "next_retry_at": False})
        with patch.object(
            outbound.requests,
            "post",
            return_value=FakeResponse(
                200, {"order_id": "blend-recovered-crash", "status": "success"}
            ),
        ):
            self.env["blend.integration.log"]._cron_process_outbound(batch_size=1)
        log.invalidate_recordset()
        self.assertEqual(log.state, "success")
        self.assertEqual(log.http_status, 200)

    def test_failed_payload_build_can_be_rebuilt_after_product_id_is_set(self):
        session = self.env["pos.session"].create({"config_id": self.config.id})
        product = self.env["product.template"].with_context(blend_inbound_sync=True).create(
            {
                "name": "Late Blend Mapping",
                "list_price": 25.0,
                "sale_ok": True,
                "available_in_pos": True,
                "type": "consu",
            }
        )
        order = self.env["pos.order"].create({
            "session_id": session.id,
            "config_id": self.config.id,
            "amount_tax": 0.0,
            "amount_total": 25.0,
            "amount_paid": 25.0,
            "amount_return": 0.0,
        })
        self.env["pos.order.line"].create({
            "order_id": order.id,
            "product_id": product.product_variant_id.id,
            "qty": 1,
            "price_unit": 25.0,
            "price_subtotal": 25.0,
            "price_subtotal_incl": 25.0,
        })

        order._blend_enqueue_order_created()
        log = self.env["blend.integration.log"].search([
            ("config_id", "=", self.config.id),
            ("event", "=", "order_created"),
            ("order_id", "=", order.id),
        ])
        self.assertEqual(log.state, "failed")
        self.assertTrue(log.request_payload["_build_required"])
        product.with_context(blend_inbound_sync=True).blend_external_id = "late-item-900"

        log.action_retry()
        with patch.object(
            outbound.requests,
            "post",
            return_value=FakeResponse(200, {"order_id": "blend-late-900", "status": "success"}),
        ):
            self.assertTrue(log._process_outbound())

        self.assertEqual(log.state, "success")
        self.assertNotIn("_build_required", log.request_payload)
        self.assertEqual(log.request_payload["payload"]["items"][0]["id"], "late-item-900")
        self.assertEqual(order.blend_payload, log.request_payload)
        self.assertEqual(order.blend_order_id, "blend-late-900")

    def test_outgoing_status_schemas_use_original_order_id(self):
        order = self.create_order()
        order.blend_order_id = "blend-order-status"

        primary = order._blend_status_payload("delivered")
        self.assertEqual(primary, {
            "event": "order_updated",
            "event_time": primary["event_time"],
            "branch_id": self.branch_id,
            "payload": {"order_id": order.uuid, "status": "delivered"},
        })

        self.config.blend_schema = "pos"
        flat = order._blend_status_payload("cancelled")
        self.assertEqual(flat["event"], "order.updated")
        self.assertEqual(flat["brand_id"], self.brand_id)
        self.assertEqual(flat["branch_id"], self.branch_id)
        self.assertEqual(flat["order_id"], order.uuid)
        self.assertEqual(flat["status"], "CANCELED")

    def test_status_endpoint_is_called(self):
        order = self.create_order()
        order.blend_partner_reference = "blend-status-endpoint"
        order._blend_enqueue_status("delivered")
        log = self.env["blend.integration.log"].search(
            [("config_id", "=", self.config.id), ("event", "=", "order_updated")],
            order="id desc",
            limit=1,
        )

        with patch.object(
            outbound.requests,
            "post",
            return_value=FakeResponse(200, {"status": "success"}),
        ) as mock_post:
            self.assertTrue(log._process_outbound())

        self.assertEqual(mock_post.call_args.args[0], self.config.blend_status_url)
        self.assertEqual(mock_post.call_args.kwargs["json"]["payload"]["status"], "delivered")

    def test_menu_event_endpoint_is_called(self):
        payload = {
            "brand_id": self.brand_id,
            "event_time": "2026-09-01T12:00:00Z",
            "event": "menu.updated",
            "entity": {"type": "product", "id": "100"},
        }
        log = self.env["blend.integration.log"].enqueue(
            self.config,
            "menu.updated",
            payload,
            idempotency_key="menu-event-test:%s" % self.config.id,
        )

        with patch.object(
            outbound.requests,
            "post",
            return_value=FakeResponse(200, {"status": "success"}),
        ) as mock_post:
            self.assertTrue(log._process_outbound())

        self.assertEqual(mock_post.call_args.args[0], self.config.blend_event_url)
        self.assertEqual(mock_post.call_args.kwargs["json"], payload)

    def test_non_https_endpoint_is_rejected_without_network_call(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "blend.allow_insecure_urls", "0"
        )
        order = self.create_order()
        log, _payload = self._enqueue_order(order)
        log.endpoint_url = "http://blend.example.test/orders"
        with patch.object(outbound.requests, "post") as mock_post:
            self.assertFalse(log._process_outbound())
        mock_post.assert_not_called()
        self.assertEqual(log.state, "retry")
        self.assertIn("HTTPS", log.error_message)

    def test_customer_phone_requires_country_code(self):
        order = self.create_order()
        order.partner_id.phone = "0500000000"
        with self.assertRaises(ValidationError):
            order._blend_build_order_created_payload()

    def test_pos_schema_incoming_order_paid_state_queues_accepted(self):
        self.config.blend_schema = "pos"
        order = self.create_order()
        order.blend_partner_reference = "incoming:%s" % order.uuid
        order.write({"state": "paid"})
        log = self.env["blend.integration.log"].search(
            [
                ("config_id", "=", self.config.id),
                ("order_id", "=", order.id),
                ("event", "=", "order.updated"),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(log)
        self.assertEqual(log.request_payload["status"], "ACCEPTED")
        self.assertEqual(log.request_payload["order_id"], order.uuid)

    def test_schema_rejects_status_from_opposite_direction(self):
        order = self.create_order()
        with self.assertRaises(ValidationError):
            order._blend_status_payload("accepted")
        self.config.blend_schema = "pos"
        with self.assertRaises(ValidationError):
            order._blend_status_payload("delivered")
