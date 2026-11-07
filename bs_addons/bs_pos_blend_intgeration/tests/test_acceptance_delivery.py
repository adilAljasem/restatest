import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import requests
from psycopg2.errors import SerializationFailure

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user, tagged

from ..services import outbound
from ..services.pos_api import BlendPOSAPIService
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendAcceptanceDelivery(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.config.write({
            "blend_outbound_webhook_url": "https://blend.example.test/events",
            "blend_auto_accept": True,
        })
        self.session = self.env["pos.session"].create({"config_id": self.config.id})
        self.api = BlendPOSAPIService(self.env, self.config)
        self.order = self._order()

    def _order(self, inbound=True):
        reference = str(uuid.uuid4())
        return self.env["pos.order"].create({
            "session_id": self.session.id, "config_id": self.config.id,
            "uuid": reference, "pos_reference": "ACCEPTANCE-TEST-%s" % reference,
            "amount_tax": 0, "amount_total": 0, "amount_paid": 0, "amount_return": 0,
            "blend_status": "pending",
            "blend_partner_reference": "test:%s" % reference if inbound else False,
        })

    def _logs(self, order=None):
        return self.env["blend.integration.log"].search([
            ("order_id", "=", (order or self.order).id),
            ("event", "=", "order.updated"), ("direction", "=", "outbound"),
        ])

    def _accept(self):
        self.order._blend_auto_accept()
        log = self._logs()
        self.assertEqual(len(log), 1)
        return log

    def _update(self, status="PREPARING"):
        result = self.api.update_order({
            "status": status, "brand_id": self.brand_id,
            "branch_id": self.branch_id, "order_id": self.order.uuid,
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], status)

    def _assert_superseded_without_http(self, log):
        payload = log.request_payload
        response = log.response_payload
        attempts = log.attempt_count
        with patch.object(outbound.requests, "post") as post:
            self.assertFalse(log._process_outbound())
            post.assert_not_called()
        self.assertEqual(log.state, "superseded")
        self.assertTrue(log.error_message)
        self.assertFalse(log.next_retry_at)
        self.assertEqual(log.attempt_count, attempts)
        self.assertEqual(log.request_payload, payload)
        self.assertEqual(log.response_payload, response)

    def test_inbound_preparing_does_not_echo_acceptance(self):
        self._update()
        self._update()
        self.assertEqual(self.order.blend_status, "preparing")
        self.assertFalse(self._logs())

    def test_waiting_acceptance_is_superseded_after_preparing(self):
        log = self._accept()
        self._update()
        self._assert_superseded_without_http(log)
        self.assertEqual(self.order.blend_status, "preparing")
        self.assertEqual(self.order.blend_decision_sync_state, "superseded")

    def test_timeout_then_preparing_does_not_resend_old_acceptance(self):
        log = self._accept()
        with patch.object(outbound.requests, "post", side_effect=requests.Timeout("lost acknowledgment")) as post:
            self.assertFalse(log._process_outbound())
            self.assertEqual(post.call_count, 1)
        self.assertEqual(log.state, "retry")
        self.assertFalse(self.order.blend_accepted_delivery_log_id)
        self._update()
        self._assert_superseded_without_http(log)
        self.assertEqual(log.attempt_count, 1)

    def test_error_response_is_preserved_when_acceptance_becomes_obsolete(self):
        log = self._accept()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(503, {"error": "unavailable"})):
            log._process_outbound()
        self._update()
        self._assert_superseded_without_http(log)
        self.assertEqual(log.http_status, 503)

    def test_manual_reset_cannot_revive_an_obsolete_acceptance(self):
        log = self._accept()
        with patch.object(outbound.requests, "post", side_effect=requests.Timeout("timeout")):
            log._process_outbound()
        self._update()
        log.action_retry()
        self._assert_superseded_without_http(log)
        with self.assertRaises(UserError):
            log.action_retry()
        with patch.object(outbound.requests, "post") as post:
            log.action_process_now()
            post.assert_not_called()

    def test_successful_log_is_not_sent_again_by_any_entry_point(self):
        log = self._accept()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            log.action_process_now()
            log.action_process_now()
            self.assertFalse(outbound.BlendOutboundService(self.env).send(log))
            self.assertEqual(post.call_count, 1)
        self.assertEqual(log.state, "success")
        self.assertEqual(log.attempt_count, 1)

    def test_timeout_still_retries_when_acceptance_is_current(self):
        log = self._accept()
        with patch.object(outbound.requests, "post", side_effect=[
            requests.Timeout("lost acknowledgment"), FakeResponse(200, {"status": "success"}),
        ]) as post:
            self.assertFalse(log._process_outbound())
            self.assertTrue(log._process_outbound())
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args_list[0].kwargs["json"], post.call_args_list[1].kwargs["json"])
        self.assertEqual(log.attempt_count, 2)
        self.assertEqual(log.state, "success")
        self.assertEqual(self.order.blend_accepted_delivery_log_id, log)

    def test_automatic_and_manual_acceptance_payment_callbacks_share_one_job(self):
        for automatic in (True, False):
            with self.subTest(automatic=automatic):
                order = self._order()
                if automatic:
                    order._blend_auto_accept()
                else:
                    order.action_blend_accept_order()
                original = self._logs(order)
                with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 1)):
                    order.action_pos_order_paid()
                    order._process_saved_order(False)
                self.assertEqual(self._logs(order), original)

    def test_orders_without_partner_reference_also_have_stable_acceptance_key(self):
        order = self._order(inbound=False)
        order.action_pos_order_paid()
        original = self._logs(order)
        with patch("odoo.fields.Datetime.now", return_value=datetime(2030, 1, 1)):
            order._process_saved_order(False)
        self.assertEqual(len(original), 1)
        self.assertEqual(self._logs(order), original)

    def test_payment_after_preparing_does_not_enqueue_acceptance(self):
        self._update()
        self.order.action_pos_order_paid()
        self.order._process_saved_order(False)
        self.assertFalse(self._logs())
        self.assertEqual(self.order.blend_status, "preparing")

    def test_terminal_and_later_statuses_supersede_acceptance(self):
        for status in ("prepared", "delivered", "rejected", "cancelled", "time_out"):
            with self.subTest(status=status):
                self.order = self._order()
                log = self._accept()
                self.order.blend_status = status
                self._assert_superseded_without_http(log)

    def test_native_cancel_also_supersedes_acceptance(self):
        log = self._accept()
        self.order.with_context(blend_skip_status_sync=True).action_pos_order_cancel()
        self._assert_superseded_without_http(log)

    def test_newer_outgoing_preparing_supersedes_acceptance(self):
        log = self._accept()
        self.order._blend_enqueue_status("preparing")
        # The legacy status selector does not modify blend_status itself.
        self.assertEqual(self.order.blend_status, "accepted")
        self._assert_superseded_without_http(log)

    def test_pre_upgrade_duplicate_jobs_send_acceptance_only_once(self):
        first = self._accept()
        second = self.env["blend.integration.log"].enqueue(
            self.config, first.event, first.request_payload, order=self.order,
            idempotency_key="legacy-timed-acceptance:%s" % self.order.uuid,
        )
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.assertTrue(first._process_outbound())
            self.assertFalse(second._process_outbound())
            self.assertEqual(post.call_count, 1)
        self.assertEqual(second.state, "superseded")

    def test_deleted_order_does_not_send_old_acceptance(self):
        log = self._accept()
        self.order.with_context(blend_skip_status_sync=True).unlink()
        log.invalidate_recordset(["order_id"])
        self._assert_superseded_without_http(log)

    def test_delivered_acceptance_marker_is_written_and_not_copied(self):
        log = self._accept()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})):
            log._process_outbound()
        self.assertEqual(self.order.blend_accepted_delivery_log_id, log)
        self.assertFalse(self.order._fields["blend_accepted_delivery_log_id"].copy)

    def test_another_order_delivery_does_not_suppress_this_order(self):
        first = self._accept()
        second_order = self._order()
        second_order._blend_auto_accept()
        second = self._logs(second_order)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.assertTrue(first._process_outbound())
            self.assertTrue(second._process_outbound())
            self.assertEqual(post.call_count, 2)

    def test_terminal_queue_states_cannot_be_processed_directly(self):
        log = self._accept()
        for state in ("processing", "success", "failed", "duplicate", "superseded"):
            with self.subTest(state=state):
                log.state = state
                with patch.object(outbound.requests, "post") as post:
                    self.assertFalse(log._process_outbound())
                    post.assert_not_called()
                self.assertEqual(log.state, state)
                self.assertEqual(log.attempt_count, 0)

    def test_busy_log_or_order_is_not_sent_or_counted_as_failure(self):
        log = self._accept()
        for model in (log, self.order):
            with self.subTest(model=model._name):
                with patch.object(type(model), "try_lock_for_update", return_value=model.browse()), \
                        patch.object(outbound.requests, "post") as post:
                    self.assertFalse(log._process_outbound())
                    post.assert_not_called()
                self.assertEqual(log.state, "pending")
                self.assertEqual(log.attempt_count, 0)

    def test_changed_snapshot_is_retried_before_any_http(self):
        log = self._accept()
        with patch.object(type(self.order), "try_lock_for_update", side_effect=SerializationFailure("changed snapshot")), \
                patch.object(outbound.requests, "post") as post:
            with self.assertRaises(SerializationFailure):
                log._process_outbound()
            post.assert_not_called()
        self.assertEqual(log.state, "pending")
        self.assertEqual(log.attempt_count, 0)

    def test_cron_and_process_now_do_not_duplicate_delivery(self):
        log = self._accept()
        # Isolate this test job from unrelated fixture/database work.
        self.env["blend.integration.log"].search([
            ("id", "!=", log.id), ("direction", "=", "outbound"),
            ("state", "in", ["pending", "retry", "processing"]),
        ]).write({"state": "failed", "next_retry_at": False})
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.env["blend.integration.log"]._cron_process_outbound()
            log.action_process_now()
            self.env["blend.integration.log"]._cron_process_outbound()
            self.assertEqual(post.call_count, 1)

    def test_processing_recovery_skips_a_locked_worker(self):
        log = self._accept()
        log.write({"state": "processing", "last_attempt_at": fields.Datetime.now() - timedelta(minutes=11)})
        with patch.object(type(log), "try_lock_for_update", return_value=log.browse()), \
                patch.object(outbound.requests, "post") as post:
            self.env["blend.integration.log"]._cron_process_outbound()
            post.assert_not_called()
        self.assertEqual(log.state, "processing")

    def test_non_admin_cannot_process_queue_via_public_action(self):
        log = self._accept()
        user = new_test_user(self.env, login="blend-queue-user-%s" % uuid.uuid4().hex,
                             groups="base.group_user,point_of_sale.group_pos_user")
        with patch.object(outbound.requests, "post") as post, self.assertRaises(AccessError):
            log.with_user(user).action_process_now()
        post.assert_not_called()
