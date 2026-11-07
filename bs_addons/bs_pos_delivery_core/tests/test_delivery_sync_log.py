from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from ..tools.payload_masking import MASK, serialize_masked_payload


@tagged("post_install", "-at_install")
class TestDeliverySyncLog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env["bs.delivery.sync.log"]
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "Log Test Provider", "code": "log_test"}
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "Log Test Connection",
                "provider_id": cls.provider.id,
            }
        )
        cls.binding, _created = cls.env["bs.delivery.order.binding"].get_or_create(
            cls.connection,
            "LOG-ORDER-001",
        )

    def test_nested_credentials_are_masked_before_storage(self):
        log = self.Log.start_attempt(
            self.connection,
            direction="incoming",
            operation="receive_order",
            binding=self.binding,
            external_id="LOG-ORDER-001",
            request_method="post",
            request_url="https://example.test/orders?api_key=url-secret&safe=yes",
            request_payload={
                "api_key": "payload-secret",
                "safe": "visible-value",
                "nested": {
                    "access_token": "nested-secret",
                    "auth_token": "provider-auth-token",
                    "customer": "Ahmed",
                },
            },
        )

        self.assertEqual(log.request_method, "POST")
        self.assertNotIn("url-secret", log.request_url)
        self.assertNotIn("payload-secret", log.request_payload)
        self.assertNotIn("nested-secret", log.request_payload)
        self.assertNotIn("provider-auth-token", log.request_payload)
        self.assertIn(MASK, log.request_url)
        self.assertIn(MASK, log.request_payload)
        self.assertIn("visible-value", log.request_payload)
        self.assertIn("Ahmed", log.request_payload)

    def test_failed_attempt_masks_response_and_error(self):
        log = self.Log.start_attempt(
            self.connection,
            direction="outgoing",
            operation="update_status",
            request_payload={"status": "ready"},
        )

        log.mark_failed(
            "Authorization=Bearer raw-error-token",
            response_payload='{"password": "response-secret", "message": "Denied"}',
            http_status=401,
            duration_ms=125.5,
        )

        self.assertEqual(log.status, "failed")
        self.assertEqual(log.http_status, 401)
        self.assertEqual(log.duration_ms, 125.5)
        self.assertNotIn("raw-error-token", log.error_message)
        self.assertNotIn("response-secret", log.response_payload)
        self.assertIn("Denied", log.response_payload)

    def test_payload_size_is_bounded_after_masking(self):
        serialized = serialize_masked_payload(
            {"api_key": "secret", "content": "x" * 500},
            max_chars=100,
        )

        self.assertNotIn("secret", serialized)
        self.assertTrue(serialized.endswith("...[truncated]"))
        self.assertLessEqual(len(serialized), 116)

    def test_log_binding_must_use_same_connection(self):
        second_connection = self.env["bs.delivery.connection"].create(
            {
                "name": "Second Log Connection",
                "provider_id": self.provider.id,
            }
        )

        with self.assertRaises(ValidationError):
            self.Log.create(
                {
                    "connection_id": second_connection.id,
                    "binding_id": self.binding.id,
                    "direction": "incoming",
                    "operation": "invalid_connection_test",
                }
            )

    def test_integration_manager_has_read_only_log_access(self):
        log = self.Log.start_attempt(
            self.connection,
            direction="incoming",
            operation="read_only_test",
        )
        manager = new_test_user(
            self.env,
            login="log_read_only_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )

        self.assertEqual(self.Log.with_user(manager).search([("id", "=", log.id)]), log)
        with self.assertRaises(AccessError):
            self.Log.with_user(manager).create(
                {
                    "connection_id": self.connection.id,
                    "direction": "incoming",
                    "operation": "manual_create_forbidden",
                }
            )

    def test_failed_webhook_log_retries_original_event_once(self):
        self.connection.state = "connected"
        event = self.env["bs.delivery.webhook.event"].create({
            "connection_id": self.connection.id,
            "binding_id": self.binding.id,
            "idempotency_key": "retry-log-event",
            "payload_digest": "0" * 64,
            "raw_payload": '{"order_id":"LOG-ORDER-001"}',
            "state": "failed",
            "attempt_count": 1,
            "max_attempts": 1,
        })
        log = self.Log.start_attempt(
            self.connection, direction="incoming", operation="webhook_process",
            binding=self.binding, webhook_event=event,
        )
        log.mark_failed("Temporary processing failure")

        self.assertTrue(log.can_retry_processing)
        manager = new_test_user(
            self.env, login="log_event_retry_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )
        self.assertTrue(log.with_user(manager).can_retry_processing)
        with patch.object(type(event), "_trigger_processing", return_value=None):
            action = log.with_user(manager).action_retry_processing()
        self.assertEqual(action["res_model"], "bs.delivery.sync.log")
        self.assertEqual(action["domain"], [("webhook_event_id", "=", event.id)])
        self.assertEqual(event.state, "retry")
        self.assertEqual(event.max_attempts, 2)
        self.assertEqual(log.status, "failed")
        with self.assertRaises(UserError):
            log.action_retry_processing()
        with patch.object(type(self.connection), "_dispatch", return_value={"ok": True}):
            event._process_one()
        self.assertEqual(event.state, "done")
        self.assertEqual(event.attempt_count, 2)
        self.assertEqual(self.Log.search_count([
            ("webhook_event_id", "=", event.id), ("status", "=", "success"),
        ]), 1)

    def test_failed_logs_show_retry_even_when_source_is_missing(self):
        orphan = self.Log.start_attempt(
            self.connection, direction="incoming", operation="receive_order",
            request_payload={"redacted": True},
        )
        orphan.mark_failed("Invalid product", http_status=422)
        self.assertTrue(orphan.can_retry_processing)
        log_count = self.Log.search_count([("correlation_id", "=", orphan.correlation_id)])
        action = orphan.action_retry_processing()
        self.assertEqual(action["tag"], "reload")
        self.assertEqual(orphan.attempt_number, 2)
        self.assertEqual(orphan.status, "failed")
        self.assertEqual(self.Log.search_count([
            ("correlation_id", "=", orphan.correlation_id),
        ]), log_count)
        outgoing = self.Log.start_attempt(
            self.connection, direction="outgoing", operation="update_status",
            binding=self.binding,
        )
        outgoing.mark_failed("Provider unavailable")
        self.assertTrue(outgoing.can_retry_processing)

        self.connection.state = "connected"
        event = self.env["bs.delivery.webhook.event"].create({
            "connection_id": self.connection.id,
            "idempotency_key": "scrubbed-retry-log-event",
            "payload_digest": "0" * 64,
            "state": "failed",
        })
        log = self.Log.start_attempt(
            self.connection, direction="incoming", operation="webhook_process",
            webhook_event=event,
        )
        log.mark_failed("Payload has been removed")
        self.assertTrue(log.can_retry_processing)
        with self.assertRaises(UserError):
            log.action_retry_processing()

        other_binding, _created = self.env["bs.delivery.order.binding"].get_or_create(
            self.connection, "OTHER-ORDER",
        )
        event.write({
            "binding_id": other_binding.id,
            "raw_payload": '{"order_id":"OTHER-ORDER"}',
        })
        mismatched = self.Log.start_attempt(
            self.connection, direction="incoming", operation="webhook_process",
            binding=self.binding, webhook_event=event,
        )
        mismatched.mark_failed("Mismatched source")
        self.assertTrue(mismatched.can_retry_processing)
        with self.assertRaises(UserError):
            mismatched.action_retry_processing()

        unbound_event = self.env["bs.delivery.webhook.event"].create({
            "connection_id": self.connection.id,
            "idempotency_key": "unbound-retry-log-event",
            "payload_digest": "0" * 64,
            "raw_payload": '{"order_id":"UNBOUND"}',
            "state": "failed",
        })
        unbound_log = self.Log.start_attempt(
            self.connection, direction="incoming", operation="webhook_process",
            binding=self.binding, webhook_event=unbound_event,
        )
        unbound_log.mark_failed("Event has no matching binding")
        self.assertTrue(unbound_log.can_retry_processing)
        with self.assertRaises(UserError):
            unbound_log.action_retry_processing()

    def test_retry_requires_integration_manager_and_allowed_company(self):
        log = self.Log.start_attempt(
            self.connection, direction="incoming", operation="webhook_process",
            binding=self.binding,
        )
        log.mark_failed("Failure")
        viewer = new_test_user(
            self.env, login="log_retry_viewer",
            groups="bs_pos_delivery_core.group_delivery_viewer",
        )
        with self.assertRaises(AccessError):
            log.with_user(viewer).action_retry_processing()

        other = self.env["res.company"].create({"name": "Other log retry company"})
        manager = new_test_user(
            self.env, login="log_retry_other_company_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )
        manager.company_ids |= other
        manager.company_id = other
        with self.assertRaises(AccessError):
            log.with_user(manager).with_context(
                allowed_company_ids=other.ids,
            ).action_retry_processing()

    def test_failed_outbox_log_requeues_its_original_job(self):
        self.connection.state = "connected"
        job = self.env["bs.delivery.outbox"].create({
            "connection_id": self.connection.id,
            "binding_id": self.binding.id,
            "operation": "update_order",
            "idempotency_key": "log-outbox-retry",
            "state": "failed",
        })
        log = self.Log.start_attempt(
            self.connection, direction="outgoing", operation="outbox:update_order",
            binding=self.binding, correlation_id=job.correlation_id,
        )
        log.mark_failed("Provider unavailable")
        with patch.object(type(job), "_trigger_processing"):
            log.action_retry_processing()
        self.assertEqual(job.state, "retry")


@tagged("post_install", "-at_install")
class TestDeliverySyncLogPosRetry(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager",
        )
        provider = cls.env["bs.delivery.provider"].create({
            "name": "POS Retry Log Provider", "code": "pos_retry_log_provider",
        })
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "POS Retry Log Connection", "provider_id": provider.id,
            "company_id": cls.company.id, "state": "connected",
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": cls.env["res.partner"].create({
                "name": "POS retry customer",
            }).id,
        })
        cls.product = cls.env["product.product"].create({
            "name": "POS Retry Meal", "available_in_pos": True,
            "list_price": 20, "taxes_id": [Command.clear()],
            "property_account_income_id": cls.sales_account.id,
        })
        cls.basic_config.open_ui()
        cls.store = cls.env["bs.delivery.store.binding"].create({
            "connection_id": cls.connection.id,
            "pos_config_id": cls.basic_config.id,
            "remote_store_id": "POS-RETRY-STORE",
        })
        cls.env["bs.delivery.product.binding"].create({
            "connection_id": cls.connection.id,
            "store_binding_id": cls.store.id,
            "external_product_id": "MEAL",
            "product_id": cls.product.id,
        })
        cls.connection.write({"delivery_payment_mapping_ids": [Command.create({
            "pos_config_id": cls.basic_config.id,
            "aggregator_name": "marketplace", "payment_type": "credit_visa",
            "payment_method_id": cls.bank_pm1.id,
        })]})

    def test_retry_blocked_pos_order_creates_one_pos_and_separate_log(self):
        self.basic_config.current_session_id.action_pos_session_closing_control()
        event = self.env["bs.delivery.webhook.event"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "signature_verified": True,
            "external_id": "POS-RETRY-ORDER",
            "idempotency_key": "pos-retry-log-event",
            "payload_digest": "0" * 64,
        })
        currency = self.basic_config.currency_id.name
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, {
                "contract_version": 1,
                "external_order_id": "POS-RETRY-ORDER",
                "external_source": "marketplace",
                "currency": currency,
                "fulfillment_type": "delivery",
                "price_basis": "tax_excluded",
                "customer": {"name": "POS Retry Customer"},
                "lines": [{
                    "external_line_id": "item-1", "external_product_id": "MEAL",
                    "name": "Meal", "quantity": 1, "price_unit": 20,
                    "discount_amount": 0, "vat_amount": 0,
                    "price_subtotal": 20,
                }],
                "totals": {
                    "items_subtotal": 20, "discount_amount": 0,
                    "fee_amount": 0, "vat_amount": 0,
                    "rounding_amount": 0, "amount_total": 20,
                },
                "payments": [{
                    "external_payment_code": "credit_visa",
                    "external_source": "marketplace",
                    "amount": 20, "currency": currency,
                    "collected_by": "aggregator", "collection_status": "collected",
                    "external_payment_reference": "POS-RETRY-PAYMENT",
                }],
            }, event=event,
        )
        self.assertEqual(order.state, "accepted")
        self.assertFalse(order.pos_order_id)
        job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
            self.connection, "accept_order", order.binding_id,
            idempotency_key="pos-retry-log-accept",
        )
        with patch.object(type(job), "_dispatch_operation", return_value={"ok": True}):
            self.assertTrue(job._process_one())
        self.basic_config.open_ui()
        if self.basic_config.current_session_id.state == "opening_control":
            self.basic_config.current_session_id.set_opening_control(0.0, "")
        journal = self.basic_config.invoice_journal_id
        self.basic_config.invoice_journal_id = False
        self.env["bs.delivery.order"]._cron_resume_awaiting_pos_sessions()
        self.assertEqual(order.pos_processing_state, "blocked")
        self.assertFalse(order.pos_order_id)
        failed_log = self.env["bs.delivery.sync.log"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "=", "core:pos_processing"),
            ("status", "=", "failed"),
        ], limit=1)
        self.assertTrue(failed_log)
        self.assertTrue(failed_log.can_retry_processing)
        unrelated_log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="unrelated_validation",
            binding=order.binding_id,
        )
        unrelated_log.mark_failed("Unrelated validation failure")
        self.assertTrue(unrelated_log.can_retry_processing)
        with self.assertRaises(UserError):
            unrelated_log.action_retry_processing()
        action = failed_log.action_retry_processing()
        self.assertEqual(action["tag"], "reload")
        self.assertFalse(order.pos_order_id)
        self.assertEqual(failed_log.status, "failed")
        self.assertEqual(failed_log.attempt_number, 2)
        self.assertFalse(self.env["bs.delivery.sync.log"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "=", "core:pos_processing_retry"),
        ]))

        self.basic_config.invoice_journal_id = journal
        manager = new_test_user(
            self.env, login="log_pos_retry_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )
        self.assertTrue(failed_log.with_user(manager).can_retry_processing)
        failed_log.with_user(manager).action_retry_processing()
        self.assertTrue(order.pos_order_id)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(failed_log.status, "success")
        self.assertEqual(failed_log.attempt_number, 3)
        self.assertFalse(failed_log.can_retry_processing)
        self.assertFalse(self.env["bs.delivery.sync.log"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "=", "core:pos_processing_retry"),
        ]))
        pos_id = order.pos_order_id.id
        with self.assertRaises(UserError):
            failed_log.action_retry_processing()
        self.assertEqual(order.pos_order_id.id, pos_id)
