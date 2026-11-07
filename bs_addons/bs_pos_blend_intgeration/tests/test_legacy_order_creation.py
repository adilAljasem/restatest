from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from ..services import outbound
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendLegacyOrderCreation(BlendTransactionCase):
    def test_default_off_preserves_url_and_does_not_build_or_queue(self):
        self.assertFalse(self.config.blend_legacy_order_creation_enabled)
        self.assertFalse(self.config._fields["blend_legacy_order_creation_enabled"].copy)
        order = self.create_order()
        original_payload = order.blend_payload
        with patch.object(type(order), "_blend_build_order_created_payload") as build:
            order._blend_enqueue_order_created()
        build.assert_not_called()
        self.assertEqual(order.blend_payload, original_payload)
        self.assertFalse(self.env["blend.integration.log"].search([
            ("order_id", "=", order.id), ("event", "=", "order_created"),
        ]))
        self.assertEqual(self.config.blend_order_url, "https://blend.example.test/orders")

    def test_direct_enqueue_cannot_bypass_disabled_switch(self):
        log = self.env["blend.integration.log"].enqueue(
            self.config, "order_created", {"event": "order_created"},
        )
        self.assertFalse(log)

    def test_pending_job_and_manual_retry_blocked_until_enabled(self):
        self.config.blend_legacy_order_creation_enabled = True
        order = self.create_order()
        order._blend_enqueue_order_created()
        log = self.env["blend.integration.log"].search([
            ("order_id", "=", order.id), ("event", "=", "order_created"),
        ])
        self.assertEqual(len(log), 1)
        payload, url = log.request_payload, log.endpoint_url
        self.config.blend_legacy_order_creation_enabled = False
        with patch.object(outbound.requests, "post") as send:
            log.action_process_now()
            self.assertEqual(log.state, "failed")
            log.action_retry()
            log.action_process_now()
        send.assert_not_called()
        self.assertEqual(log.state, "failed")
        self.assertIn("Legacy order creation is disabled", log.error_message)
        self.assertEqual(log.attempt_count, 0)
        self.assertFalse(log.next_retry_at)
        self.assertEqual(log.request_payload, payload)
        self.assertEqual(log.endpoint_url, url)
        self.config.blend_legacy_order_creation_enabled = True
        log.action_retry()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(
            200, {"status": "success", "order_id": "legacy-opt-in-test"},
        )) as send:
            log.action_process_now()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(log.state, "success")

    def test_switch_is_per_config_and_copy_starts_disabled(self):
        self.config.blend_legacy_order_creation_enabled = True
        other = self.config.copy({"name": "Legacy disabled copy"})
        logs = self.env["blend.integration.log"]
        self.assertTrue(logs.enqueue(self.config, "order_created", {"event": "order_created"}))
        self.assertFalse(logs.enqueue(other, "order_created", {"event": "order_created"}))
        self.assertFalse(other.blend_legacy_order_creation_enabled)

    def test_pos_operator_cannot_enable_legacy_via_rpc(self):
        user = new_test_user(
            self.env, login="legacy-toggle-%s" % self.config.id,
            groups="base.group_user,point_of_sale.group_pos_manager",
        )
        with self.assertRaises(AccessError):
            self.config.with_user(user).write({"blend_legacy_order_creation_enabled": True})
        self.assertFalse(self.config.blend_legacy_order_creation_enabled)

    def test_other_events_still_queue_when_legacy_disabled(self):
        self.config.blend_outbound_webhook_url = "https://blend.example.test/webhook/test"
        for event in ("menu.updated", "item.updated", "store.updated", "order.updated"):
            log = self.env["blend.integration.log"].enqueue(self.config, event, {"event": event})
            self.assertTrue(log)
            self.assertEqual(log.endpoint_url, self.config.blend_outbound_webhook_url)
