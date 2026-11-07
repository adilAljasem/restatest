from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..exceptions import DeliveryBusinessError, DeliveryNetworkError


@tagged("post_install", "-at_install")
class TestDeliveryOutbox(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Outbox = cls.env["bs.delivery.outbox"]
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "Outbox Test Provider", "code": "outbox_test"}
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {
                "name": "Outbox Test Connection",
                "provider_id": cls.provider.id,
                "state": "connected",
                "outgoing_mode": "queued",
                "outbox_max_attempts": 3,
                "outbox_retry_base_seconds": 10,
                "outbox_processing_timeout_minutes": 1,
            }
        )
        pos_config = cls.env["pos.config"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        if not pos_config:
            pos_config = cls.env["pos.config"].create({"name": "Outbox Test POS"})
        if not pos_config.current_session_id:
            pos_config.open_ui()
        product = cls.env["product.product"].create(
            {
                "name": "Outbox Test Product",
                "available_in_pos": True,
                "list_price": 10.0,
                "taxes_id": [Command.clear()],
            }
        )
        order = cls.env["pos.order"].create(
            {
                "company_id": cls.env.company.id,
                "session_id": pos_config.current_session_id.id,
                "amount_tax": 0.0,
                "amount_total": 10.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
                "lines": [
                    Command.create(
                        {
                            "product_id": product.id,
                            "qty": 1.0,
                            "price_unit": 10.0,
                            "price_subtotal": 10.0,
                            "price_subtotal_incl": 10.0,
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        cls.binding, _created = cls.env["bs.delivery.order.binding"].get_or_create(
            cls.connection,
            "OUTBOX-ORDER-001",
            {"pos_order_id": order.id},
        )

    def _enqueue(self, *, operation="update_order", key="request-key", **kwargs):
        values = {"status_value": "ready"} if operation == "update_order" else {}
        values.update(kwargs)
        with patch.object(type(self.Outbox), "_trigger_processing", lambda jobs: None):
            return self.Outbox.enqueue(
                self.connection,
                operation,
                self.binding,
                idempotency_key=key,
                **values,
            )

    def test_enqueue_is_idempotent_and_does_not_store_raw_key(self):
        job, created = self._enqueue(key="customer-visible-request-key")
        duplicate, duplicate_created = self._enqueue(
            key="customer-visible-request-key"
        )

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(job, duplicate)
        self.assertEqual(len(job.idempotency_key), 64)
        self.assertNotIn("customer-visible", job.idempotency_key)
        self.assertEqual(job.state, "queued")
        self.assertEqual(self.binding.sync_state, "pending")

    def test_successful_job_updates_binding_connection_and_sanitized_log(self):
        job, _created = self._enqueue(key="successful-job")

        def successful_dispatch(connection, operation, binding, status, **kwargs):
            self.assertEqual(connection, self.connection)
            self.assertEqual(operation, "update_order")
            self.assertEqual(binding, self.binding)
            self.assertEqual(status, "ready")
            return {"ok": True, "access_token": "provider-secret"}

        with patch.object(type(self.connection), "_dispatch", successful_dispatch):
            processed = job._process_one()

        self.assertTrue(processed)
        self.assertEqual(job.state, "done")
        self.assertEqual(job.attempt_count, 1)
        self.assertNotIn("provider-secret", job.result_payload)
        self.assertEqual(self.binding.sync_state, "synced")
        self.assertTrue(self.connection.last_sync_date)
        log = self.env["bs.delivery.sync.log"].search(
            [("correlation_id", "=", job.correlation_id)], limit=1
        )
        self.assertEqual(log.status, "success")
        self.assertEqual(log.operation, "outbox:update_order")

    def test_retryable_failure_backs_off_then_exhausts(self):
        job, _created = self._enqueue(key="retryable-job")
        job.max_attempts = 2

        def network_failure(connection, operation, *args, **kwargs):
            raise DeliveryNetworkError(
                "Authorization=Bearer should-never-be-stored"
            )

        with patch.object(type(self.connection), "_dispatch", network_failure):
            job._process_one()
            first_delay = job.next_attempt_at - job.last_attempt_at
            self.assertEqual(job.state, "retry")
            self.assertGreaterEqual(first_delay, timedelta(seconds=9))
            self.assertNotIn("should-never-be-stored", job.last_error)

            job.next_attempt_at = fields.Datetime.now()
            job._process_one()

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.attempt_count, 2)
        self.assertFalse(job.next_attempt_at)
        self.assertEqual(self.binding.sync_state, "error")
        self.assertEqual(
            self.env["bs.delivery.sync.log"].search_count(
                [("correlation_id", "=", job.correlation_id)]
            ),
            2,
        )

    def test_completed_job_does_not_hide_another_pending_job(self):
        first, _created = self._enqueue(key="first-status")
        second, _created = self._enqueue(key="second-status")
        with patch.object(type(self.connection), "_dispatch", return_value={"ok": True}):
            self.assertTrue(first._process_one())
            self.assertEqual(self.binding.sync_state, "pending")
            self.assertTrue(second._process_one())
            self.assertEqual(self.binding.sync_state, "synced")

    def test_acknowledged_order_without_pos_does_not_claim_pos_binding(self):
        binding, _created = self.env["bs.delivery.order.binding"].get_or_create(
            self.connection, "REJECTED-NO-POS"
        )
        order = self.env["bs.delivery.order"].create(
            {
                "connection_id": self.connection.id,
                "binding_id": binding.id,
                "external_order_id": binding.external_order_id,
            }
        )
        job, _created = self.Outbox.enqueue(self.connection, "reject_order", binding)
        with patch.object(type(self.connection), "_dispatch", return_value={"ok": True}):
            self.assertTrue(job._process_one())
        self.assertFalse(binding.pos_order_id)
        self.assertNotEqual(binding.sync_state, "synced")
        self.assertEqual(order.sync_state, "synced")

    def test_non_retryable_failure_stops_after_one_attempt(self):
        job, _created = self._enqueue(key="business-failure")

        def business_failure(connection, operation, *args, **kwargs):
            raise DeliveryBusinessError("The provider rejected this transition")

        with patch.object(type(self.connection), "_dispatch", business_failure):
            job._process_one()

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.attempt_count, 1)
        self.assertFalse(job.next_attempt_at)

    def test_due_processor_skips_future_job(self):
        due_job, _created = self._enqueue(key="due-job")
        future_job, _created = self._enqueue(key="future-job")
        future_job.next_attempt_at = fields.Datetime.now() + timedelta(hours=1)

        with patch.object(
            type(self.connection),
            "_dispatch",
            lambda connection, operation, *args, **kwargs: {"ok": True},
        ):
            processed = self.Outbox._process_due_jobs(batch_size=10)

        self.assertEqual(processed, 1)
        self.assertEqual(due_job.state, "done")
        self.assertEqual(future_job.state, "queued")

    def test_ready_waits_for_acceptance_retry(self):
        acceptance, _created = self._enqueue(operation="accept_order", key="accept-first")
        acceptance.write(
            {"state": "retry", "next_attempt_at": fields.Datetime.now() + timedelta(hours=1)},
        )
        ready, _created = self._enqueue(operation="mark_order_ready", key="ready-second")
        with patch.object(type(self.connection), "_dispatch", return_value={"ok": True}):
            self.assertFalse(ready._process_one())
        self.assertEqual(ready.state, "queued")
        self.assertEqual(ready.attempt_count, 0)

    def test_priority_cannot_reorder_one_orders_mutations(self):
        acceptance, _created = self._enqueue(operation="accept_order", key="accept-low", priority=1)
        ready, _created = self._enqueue(operation="mark_order_ready", key="ready-high", priority=100)
        sent = []

        def dispatch(connection, operation, *args, **kwargs):
            sent.append(operation)
            return {"ok": True}

        with patch.object(type(self.connection), "_dispatch", dispatch):
            self.Outbox._process_due_jobs(batch_size=10)
        self.assertEqual(sent, ["accept_order", "mark_order_ready"])
        self.assertEqual(acceptance.state, "done")
        self.assertEqual(ready.state, "done")

    def test_blocked_order_does_not_block_another_orders_jobs(self):
        acceptance, _created = self._enqueue(operation="accept_order", key="accept-blocked")
        acceptance.write({"state": "failed", "next_attempt_at": False})
        ready, _created = self._enqueue(operation="mark_order_ready", key="ready-blocked", priority=100)
        other_binding, _created = self.env["bs.delivery.order.binding"].get_or_create(
            self.connection, "INDEPENDENT-ORDER",
        )
        other, _created = self.Outbox.enqueue(
            self.connection, "accept_order", other_binding, idempotency_key="other-accept",
        )
        with patch.object(type(self.connection), "_dispatch", return_value={"ok": True}):
            processed = self.Outbox._process_due_jobs(batch_size=10)
        self.assertEqual(processed, 1)
        self.assertEqual(other.state, "done")
        self.assertEqual(ready.state, "queued")

    def test_stale_processing_job_is_recovered(self):
        job, _created = self._enqueue(key="stale-job")
        job.write(
            {
                "state": "processing",
                "attempt_count": 1,
                "last_attempt_at": fields.Datetime.now() - timedelta(minutes=2),
                "next_attempt_at": False,
            }
        )

        recovered = self.Outbox._recover_stale_processing_jobs()

        self.assertEqual(recovered, 1)
        self.assertEqual(job.state, "retry")
        self.assertTrue(job.next_attempt_at)

    def test_synchronous_mode_dispatches_without_creating_job(self):
        self.connection.outgoing_mode = "synchronous"

        with patch.object(
            type(self.connection),
            "_dispatch",
            return_value={"sent": True},
        ) as dispatch:
            result = self.connection.dispatch_order_operation(
                "accept_order", self.binding
            )

        self.assertEqual(result, {"sent": True})
        dispatch.assert_called_once()
        self.assertFalse(
            self.Outbox.search([("connection_id", "=", self.connection.id)])
        )

    def test_validation_and_management_access(self):
        with self.assertRaises(ValidationError):
            self._enqueue(operation="update_order", key="missing-status", status_value=None)

        job, _created = self._enqueue(operation="accept_order", key="access-job")
        job.write({"state": "failed", "attempt_count": 3, "max_attempts": 3})
        ordinary_user = new_test_user(self.env, login="outbox_ordinary_user")
        manager = new_test_user(
            self.env,
            login="outbox_manager",
            groups="bs_pos_delivery_core.group_delivery_integration_manager",
        )

        with self.assertRaises(AccessError):
            job.with_user(ordinary_user).action_retry()
        with patch.object(type(self.Outbox), "_trigger_processing", lambda jobs: None):
            job.with_user(manager).action_retry()

        self.assertEqual(job.state, "retry")
        self.assertEqual(job.attempt_count, 3)
        self.assertEqual(job.max_attempts, 4)
