"""Status queue regression tests; all Blend HTTP traffic is mocked."""

import uuid
from datetime import datetime
from unittest.mock import patch

import requests
from psycopg2.errors import SerializationFailure

from odoo.tests.common import tagged

from ..services import outbound
from ..services.pos_api import BlendPOSAPIService
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendStatusDelivery(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.config.blend_outbound_webhook_url = "https://blend.example.test/events"
        self.session = self.env["pos.session"].create({"config_id": self.config.id})
        self.order = self._order()
        self.logs = self.env["blend.integration.log"]

    def _order(self):
        reference = str(uuid.uuid4())
        return self.env["pos.order"].create({
            "session_id": self.session.id, "config_id": self.config.id,
            "uuid": reference, "pos_reference": "STATUS-TEST-%s" % reference,
            "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0,
            "blend_status": "pending", "blend_partner_reference": "test:%s" % reference,
        })

    def _queue(self, status, order=None):
        order = order or self.order
        order._blend_enqueue_status(status)
        return self.logs.search([
            ("order_id", "=", order.id), ("direction", "=", "outbound"),
            ("event", "in", ["order.updated", "order_updated"]),
        ], order="id desc", limit=1)

    def _assert_obsolete(self, log):
        audit = (log.request_payload, log.response_payload, log.http_status, log.attempt_count)
        with patch.object(outbound.requests, "post") as post:
            self.assertFalse(log._process_outbound())
            post.assert_not_called()
        self.assertEqual(log.state, "superseded")
        self.assertFalse(log.next_retry_at)
        self.assertTrue(log.error_message)
        self.assertEqual(audit, (log.request_payload, log.response_payload, log.http_status, log.attempt_count))

    def test_two_statuses_in_same_second_have_distinct_jobs(self):
        with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 1)):
            preparing = self._queue("preparing")
            cancelled = self._queue("cancelled")
        self.assertNotEqual(preparing, cancelled)
        self.assertNotEqual(preparing.idempotency_key, cancelled.idempotency_key)
        self.assertEqual(preparing.request_payload["event_time"], cancelled.request_payload["event_time"])
        self.assertEqual(cancelled.request_payload["status"], "CANCELED")

    def test_cashier_orders_do_not_queue_status_notifications(self):
        self.order.write({
            "blend_partner_reference": False,
            "blend_order_id": "previously-exported-cashier-order",
        })
        self.order.write({"state": "paid"})
        self.order.write({"state": "cancel"})
        for status in ("accepted", "preparing", "cancelled"):
            self.assertFalse(self._queue(status))

    def test_mixed_orders_only_queue_incoming_blend_status(self):
        cashier_order = self._order()
        cashier_order.blend_partner_reference = False
        (cashier_order | self.order)._blend_enqueue_status("preparing")
        logs = self.logs.search([
            ("order_id", "in", (cashier_order | self.order).ids),
            ("event", "in", ["order.updated", "order_updated"]),
        ])
        self.assertEqual(logs.order_id, self.order)
        self.assertEqual(len(logs), 1)

    def test_preexisting_cashier_status_retry_is_not_sent(self):
        self.order.write({
            "blend_partner_reference": False,
            "blend_order_id": "previously-exported-cashier-order",
        })
        for event, payload in (
            ("order.updated", self.order._blend_status_payload("accepted")),
            ("order_updated", {"payload": {"status": "delivered"}}),
        ):
            with self.subTest(event=event):
                log = self.logs.enqueue(self.config, event, payload, order=self.order)
                log.write({
                    "state": "retry", "attempt_count": 1, "http_status": 503,
                    "response_payload": {"message": "Previous failed attempt"},
                    "next_retry_at": datetime(2030, 1, 1),
                })
                self._assert_obsolete(log)

    def test_consecutive_callbacks_reuse_the_job_even_after_success(self):
        first = self._queue("preparing")
        with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 1)):
            self.assertEqual(self._queue("preparing"), first)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})) as post:
            self.assertTrue(first._process_outbound())
            with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 2)):
                repeated = self._queue("preparing")
            self.assertEqual(repeated, first)
            repeated.action_process_now()
            self.assertEqual(post.call_count, 1)

    def test_later_distinct_transition_is_not_confused_with_previous_occurrence(self):
        self.config.blend_outbound_webhook_url = False
        with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 1)):
            first = self._queue("assigned")
            middle = self._queue("at_pick_up")
            last = self._queue("assigned")
        self.assertEqual(len(first | middle | last), 3)
        self._assert_obsolete(first)
        self._assert_obsolete(middle)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.assertTrue(last._process_outbound())
        self.assertEqual(post.call_args.kwargs["json"]["payload"]["status"], "assigned")

    def test_newer_cancellation_supersedes_preparing_before_delivery(self):
        preparing = self._queue("preparing")
        self._queue("cancelled")
        # The legacy selector sends a status without changing blend_status.
        self.assertEqual(self.order.blend_status, "pending")
        self._assert_obsolete(preparing)

    def test_cancel_delivery_then_manual_retry_cannot_send_old_preparing(self):
        preparing = self._queue("preparing")
        with patch.object(outbound.requests, "post", return_value=FakeResponse(503, {"error": "offline"})):
            self.assertFalse(preparing._process_outbound())
        cancelled = self._queue("cancelled")
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})) as post:
            self.assertTrue(cancelled._process_outbound())
            preparing.action_retry()
            self.assertFalse(preparing._process_outbound())
            self.assertEqual(post.call_count, 1)
        self.assertEqual(preparing.state, "superseded")
        self.assertEqual(preparing.http_status, 503)
        self.assertEqual(preparing.response_payload, {"error": "offline"})

    def test_inbound_terminal_update_makes_waiting_preparing_obsolete(self):
        log = self._queue("preparing")
        result = BlendPOSAPIService(self.env, self.config).update_order({
            "status": "CANCELED", "brand_id": self.brand_id,
            "branch_id": self.branch_id, "order_id": self.order.uuid,
        })
        self.assertTrue(result["success"])
        self._assert_obsolete(log)
        self.assertEqual(self.logs.search_count([
            ("order_id", "=", self.order.id), ("direction", "=", "outbound"),
        ]), 1)  # no echo of an inbound update

    def test_later_local_prepared_state_supersedes_preparing(self):
        log = self._queue("preparing")
        self.order.blend_status = "prepared"
        self._assert_obsolete(log)

    def test_legacy_driver_status_is_superseded_by_later_status(self):
        self.config.blend_outbound_webhook_url = False
        assigned = self._queue("assigned")
        with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 1)):
            delivered = self._queue("delivered")
        self._assert_obsolete(assigned)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.assertTrue(delivered._process_outbound())
        self.assertEqual(post.call_args.args[0], self.config.blend_status_url)
        self.assertEqual(post.call_args.kwargs["json"]["payload"]["status"], "delivered")

    def test_pre_upgrade_duplicates_are_suppressed_in_either_processing_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.order = self._order()
                first = self._queue("preparing")
                second = self.logs.enqueue(
                    self.config, first.event, first.request_payload, order=self.order,
                    idempotency_key="old-preparing:%s" % self.order.uuid,
                )
                one, two = (second, first) if reverse else (first, second)
                with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})) as post:
                    self.assertTrue(one._process_outbound())
                    self.assertFalse(two._process_outbound())
                    self.assertEqual(post.call_count, 1)
                self.assertEqual(two.state, "superseded")

    def test_timeout_retries_current_status_with_unchanged_payload(self):
        log = self._queue("preparing")
        with patch.object(outbound.requests, "post", side_effect=[
            requests.Timeout("lost acknowledgment"), FakeResponse(200, {"success": True}),
        ]) as post:
            self.assertFalse(log._process_outbound())
            self.assertTrue(log._process_outbound())
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])

    def test_deleted_order_does_not_send_a_status(self):
        log = self._queue("preparing")
        self.order.with_context(blend_skip_status_sync=True).unlink()
        log.invalidate_recordset(["order_id"])
        self._assert_obsolete(log)

    def test_another_orders_newer_status_does_not_suppress_this_order(self):
        first = self._queue("preparing")
        self._queue("cancelled", self._order())
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})):
            self.assertTrue(first._process_outbound())

    def test_legacy_status_also_checks_order_lock_before_http(self):
        self.config.blend_outbound_webhook_url = False
        log = self._queue("assigned")
        with patch.object(type(self.order), "try_lock_for_update", return_value=self.order.browse()), \
                patch.object(outbound.requests, "post") as post:
            self.assertFalse(log._process_outbound())
            post.assert_not_called()
        self.assertEqual(log.attempt_count, 0)
        with patch.object(type(self.order), "try_lock_for_update", side_effect=SerializationFailure("stale snapshot")), \
                patch.object(outbound.requests, "post") as post:
            with self.assertRaises(SerializationFailure):
                log._process_outbound()
            post.assert_not_called()

    def test_http_200_explicit_failure_is_not_a_success_for_any_event(self):
        self.config.blend_legacy_order_creation_enabled = True
        cases = [
            ("order.updated", self.order._blend_status_payload("preparing")),
            ("order_updated", {"payload": {"status": "assigned"}}),
            ("order_created", {}),
            ("menu.updated", {"entity": {"id": "1", "type": "category"}}),
            ("item.updated", {"product_id": "1", "is_stock": False}),
            ("store.updated", {"status": False}),
        ]
        for event, data in cases:
            with self.subTest(event=event):
                payload = {**data, "event": event, "brand_id": self.brand_id, "branch_id": self.branch_id}
                log = self.logs.enqueue(self.config, event, payload, order=self.order)
                # Even a contradictory status=success must not override success=false.
                response = {"success": False, "status": "success", "order_id": "must-not-save"}
                with patch.object(outbound.requests, "post", return_value=FakeResponse(200, response)):
                    self.assertFalse(log._process_outbound())
                self.assertEqual(log.state, "retry")
                self.assertEqual(log.http_status, 200)
                self.assertEqual(log.response_payload, response)
                self.assertTrue(log.next_retry_at)
                self.assertFalse(self.order.blend_order_id)

    def test_failed_acknowledgment_can_recover_without_a_new_job(self):
        log = self._queue("preparing")
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": False})):
            for _attempt in range(self.config.blend_retry_max):
                self.assertFalse(log._process_outbound())
        self.assertEqual(log.state, "failed")
        log.action_retry()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})):
            self.assertTrue(log._process_outbound())
        self.assertEqual(log.state, "success")

    def test_existing_success_response_contracts_stay_compatible(self):
        for http_status, body in ((200, {"status": "success"}), (200, {"success": True}), (204, {})):
            with self.subTest(http_status=http_status, body=body):
                log = self._queue("preparing", self._order())
                with patch.object(outbound.requests, "post", return_value=FakeResponse(http_status, body)):
                    self.assertTrue(log._process_outbound())

    def test_queue_and_acknowledgment_write_snapshot_fences(self):
        self.assertEqual(self.order.blend_status_sync_revision, 0)
        first = self._queue("preparing")
        self.assertEqual(self.order.blend_status_sync_revision, 1)
        self.assertEqual(self._queue("preparing"), first)
        self.assertEqual(self.order.blend_status_sync_revision, 1)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": False})):
            self.assertFalse(first._process_outbound())
        self.assertEqual(self.order.blend_status_sync_revision, 1)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": True})):
            self.assertTrue(first._process_outbound())
        self.assertEqual(self.order.blend_status_sync_revision, 2)
        cancelled = self._queue("cancelled")
        self.assertEqual(self.order.blend_status_sync_revision, 3)
        # Verify the fence is persisted, not just cached, under the order lock.
        self.env.cr.execute("SELECT blend_status_sync_revision FROM pos_order WHERE id = %s", [self.order.id])
        self.assertEqual(self.env.cr.fetchone()[0], 3)
        self.assertNotEqual(first, cancelled)
        self.assertFalse(self.order._fields["blend_status_sync_revision"].copy)

    def test_failed_acceptance_ack_does_not_record_a_delivery_marker(self):
        log = self._queue("accepted")
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"success": False})):
            self.assertFalse(log._process_outbound())
        self.assertFalse(self.order.blend_accepted_delivery_log_id)
        self.assertEqual(self.order.blend_status_sync_revision, 1)
        self.assertEqual(self.order.blend_decision_sync_state, "retry")

    def test_rollback_removes_queue_and_revision_without_http(self):
        with patch.object(outbound.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "rollback test"), self.env.cr.savepoint():
                log = self._queue("preparing")
                log_id = log.id
                self.assertEqual(self.order.blend_status_sync_revision, 1)
                raise ValueError("rollback test")
            post.assert_not_called()
        self.assertFalse(self.logs.browse(log_id).exists())
        self.assertEqual(self.order.blend_status_sync_revision, 0)

    def test_uncommitted_status_is_not_visible_to_another_transaction(self):
        log = self._queue("preparing")
        self.env.flush_all()
        with self.registry.cursor() as other_cr:
            other_cr.execute("SELECT id FROM blend_integration_log WHERE id = %s", [log.id])
            self.assertFalse(other_cr.fetchall())

    def test_latest_transition_can_repeat_a_status_delivered_before_a_different_intent(self):
        self.config.blend_outbound_webhook_url = False
        first = self._queue("assigned")
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.assertTrue(first._process_outbound())
            middle = self._queue("at_pick_up")
            last = self._queue("assigned")
            self.assertNotEqual(first, last)
            self.assertFalse(middle._process_outbound())
            self.assertTrue(last._process_outbound())
            self.assertEqual(post.call_count, 2)

    def test_wrong_company_link_fails_before_http(self):
        other_company = self.env["res.company"].create({"name": "Blend queue isolation test"})
        log = self._queue("preparing")
        log.company_id = other_company
        with patch.object(outbound.requests, "post") as post:
            self.assertFalse(log._process_outbound())
            post.assert_not_called()
        self.assertEqual(log.state, "retry")
        self.assertIn("branch/company", log.error_message)

    def test_multirecord_queue_keeps_orders_separate(self):
        orders = self.order | self._order()
        with patch.object(outbound.requests, "post") as post:
            orders._blend_enqueue_status("preparing")
            orders._blend_enqueue_status("preparing")
            post.assert_not_called()
        logs = self.logs.search([
            ("order_id", "in", orders.ids), ("event", "=", "order.updated"),
            ("direction", "=", "outbound"),
        ])
        self.assertEqual(len(logs), 2)
        self.assertEqual(set(logs.mapped("order_id").ids), set(orders.ids))
        self.assertEqual(orders.mapped("blend_status_sync_revision"), [1, 1])

