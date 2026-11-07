import uuid
from datetime import datetime
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import new_test_user, tagged
from odoo.tools.safe_eval import safe_eval

from ..services import outbound
from ..services.exceptions import BlendAPIError
from ..services.menu import BlendMenuService
from ..services.pos_api import BlendPOSAPIService
from .common import BlendTransactionCase, sample_menu, configure_blend_preset
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendAutoAccept(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.sync_sample_menu()
        self.session = self.env["pos.session"].create({"config_id": self.config.id})
        self.session.set_opening_control(0, "")
        self.service = BlendPOSAPIService(self.env, self.config)

    def _enable(self):
        self.config.write({"blend_schema": "pos", "blend_auto_accept": True})

    def _payload(self, status="PENDING"):
        reference = uuid.uuid4().hex
        return {
            "branch_id": self.branch_id, "brand_id": self.brand_id,
            "partner_reference": "jahez: %s" % reference,
            "aggregator": "jahez", "aggregator_order_id": reference,
            "order_type": "delivery", "status": status,
            "subtotal": "62.00", "tax": "0.00", "total": "62.00",
            "products": [{
                "id": "100", "quantity": 1, "total": 50,
                "modifiers": [{"id": "200", "quantity": 1, "total": "12.00"}],
            }],
        }

    def _create(self, payload=None):
        response = self.service.create_order(payload or self._payload())
        self.assertTrue(response["success"])
        return self.env["pos.order"].search([("uuid", "=", response["order_id"])])

    def _acceptance_logs(self, order=None):
        domain = [
            ("config_id", "=", self.config.id),
            ("direction", "=", "outbound"),
            ("event", "=", "order.updated"),
        ]
        if order:
            domain.append(("order_id", "=", order.id))
        return self.env["blend.integration.log"].search(domain)

    def test_default_off_preserves_pending(self):
        self.assertFalse(self.config.blend_auto_accept)
        self.assertFalse(self.config._fields["blend_auto_accept"].copy)
        order = self._create()
        self.assertEqual(order.blend_status, "pending")
        self.assertEqual(order.state, "draft")
        self.assertFalse(self._acceptance_logs())

    def test_auto_accept_queues_without_paying_or_http(self):
        self._enable()
        with patch.object(outbound.requests, "post") as post:
            order = self._create()
            post.assert_not_called()
        self.assertEqual(order.blend_status, "accepted")
        self.assertEqual(order.blend_payload["status"], "PENDING")
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.amount_paid, 0.0)
        self.assertFalse(order.payment_ids)
        self.assertEqual(order.amount_total, 62.0)
        log = self._acceptance_logs(order)
        self.assertEqual(len(log), 1)
        self.assertEqual(log.state, "pending")
        self.assertEqual(log.attempt_count, 0)
        self.assertEqual(log.company_id, order.company_id)
        self.assertEqual(log.endpoint_url, self.config.blend_status_url)
        self.assertEqual(log.request_payload["status"], "ACCEPTED")
        self.assertEqual(log.request_payload["order_id"], order.uuid)
        self.assertEqual(log.request_payload["branch_id"], self.branch_id)
        self.assertEqual(log.request_payload["brand_id"], self.brand_id)

    def test_scheduled_auto_accept_and_mark_prepared_are_independent(self):
        self._enable()
        payload = self._payload("SCHEDULED")
        payload.update({
            "schedule_at": "2026-10-01 18:30:00",
            "note": "Please add extra napkins",
        })
        order = self._create(payload)
        self.assertEqual(order.blend_status, "accepted")
        self.assertEqual(order.blend_payload["status"], "SCHEDULED")
        self.assertEqual(order.blend_schedule_at, payload["schedule_at"])
        self.assertIn(payload["schedule_at"], order.general_customer_note)
        self.assertIn(payload["note"], order.general_customer_note)
        self.assertFalse(order.blend_scheduled_prepared_at)
        self.assertEqual(len(self._acceptance_logs(order)), 1)
        action = self.env.ref("bs_pos_blend_intgeration.action_blend_scheduled_orders")
        self.assertIn(order, self.env["pos.order"].search(safe_eval(action.domain)))
        order.action_blend_mark_scheduled_prepared()
        self.assertTrue(order.blend_scheduled_prepared_at)
        self.assertEqual(order.blend_scheduled_prepared_by, self.env.user)
        self.assertNotIn(order, self.env["pos.order"].search(safe_eval(action.domain)))
        self.assertEqual(len(self._acceptance_logs(order)), 1)
        prepared_at = order.blend_scheduled_prepared_at
        order.action_blend_mark_scheduled_prepared()
        self.assertEqual(order.blend_scheduled_prepared_at, prepared_at)

    def test_scheduled_requires_valid_due_time(self):
        for schedule_at in (None, "", "2026-99-01 18:30:00", "tomorrow"):
            with self.subTest(schedule_at=schedule_at), self.assertRaises(BlendAPIError):
                payload = self._payload("SCHEDULED")
                payload["schedule_at"] = schedule_at
                self.service.create_order(payload)

    def test_scheduled_without_auto_accept_can_be_manually_accepted(self):
        self.config.blend_schema = "pos"
        payload = self._payload("SCHEDULED")
        payload["schedule_at"] = "2026-10-01 18:30:00"
        order = self._create(payload)
        self.assertEqual(order.blend_status, "scheduled")
        self.assertTrue(order.blend_can_decide)
        order.action_blend_accept_order()
        self.assertEqual(order.blend_status, "accepted")
        self.assertEqual(len(self._acceptance_logs(order)), 1)

    def test_update_to_scheduled_adds_due_time_and_auto_accepts_once(self):
        self._enable()
        order = self._create(self._payload("ACCEPTED"))
        self.assertFalse(order.blend_schedule_at)
        self.service.update_order({
            "order_id": order.uuid, "branch_id": self.branch_id,
            "brand_id": self.brand_id, "status": "SCHEDULED",
            "schedule_at": "2026-10-01 18:30:00", "note": "Extra napkins",
        })
        self.assertEqual(order.blend_schedule_at, "2026-10-01 18:30:00")
        self.assertEqual(order.blend_status, "accepted")
        self.assertIn("Extra napkins", order.general_customer_note)
        self.assertEqual(len(self._acceptance_logs(order)), 1)

    def test_create_replay_and_later_acceptance_share_one_event(self):
        self._enable()
        payload = self._payload()
        order = self._create(payload)
        original_log = self._acceptance_logs(order)
        original_payload = original_log.request_payload
        for _attempt in range(2):
            self.assertEqual(self._create(payload), order)
            order._blend_auto_accept()
        # The payment callback also calls this method, at a later event time.
        with patch("odoo.addons.bs_pos_blend_intgeration.models.pos_order.fields.Datetime.now",
                   return_value=datetime(2030, 1, 2, 3, 4, 5)):
            order._blend_enqueue_status("accepted")
        self.assertEqual(self._acceptance_logs(order), original_log)
        self.assertEqual(original_log.request_payload, original_payload)

    def test_existing_pending_order_is_not_accepted_on_create_replay(self):
        payload = self._payload()
        order = self._create(payload)
        self._enable()
        self.assertEqual(self._create(payload), order)
        self.assertEqual(order.blend_status, "pending")
        self.assertFalse(self._acceptance_logs())

    def test_auto_accept_only_changes_pending_status(self):
        self._enable()
        for status in ("ACCEPTED", "PREPARING", "ASSIGNED", "READY_TO_PICK_UP", "AT_PICK_UP", "ON_THE_WAY", "COMPLETED", "CANCELED", "REJECTED", "TIME_OUT"):
            with self.subTest(status=status):
                order = self._create(self._payload(status))
                self.assertEqual(order.blend_status, self.service.ALLOWED_ORDER_STATUSES[status])
                self.assertEqual(order.state, "cancel" if status in self.service.TERMINAL_ORDER_STATUSES else "draft")
                self.assertFalse(self._acceptance_logs(order))

    def test_update_order_accepts_advanced_delivery_statuses_without_accounting(self):
        order = self._create()
        for status in ("READY_TO_PICK_UP", "AT_PICK_UP", "ON_THE_WAY", "COMPLETED"):
            with self.subTest(status=status):
                response = self.service.update_order({
                    "order_id": order.uuid, "branch_id": self.branch_id,
                    "brand_id": self.brand_id, "status": status,
                })
                self.assertEqual(response["status"], status)
                self.assertEqual(order.blend_status, status.lower())
                self.assertEqual(order.state, "draft")
                self.assertFalse(order.payment_ids or order.account_move)

    def test_validation_failure_never_queues_acceptance(self):
        self._enable()
        payload = self._payload()
        payload["products"][0]["total"] = 49
        with self.assertRaises(BlendAPIError):
            self.service.create_order(payload)
        self.assertFalse(self.env["pos.order"].search([
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ]))
        self.assertFalse(self._acceptance_logs())

    def test_later_failure_rolls_back_order_and_acceptance_together(self):
        self._enable()
        payload = self._payload()
        with patch.object(type(self.config), "notify_synchronisation", side_effect=RuntimeError("test rollback")):
            with self.assertRaisesRegex(RuntimeError, "test rollback"):
                self.service.create_order(payload)
        self.assertFalse(self.env["pos.order"].search([
            ("blend_partner_reference", "=", payload["partner_reference"]),
        ]))
        self.assertFalse(self._acceptance_logs())

    def test_failed_delivery_retries_without_new_order_or_event(self):
        self._enable()
        payload = self._payload()
        order = self._create(payload)
        log = self._acceptance_logs(order)
        outbound_payload = log.request_payload
        with patch.object(outbound.requests, "post", return_value=FakeResponse(503, {"status": "failed"})):
            self.assertFalse(log._process_outbound())
        self.assertEqual(log.state, "retry")
        self.assertEqual(order.blend_status, "accepted")
        self.assertEqual(self._create(payload), order)
        self.assertEqual(self._acceptance_logs(order), log)
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})) as post:
            self.assertTrue(log._process_outbound())
            self.assertEqual(post.call_args.kwargs["json"], outbound_payload)
        self.assertEqual(log.state, "success")
        self.assertEqual(log.attempt_count, 2)
        self.assertEqual(order.state, "draft")

    def test_setting_is_per_pos_not_global(self):
        self._enable()
        second_config = self.env["pos.config"].create({
            "name": "Blend manual acceptance branch",
            "blend_enabled": True, "blend_branch_id": self.branch_id + "-manual",
            "blend_brand_id": self.brand_id, "blend_inbound_token": self.token,
            "payment_method_ids": [(6, 0, [])],
        })
        self.assertFalse(second_config.blend_auto_accept)
        configure_blend_preset(second_config)
        session = self.env["pos.session"].create({"config_id": second_config.id})
        session.set_opening_control(0, "")
        BlendMenuService(self.env, second_config).sync_menu(sample_menu(second_config.blend_branch_id))
        second_config.write({
            "limit_categories": True,
            "iface_available_categ_ids": [(6, 0, self.env["pos.category"].search([
                ("blend_config_id", "=", second_config.id),
            ]).ids)],
        })
        payload = self._payload()
        payload["branch_id"] = second_config.blend_branch_id
        result = BlendPOSAPIService(self.env, self.config | second_config).create_order(payload)
        order = self.env["pos.order"].search([("uuid", "=", result["order_id"])])
        self.assertEqual(order.config_id, second_config)
        self.assertEqual(order.blend_status, "pending")

    def test_config_rejects_incompatible_settings_and_later_changes(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.config.blend_auto_accept = True  # primary schema
        self.config.blend_schema = "pos"
        for invalid_url in (False, "", "http://blend.example.test/status", "https://", "not-a-url"):
            with self.subTest(url=invalid_url):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.config.write({"blend_status_url": invalid_url, "blend_auto_accept": True})
        self._enable()
        for invalid_values in ({"blend_schema": "primary"}, {"blend_status_url": False}, {"blend_brand_id": False}):
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.config.write(invalid_values)
        self.config.write({"blend_auto_accept": False, "blend_schema": "primary", "blend_status_url": False})

    def test_pos_user_cannot_enable_auto_accept_through_rpc(self):
        user = new_test_user(
            self.env, login="blend-auto-accept-pos-%s" % uuid.uuid4().hex,
            groups="point_of_sale.group_pos_manager",
        )
        with self.assertRaises(AccessError):
            self.config.with_user(user).write({"blend_auto_accept": True})
