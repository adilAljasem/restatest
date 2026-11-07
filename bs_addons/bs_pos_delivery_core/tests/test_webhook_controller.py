import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..controllers import webhook as webhook_controller
from .test_delivery_webhook_event import _successful_dispatch


class _HttpRequestStub:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    def get_data(self, cache=True):
        return self._body


class _RequestStub:
    def __init__(self, env, body, headers=None):
        self.env = env
        self.httprequest = _HttpRequestStub(body, headers)

    @staticmethod
    def make_json_response(payload, status=200):
        return SimpleNamespace(status_code=status, payload=payload)


@tagged("post_install", "-at_install")
class TestDeliveryWebhookController(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env["bs.delivery.webhook.event"]
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "HTTP Webhook Provider", "code": "http_webhook_test"}
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "HTTP Webhook Connection",
                "provider_id": cls.provider.id,
                "state": "connected",
            }
        )
        cls.controller = webhook_controller.DeliveryWebhookController()

    def _call_endpoint(self, body, headers, token=None):
        request_stub = _RequestStub(self.env, body, headers)
        event_model_type = type(self.Event)
        with (
            patch.object(webhook_controller, "request", request_stub),
            patch.object(type(self.connection), "_dispatch", _successful_dispatch),
            patch.object(event_model_type, "_trigger_processing", lambda events: None),
        ):
            endpoint = self.controller.receive_webhook.original_endpoint
            return endpoint(
                self.controller,
                token if token is not None else self.connection.webhook_token
            )

    def test_endpoint_acknowledges_and_deduplicates(self):
        body = json.dumps(
            {
                "event_id": "http-event-001",
                "event_type": "order.created",
                "order_id": "HTTP-ORDER-001",
            }
        ).encode()
        headers = {"Content-Type": "application/json", "X-Signature": "valid"}

        first = self._call_endpoint(body, headers)
        duplicate = self._call_endpoint(body, headers)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.payload["status"], "queued")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.payload["status"], "duplicate")

    def test_endpoint_reports_changed_idempotent_payload_as_conflict(self):
        headers = {"Content-Type": "application/json", "X-Signature": "valid"}
        first_body = json.dumps(
            {
                "event_id": "http-event-conflict",
                "event_type": "order.created",
                "order_id": "HTTP-ORDER-A",
            }
        ).encode()
        changed_body = json.dumps(
            {
                "event_id": "http-event-conflict",
                "event_type": "order.created",
                "order_id": "HTTP-ORDER-B",
            }
        ).encode()

        first = self._call_endpoint(first_body, headers)
        conflict = self._call_endpoint(changed_body, headers)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.payload["status"], "idempotency_conflict")

    def test_endpoint_rejects_invalid_signature(self):
        body = json.dumps(
            {"event_id": "http-event-invalid", "event_type": "order.created"}
        ).encode()
        headers = {"Content-Type": "application/json", "X-Signature": "invalid"}

        response = self._call_endpoint(body, headers)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.payload["status"], "unauthorized")

    def test_endpoint_rejects_authenticated_orders_while_receiving_is_paused(self):
        self.connection.pause_receiving(reason="Rush hour")
        body = json.dumps(
            {
                "event_id": "http-event-paused",
                "event_type": "order.created",
                "order_id": "HTTP-ORDER-PAUSED",
            }
        ).encode()
        headers = {"Content-Type": "application/json", "X-Signature": "valid"}

        response = self._call_endpoint(body, headers)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.payload["status"], "rejected")

    def test_endpoint_queues_existing_order_callback_during_pause(self):
        self.env["bs.delivery.order"]._with_delivery_internal_update().create({
            "connection_id": self.connection.id,
            "external_order_id": "HTTP-EXISTING-ORDER",
            "currency_id": self.env.company.currency_id.id,
            "amount_total": 0.0,
        })
        self.connection.pause_receiving(reason="Rush hour")
        body = json.dumps({
            "event_id": "http-existing-callback",
            "event_type": "order.cancelled",
            "order_id": "HTTP-EXISTING-ORDER",
        }).encode()
        headers = {"Content-Type": "application/json", "X-Signature": "valid"}

        first = self._call_endpoint(body, headers)
        duplicate = self._call_endpoint(body, headers)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.payload["status"], "queued")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.payload["status"], "duplicate")

    def test_endpoint_hides_unknown_tokens(self):
        response = self._call_endpoint(
            b"{}",
            {"Content-Type": "application/json"},
            token="not-a-real-token",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.payload["status"], "not_found")

    def test_endpoint_rejects_oversized_payload_before_database_work(self):
        oversized_body = b"x" * (webhook_controller.MAX_WEBHOOK_BODY_BYTES + 1)

        response = self._call_endpoint(oversized_body, {})

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.payload["status"], "payload_too_large")
