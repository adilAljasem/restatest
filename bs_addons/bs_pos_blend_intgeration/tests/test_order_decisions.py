import uuid
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import new_test_user, tagged

from ..services import outbound
from ..services.pos_api import BlendPOSAPIService
from .common import BlendTransactionCase
from .test_outbound import FakeResponse


@tagged("post_install", "-at_install")
class TestBlendOrderDecisions(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.config.blend_schema = "pos"
        self.sync_sample_menu()
        self.session = self.env["pos.session"].create({"config_id": self.config.id})
        self.session.set_opening_control(0, "")
        reference = uuid.uuid4().hex
        result = BlendPOSAPIService(self.env, self.config).create_order({
            "branch_id": self.branch_id, "brand_id": self.brand_id,
            "partner_reference": "jahez: %s" % reference,
            "aggregator": "jahez", "aggregator_order_id": reference,
            "order_type": "delivery", "status": "PENDING",
            "subtotal": 62, "tax": 0, "total": 62,
            "products": [{
                "id": "100", "quantity": 1, "total": 50,
                "modifiers": [{"id": "200", "quantity": 1, "total": 12}],
            }],
        })
        self.order = self.env["pos.order"].search([("uuid", "=", result["order_id"])])
        self.pos_user = new_test_user(
            self.env, login="blend-decisions-%s" % reference,
            groups="base.group_user,point_of_sale.group_pos_user",
        )

    def _logs(self):
        return self.env["blend.integration.log"].search([
            ("order_id", "=", self.order.id), ("direction", "=", "outbound"),
        ])

    def _wizard(self, reason="Product unavailable", user=None):
        return self.env["blend.order.reject.wizard"].with_user(user or self.pos_user).create({
            "order_id": self.order.id, "reason": reason,
        })

    def test_group_and_filter_incoming_blend_orders(self):
        ordinary, outbound_order = self.env["pos.order"].create([
            {
                "session_id": self.session.id,
                "amount_total": 0, "amount_tax": 0,
                "amount_paid": 0, "amount_return": 0,
                "blend_order_id": blend_order_id,
            }
            for blend_order_id in (False, "outbound-blend-id")
        ])
        orders = (self.order | ordinary | outbound_order).with_user(self.pos_user)
        domain = [("id", "in", orders.ids)]
        self.assertEqual(
            dict(orders._read_group(domain, ["blend_is_inbound"], ["__count"])),
            {True: 1, False: 2},
        )
        self.assertEqual(
            orders.search(domain + [("blend_is_inbound", "=", True)]).ids,
            [self.order.id],
        )

        reference = self.order.blend_partner_reference
        self.order.blend_partner_reference = False
        ordinary.blend_partner_reference = reference
        self.assertFalse(self.order.blend_can_decide)
        self.assertEqual(
            orders.search(domain + [("blend_is_inbound", "=", True)]).ids,
            [ordinary.id],
        )
        self.assertEqual(
            dict(orders._read_group(domain, ["blend_is_inbound"], ["__count"])),
            {True: 1, False: 2},
        )

    def test_pos_user_accepts_without_payment_or_immediate_http(self):
        self.assertTrue(self.order.blend_is_inbound)
        self.assertTrue(self.order.blend_can_decide)
        with patch.object(outbound.requests, "post") as post:
            result = self.order.with_user(self.pos_user).action_blend_accept_order()
            post.assert_not_called()
        self.assertEqual(result["tag"], "reload")
        self.assertEqual(self.order.blend_status, "accepted")
        self.assertEqual(self.order.state, "draft")
        self.assertEqual(self.order.amount_paid, 0)
        self.assertFalse(self.order.payment_ids)
        self.assertFalse(self.order.blend_can_decide)
        self.assertEqual(self.order.blend_decision_user_id, self.pos_user)
        self.assertEqual(self.order.blend_decision_source, "manual")
        self.assertTrue(self.order.blend_decision_at)
        log = self._logs()
        self.assertEqual(len(log), 1)
        self.assertEqual(log.request_payload["status"], "ACCEPTED")
        self.assertEqual(self.order.blend_decision_log_id, log)
        self.assertEqual(self.order.with_user(self.pos_user).blend_decision_sync_state, "pending")
        # POS users see only the delivery summary, not the privileged payload log.
        with self.assertRaises(AccessError):
            log.with_user(self.pos_user).read(["request_payload"])

    def test_rejection_wizard_records_reason_cancels_and_sends_rejected_only(self):
        action = self.order.with_user(self.pos_user).action_blend_reject_order()
        self.assertEqual(action["res_model"], "blend.order.reject.wizard")
        self.assertEqual(action["context"]["default_order_id"], self.order.id)
        reason = "Not available <script>alert('x')</script>"
        wizard = self._wizard(reason)
        result = wizard.action_confirm_rejection()
        self.assertEqual(result["type"], "ir.actions.act_window_close")
        self.assertEqual(self.order.state, "cancel")
        self.assertEqual(self.order.blend_status, "rejected")
        self.assertEqual(self.order.blend_rejection_reason, reason)
        self.assertFalse(self.order.blend_can_decide)
        log = self._logs()
        self.assertEqual(len(log), 1)
        self.assertEqual(log.request_payload["status"], "REJECTED")
        self.assertNotIn("reason", log.request_payload)
        messages = self.order.message_ids.filtered(lambda msg: "Not available" in str(msg.body))
        self.assertTrue(messages)
        self.assertIn("&lt;script&gt;", str(messages[0].body))
        self.assertNotIn("<script>", str(messages[0].body))
        self.assertEqual(messages[0].author_id, self.pos_user.partner_id)

    def test_rejection_requires_nonblank_bounded_reason(self):
        for reason in ("   ", "x" * 1001):
            with self.subTest(reason_length=len(reason)):
                with self.assertRaises(ValidationError):
                    self._wizard(reason).action_confirm_rejection()
        self.assertEqual(self.order.blend_status, "pending")
        self.assertFalse(self._logs())

    def test_ordinary_and_outbound_orders_have_no_decision_buttons_or_rpc(self):
        self.order.write({"blend_partner_reference": False, "blend_order_id": "outbound-blend-id"})
        self.assertFalse(self.order.blend_is_inbound)
        self.assertFalse(self.order.blend_can_decide)
        with self.assertRaises(UserError):
            self.order.action_blend_accept_order()
        with self.assertRaises(UserError):
            self.order.action_blend_reject_order()
        # A caller cannot bypass the source check by crafting a wizard directly.
        with self.assertRaises(UserError):
            self._wizard().action_confirm_rejection()
        self.assertFalse(self._logs())

    def test_same_decision_replay_is_idempotent_and_opposite_is_rejected(self):
        wizard = self._wizard()
        self.order.action_blend_accept_order()
        first = self._logs()
        messages = self.order.message_ids
        self.order.action_blend_accept_order()
        self.assertEqual(self._logs(), first)
        self.assertEqual(self.order.message_ids, messages)
        # The rejection dialog was opened before another employee accepted.
        with self.assertRaises(UserError):
            wizard.action_confirm_rejection()
        self.assertEqual(self.order.blend_status, "accepted")

    def test_rejection_confirmation_replay_is_idempotent(self):
        wizard = self._wizard()
        wizard.action_confirm_rejection()
        first = self._logs()
        wizard.action_confirm_rejection()
        self.assertEqual(self._logs(), first)
        with self.assertRaises(UserError):
            self.order.action_blend_accept_order()

    def test_paid_and_partial_payment_orders_cannot_be_decided(self):
        self.order.write({"amount_paid": 1})
        self.assertFalse(self.order.blend_can_decide)
        with self.assertRaises(UserError):
            self._wizard().action_confirm_rejection()
        self.order.with_context(blend_skip_status_sync=True).write({"state": "paid"})
        self.assertFalse(self.order.blend_can_decide)
        with self.assertRaises(UserError):
            self.order.action_blend_accept_order()
        with self.assertRaises(UserError):
            self._wizard().action_confirm_rejection()

    def test_no_decision_for_disabled_branch_or_non_pending_state(self):
        self.config.blend_enabled = False
        self.assertFalse(self.order.blend_can_decide)
        with self.assertRaises(UserError):
            self.order.action_blend_accept_order()
        self.config.blend_enabled = True
        self.order.blend_status = "preparing"
        self.assertFalse(self.order.blend_can_decide)
        with self.assertRaises(UserError):
            self.order.action_blend_reject_order()

    def test_missing_status_configuration_does_not_change_order(self):
        self.config.blend_status_url = False
        with self.assertRaises(ValidationError):
            self.order.action_blend_accept_order()
        self.assertEqual(self.order.blend_status, "pending")
        self.assertFalse(self._logs())

    def test_unauthorized_role_cannot_call_actions(self):
        user = new_test_user(self.env, login="blend-no-pos-%s" % uuid.uuid4().hex, groups="base.group_user")
        with self.assertRaises(AccessError):
            self.order.with_user(user).action_blend_accept_order()
        with self.assertRaises(AccessError):
            self.order.with_user(user).action_blend_reject_order()

    def test_other_company_order_is_not_accessible(self):
        other = self.env["res.company"].create({"name": "Blend decision other company"})
        user = new_test_user(
            self.env, login="blend-other-company-%s" % uuid.uuid4().hex,
            groups="base.group_user,point_of_sale.group_pos_user", company_id=other.id,
            company_ids=[(6, 0, [other.id])],
        )
        with self.assertRaises(AccessError):
            self.order.with_user(user).with_context(allowed_company_ids=[other.id]).action_blend_accept_order()

    def test_queue_failure_rolls_back_local_decision(self):
        with patch.object(type(self.env["blend.integration.log"]), "enqueue", side_effect=RuntimeError("queue unavailable")):
            with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
                self._wizard().action_confirm_rejection()
        self.assertEqual(self.order.state, "draft")
        self.assertEqual(self.order.blend_status, "pending")
        self.assertFalse(self.order.blend_decision_at)
        self.assertFalse(self._logs())

    def test_delivery_summary_tracks_retry_and_success(self):
        self.order.action_blend_accept_order()
        log = self._logs()
        with patch.object(outbound.requests, "post", return_value=FakeResponse(503, {})):
            self.assertFalse(log._process_outbound())
        self.assertEqual(self.order.with_user(self.pos_user).blend_decision_sync_state, "retry")
        with patch.object(outbound.requests, "post", return_value=FakeResponse(200, {"status": "success"})):
            self.assertTrue(log._process_outbound())
        self.assertEqual(self.order.with_user(self.pos_user).blend_decision_sync_state, "success")
        self.assertEqual(self.order.blend_status, "accepted")

    def test_legacy_status_selector_uses_decision_workflow(self):
        self.order.blend_delivery_status = "rejected"
        action = self.order.action_blend_send_delivery_status()
        self.assertEqual(action["res_model"], "blend.order.reject.wizard")
        self.assertEqual(self.order.blend_status, "pending")
        self.order.blend_delivery_status = "accepted"
        self.order.action_blend_send_delivery_status()
        self.assertEqual(self.order.blend_status, "accepted")

    def test_form_buttons_are_gated_by_inbound_pending_eligibility(self):
        arch = self.env.ref("bs_pos_blend_intgeration.view_pos_pos_form_blend")._get_combined_arch()
        for action in ("action_blend_accept_order", "action_blend_reject_order"):
            button = arch.xpath("//header/button[@name='%s']" % action)[0]
            expected = ("not blend_can_decide or blend_financial_state == 'failed'"
                        if action == "action_blend_accept_order" else "not blend_can_decide")
            self.assertEqual(button.get("invisible"), expected)
            self.assertIn("point_of_sale.group_pos_user", button.get("groups"))
        complete = arch.xpath("//header/button[@string='Complete Blend Accounting']")[0]
        self.assertEqual(complete.get("invisible"), "not blend_can_finalize")
        group = arch.xpath("//group[@name='blend_order_decision']")[0]
        self.assertEqual(group.get("invisible"), "not blend_is_inbound")
