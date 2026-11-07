import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..exceptions import (
    DeliveryAuthenticationError,
    DeliveryBusinessError,
    DeliveryNetworkError,
    DeliveryWebhookConflictError,
    DeliveryWebhookValidationError,
)


def _successful_dispatch(connection, operation, *args, **kwargs):
    if operation == "verify_webhook_signature":
        return kwargs["headers"].get("X-Signature") == "valid"
    if operation == "get_webhook_event_metadata":
        payload = kwargs["payload"]
        return {
            "event_id": payload.get("event_id"),
            "event_type": payload.get("event_type"),
            "external_id": payload.get("order_id"),
            "remote_store_id": payload.get("remote_store_id"),
        }
    if operation == "process_webhook":
        return {"processed": True}
    raise AssertionError("Unexpected operation: %s" % operation)


@tagged("post_install", "-at_install")
class TestDeliveryWebhookEvent(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env["bs.delivery.webhook.event"]
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "Webhook Test Provider", "code": "webhook_test"}
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "Webhook Test Connection",
                "provider_id": cls.provider.id,
                "state": "connected",
                "webhook_max_attempts": 3,
                "webhook_retry_base_seconds": 10,
            }
        )
        cls.raw_body = json.dumps(
            {
                "event_id": "evt-001",
                "event_type": "order.created",
                "order_id": "ORDER-001",
                "api_key": "payload-secret",
            }
        ).encode()
        cls.headers = {
            "Content-Type": "application/json",
            "X-Signature": "valid",
            "Authorization": "Bearer header-secret",
        }

    def _ingest(self, raw_body=None, headers=None):
        with (
            patch.object(type(self.connection), "_dispatch", _successful_dispatch),
            patch.object(type(self.Event), "_trigger_processing", lambda events: None),
        ):
            return self.Event.ingest(
                webhook_token=self.connection.webhook_token,
                raw_body=raw_body or self.raw_body,
                headers=headers or self.headers,
            )

    def test_connection_has_opaque_unique_webhook_url(self):
        second = self.env["bs.delivery.connection"].create(
            {
                "name": "Second Webhook Connection",
                "provider_id": self.provider.id,
            }
        )

        self.assertTrue(self.connection.webhook_token)
        self.assertNotEqual(self.connection.webhook_token, second.webhook_token)
        self.assertTrue(
            self.connection.webhook_url.endswith(self.connection.webhook_token)
        )
        self.assertNotIn(self.provider.code, self.connection.webhook_url)

    def test_ingest_is_idempotent_and_masks_operator_preview(self):
        headers = dict(
            self.headers,
            **{"X-Forwarded-For": "203.0.113.14, invalid-proxy-value"},
        )
        event, created = self._ingest(headers=headers)
        duplicate, duplicate_created = self._ingest(headers=headers)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(event, duplicate)
        self.assertEqual(
            self.Event.search_count(
                [
                    ("connection_id", "=", self.connection.id),
                    ("event_id", "=", "evt-001"),
                ]
            ),
            1,
        )
        self.assertEqual(event.state, "queued")
        self.assertEqual(event.external_id, "ORDER-001")
        self.assertTrue(event.signature_verified)
        self.assertEqual(event.client_ip, "203.0.113.14")
        self.assertIn("payload-secret", event.raw_payload)
        self.assertNotIn("payload-secret", event.payload_preview)
        self.assertNotIn("header-secret", event.request_headers)
        receive_logs = self.env["bs.delivery.sync.log"].search(
            [
                ("webhook_event_id", "=", event.id),
                ("operation", "=", "webhook_receive"),
            ]
        )
        self.assertEqual(len(receive_logs), 2)
        self.assertEqual(set(receive_logs.mapped("status")), {"success"})

    def test_client_ip_audit_discards_unvalidated_header_text(self):
        headers = dict(self.headers, **{"X-Forwarded-For": "not-an-ip\r\ninjected"})

        event, _created = self._ingest(headers=headers)

        self.assertFalse(event.client_ip)

    def test_reused_idempotency_key_with_different_payload_is_rejected(self):
        event, created = self._ingest()
        conflicting_body = json.dumps(
            {
                "event_id": "evt-001",
                "event_type": "order.created",
                "order_id": "ORDER-DIFFERENT",
            }
        ).encode()

        try:
            self._ingest(raw_body=conflicting_body)
        except DeliveryWebhookConflictError:
            pass
        else:
            self.fail("A changed payload must not reuse an existing idempotency key.")

        self.assertTrue(created)
        self.assertEqual(
            self.Event.search_count(
                [
                    ("connection_id", "=", self.connection.id),
                    ("event_id", "=", "evt-001"),
                ]
            ),
            1,
        )
        self.assertEqual(event.external_id, "ORDER-001")
        failed_log = self.env["bs.delivery.sync.log"].search(
            [
                ("connection_id", "=", self.connection.id),
                ("operation", "=", "webhook_receive"),
                ("status", "=", "failed"),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(failed_log)

    def test_invalid_signature_is_rejected_and_logged(self):
        headers = dict(self.headers, **{"X-Signature": "invalid"})
        with patch.object(type(self.connection), "_dispatch", _successful_dispatch):
            try:
                self.Event.ingest(
                    webhook_token=self.connection.webhook_token,
                    raw_body=self.raw_body,
                    headers=headers,
                )
            except DeliveryAuthenticationError:
                pass
            else:
                self.fail("An invalid signature must be rejected.")

        self.assertFalse(
            self.Event.search([("connection_id", "=", self.connection.id)])
        )
        log = self.env["bs.delivery.sync.log"].search(
            [("connection_id", "=", self.connection.id)],
            order="id desc",
            limit=1,
        )
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.operation, "webhook_receive")

    def test_invalid_json_is_rejected_after_authentication(self):
        with (
            patch.object(type(self.connection), "_dispatch", _successful_dispatch),
            self.assertRaises(DeliveryWebhookValidationError),
        ):
            self.Event.ingest(
                webhook_token=self.connection.webhook_token,
                raw_body=b"not-json",
                headers=self.headers,
            )

    def test_connection_pause_rejects_after_authentication_before_enqueue(self):
        self.connection.pause_receiving(reason="Rush-hour capacity")
        operations = []

        def tracked_dispatch(connection, operation, *args, **kwargs):
            operations.append(operation)
            return _successful_dispatch(connection, operation, *args, **kwargs)

        with (
            patch.object(type(self.connection), "_dispatch", tracked_dispatch),
            patch.object(type(self.Event), "_trigger_processing", lambda events: None),
            self.assertRaises(DeliveryBusinessError) as raised,
        ):
            self.Event.ingest(
                webhook_token=self.connection.webhook_token,
                raw_body=self.raw_body,
                headers=self.headers,
            )

        self.assertEqual(
            operations[:2],
            ["verify_webhook_signature", "get_webhook_event_metadata"],
        )
        self.assertIn("Rush-hour capacity", str(raised.exception))
        self.assertFalse(
            self.Event.search([("connection_id", "=", self.connection.id)])
        )

    def test_store_pause_uses_authenticated_remote_store_metadata(self):
        config = self.env["pos.config"].create(
            {"name": "Paused Webhook Store POS", "module_pos_restaurant": False}
        )
        reason = self.env["bs.delivery.reject.reason"].create(
            {
                "name": "Kitchen Capacity",
                "code": "webhook_kitchen_capacity",
                "provider_id": self.provider.id,
            }
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "PAUSED-STORE-1",
                "paused": True,
                "pause_reject_reason_id": reason.id,
            }
        )
        raw_body = json.dumps(
            {
                "event_id": "evt-paused-store",
                "event_type": "order.created",
                "order_id": "ORDER-PAUSED-STORE",
                "remote_store_id": store.remote_store_id,
            }
        ).encode()

        with self.assertRaises(DeliveryBusinessError) as raised:
            self._ingest(raw_body=raw_body)

        self.assertIn(store.remote_store_id, str(raised.exception))
        self.assertIn(reason.name, str(raised.exception))
        self.assertFalse(
            self.Event.search([("event_id", "=", "evt-paused-store")])
        )

    def _make_callback_store(self, remote_id="CALLBACK-STORE"):
        config = self.env["pos.config"].create(
            {"name": remote_id, "module_pos_restaurant": False}
        )
        return self.env["bs.delivery.store.binding"].create({
            "connection_id": self.connection.id,
            "pos_config_id": config.id,
            "remote_store_id": remote_id,
        })

    def _make_callback_order(self, store=None, connection=None):
        return self.env["bs.delivery.order"]._with_delivery_internal_update().create({
            "connection_id": (connection or self.connection).id,
            "store_binding_id": store.id if store else False,
            "external_order_id": "CALLBACK-ORDER",
            "currency_id": self.env.company.currency_id.id,
            "amount_total": 0.0,
        })

    def _callback_body(self, **overrides):
        return json.dumps({
            "event_id": "evt-callback",
            "event_type": "order.status_updated",
            "order_id": "CALLBACK-ORDER",
            **overrides,
        }).encode()

    def _assert_existing_callbacks_enqueue(self, store):
        self._make_callback_order(store)
        for event_type in (
            "order.status_updated", "order.cancelled", "order.payment_added",
            "order.created",  # A retry of an existing order is not new intake.
        ):
            with self.subTest(event_type=event_type):
                body = self._callback_body(
                    event_id=event_type, event_type=event_type,
                    remote_store_id=store.remote_store_id,
                )
                event, created = self._ingest(raw_body=body)
                duplicate, created_again = self._ingest(raw_body=body)
                self.assertTrue(created)
                self.assertFalse(created_again)
                self.assertEqual(event, duplicate)
                self.assertEqual(event.state, "queued")
                self.assertEqual(event.store_binding_id, store)

    def test_connection_pause_allows_existing_order_callbacks(self):
        store = self._make_callback_store()
        self.connection.pause_receiving(reason="Rush hour")
        self._assert_existing_callbacks_enqueue(store)

    def test_store_pause_allows_existing_order_callbacks(self):
        store = self._make_callback_store()
        store.paused = True
        self._assert_existing_callbacks_enqueue(store)

    def test_callback_without_store_uses_existing_orders_paused_store(self):
        store = self._make_callback_store()
        self._make_callback_order(store)
        store.paused = True
        self.connection.pause_receiving(reason="Rush hour")

        event, created = self._ingest(raw_body=self._callback_body())

        self.assertTrue(created)
        self.assertEqual(event.store_binding_id, store)

    def test_callback_cannot_use_another_stores_order(self):
        self._make_callback_order(self._make_callback_store())
        other_store = self._make_callback_store("OTHER-STORE")
        self.connection.pause_receiving(reason="Rush hour")
        with self.assertRaises(DeliveryWebhookValidationError):
            self._ingest(raw_body=self._callback_body(
                remote_store_id=other_store.remote_store_id,
            ))
        self.assertFalse(self.Event.search([("event_id", "=", "evt-callback")]))

    def test_callback_unknown_store_cannot_fall_back_to_existing_order(self):
        self._make_callback_order(self._make_callback_store())
        self.connection.pause_receiving(reason="Rush hour")
        with self.assertRaises(DeliveryWebhookValidationError):
            self._ingest(raw_body=self._callback_body(remote_store_id="UNKNOWN"))

    def test_archived_store_still_blocks_existing_order_callback(self):
        store = self._make_callback_store()
        self._make_callback_order(store)
        store.active = False
        with self.assertRaises(DeliveryBusinessError):
            self._ingest(raw_body=self._callback_body())

    def test_existing_order_callback_still_requires_authentication(self):
        self._make_callback_order(self._make_callback_store())
        self.connection.pause_receiving(reason="Rush hour")
        with self.assertRaises(DeliveryAuthenticationError):
            self._ingest(
                raw_body=self._callback_body(), headers={"X-Signature": "invalid"},
            )
        self.assertFalse(self.Event.search([("event_id", "=", "evt-callback")]))

    def test_update_label_without_existing_order_cannot_bypass_pause(self):
        self.connection.pause_receiving(reason="Rush hour")
        for order_id in ("UNKNOWN", None):
            with self.subTest(order_id=order_id):
                with self.assertRaises(DeliveryBusinessError):
                    self._ingest(raw_body=self._callback_body(order_id=order_id))

    def test_other_connections_order_cannot_bypass_pause(self):
        other_company = self.env["res.company"].create({"name": "Callback Company"})
        other_connection = self.env["bs.delivery.connection"].with_company(
            other_company
        ).create({
            "name": "Other Callback Connection",
            "provider_id": self.provider.id,
            "company_id": other_company.id,
            "state": "connected",
        })
        self._make_callback_order(connection=other_connection)
        self.connection.pause_receiving(reason="Rush hour")
        with self.assertRaises(DeliveryBusinessError):
            self._ingest(raw_body=self._callback_body())

    def test_store_metadata_is_saved_on_accepted_webhook(self):
        config = self.env["pos.config"].create(
            {"name": "Accepted Webhook Store POS", "module_pos_restaurant": False}
        )
        store = self.env["bs.delivery.store.binding"].create(
            {
                "connection_id": self.connection.id,
                "pos_config_id": config.id,
                "remote_store_id": "ACTIVE-STORE-1",
            }
        )
        raw_body = json.dumps(
            {
                "event_id": "evt-active-store",
                "event_type": "order.created",
                "order_id": "ORDER-ACTIVE-STORE",
                "remote_store_id": store.remote_store_id,
            }
        ).encode()

        event, created = self._ingest(raw_body=raw_body)

        self.assertTrue(created)
        self.assertEqual(event.store_binding_id, store)

    def test_event_exposes_its_canonical_delivery_order(self):
        event, _created = self._ingest()
        order = (
            self.env["bs.delivery.order"]
            ._with_delivery_internal_update()
            .create(
                {
                    "connection_id": self.connection.id,
                    "webhook_event_id": event.id,
                    "external_order_id": "ORDER-WEBHOOK-AUDIT",
                    "currency_id": self.env.company.currency_id.id,
                    "amount_total": 0.0,
                }
            )
        )

        event.invalidate_recordset(["delivery_order_id"])
        self.assertEqual(event.delivery_order_id, order)

    def test_successful_processing_marks_event_done(self):
        event, _created = self._ingest()

        with patch.object(type(self.connection), "_dispatch", _successful_dispatch):
            result = event._process_one()

        self.assertTrue(result)
        self.assertEqual(event.state, "done")
        self.assertEqual(event.attempt_count, 1)
        self.assertTrue(event.processed_at)
        self.assertTrue(event.processed)
        process_log = self.env["bs.delivery.sync.log"].search(
            [
                ("webhook_event_id", "=", event.id),
                ("operation", "=", "webhook_process"),
            ],
            limit=1,
        )
        self.assertEqual(process_log.status, "success")
        self.assertEqual(process_log.attempt_number, 1)

    def test_queue_processor_only_handles_due_events(self):
        due_event, _created = self._ingest()
        later_body = json.dumps(
            {
                "event_id": "evt-later",
                "event_type": "order.updated",
                "order_id": "ORDER-LATER",
            }
        ).encode()
        later_event, _created = self._ingest(raw_body=later_body)
        later_event.next_attempt_at = fields.Datetime.now() + timedelta(hours=1)

        with patch.object(type(self.connection), "_dispatch", _successful_dispatch):
            processed = self.Event._process_due_events(batch_size=10)

        self.assertEqual(processed, 1)
        self.assertEqual(due_event.state, "done")
        self.assertEqual(later_event.state, "queued")

    def test_company_rule_isolates_webhook_events(self):
        visible_event, _created = self._ingest()
        other_company = self.env["res.company"].create(
            {"name": "Other Webhook Company"}
        )
        other_connection = self.env["bs.delivery.connection"].with_company(
            other_company
        ).create(
            {
                "name": "Other Company Webhook Connection",
                "provider_id": self.provider.id,
                "company_id": other_company.id,
                "state": "connected",
            }
        )
        other_body = json.dumps(
            {
                "event_id": "evt-other-company",
                "event_type": "order.created",
            }
        ).encode()
        with (
            patch.object(type(self.connection), "_dispatch", _successful_dispatch),
            patch.object(type(self.Event), "_trigger_processing", lambda events: None),
        ):
            hidden_event, _created = self.Event.ingest(
                webhook_token=other_connection.webhook_token,
                raw_body=other_body,
                headers=self.headers,
            )
        manager = new_test_user(
            self.env,
            login="single_company_webhook_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
            company_id=self.env.company.id,
        )

        visible = self.Event.with_user(manager).search(
            [("id", "in", [visible_event.id, hidden_event.id])]
        )

        self.assertEqual(visible, visible_event)

    def test_retryable_failure_uses_exponential_backoff_then_dead_letters(self):
        event, _created = self._ingest()

        def failing_dispatch(connection, operation, *args, **kwargs):
            if operation == "process_webhook":
                raise DeliveryNetworkError("Temporary network failure")
            return _successful_dispatch(connection, operation, *args, **kwargs)

        with patch.object(type(self.connection), "_dispatch", failing_dispatch):
            event._process_one()
            first_delay = event.next_attempt_at - event.last_attempt_at
            self.assertEqual(event.state, "retry")
            self.assertGreaterEqual(first_delay, timedelta(seconds=9))

            event.next_attempt_at = fields.Datetime.now()
            event._process_one()
            second_delay = event.next_attempt_at - event.last_attempt_at
            self.assertEqual(event.state, "retry")
            self.assertGreaterEqual(second_delay, timedelta(seconds=19))

            event.next_attempt_at = fields.Datetime.now()
            event._process_one()

        self.assertEqual(event.state, "failed")
        self.assertEqual(event.error_type, "network_error")
        self.assertEqual(event.attempt_count, 3)
        self.assertFalse(event.next_attempt_at)

    def test_non_retryable_business_failure_stops_immediately(self):
        event, _created = self._ingest()

        def business_failure(connection, operation, *args, **kwargs):
            if operation == "process_webhook":
                raise DeliveryBusinessError("Order is already cancelled")
            return _successful_dispatch(connection, operation, *args, **kwargs)

        with patch.object(type(self.connection), "_dispatch", business_failure):
            event._process_one()

        self.assertEqual(event.state, "failed")
        self.assertEqual(event.error_type, "business_error")
        self.assertEqual(event.attempt_count, 1)

    def test_manager_can_monitor_but_cannot_read_raw_payload(self):
        event, _created = self._ingest()
        manager = new_test_user(
            self.env,
            login="webhook_event_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )
        managed_event = event.with_user(manager)

        self.assertEqual(self.Event.with_user(manager).search([("id", "=", event.id)]), event)
        self.assertTrue(managed_event.payload_preview)
        with self.assertRaises(AccessError):
            managed_event.read(["raw_payload"])

    def test_manual_retry_adds_one_attempt_without_resetting_history(self):
        event, _created = self._ingest()
        event.write({"state": "failed", "attempt_count": 3, "max_attempts": 3})
        manager = new_test_user(
            self.env,
            login="webhook_retry_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )

        with patch.object(type(self.Event), "_trigger_processing", lambda events: None):
            event.with_user(manager).action_retry()

        self.assertEqual(event.state, "retry")
        self.assertEqual(event.attempt_count, 3)
        self.assertEqual(event.max_attempts, 4)
        self.assertTrue(event.next_attempt_at)
