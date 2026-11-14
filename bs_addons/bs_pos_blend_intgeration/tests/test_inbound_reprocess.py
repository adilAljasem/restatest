import copy
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, tagged
from odoo.tests.common import new_test_user

from .test_order_accounting import BlendAccountingCommon
from .test_order_discount import NINJA_PAYLOAD


@tagged("post_install", "-at_install")
class TestBlendInboundReprocess(BlendAccountingCommon):
    def setUp(self):
        super().setUp()
        self.env.user.group_ids += self.env.ref("base.group_system")
        self.config.write({"blend_brand_id": "1300011", "blend_branch_id": "1300024", "blend_auto_accept": True})
        self.item.write({"blend_external_id": "859", "modifier_grp_ids": [Command.clear()]})
        self.second = self.item.copy({"blend_external_id": "851"})
        self.mapping.aggregator = "ninja"
        self.log = self.env["blend.integration.log"].create({
            "name": "Failed Ninja order", "direction": "inbound", "event": "pos.order.create",
            "company_id": self.config.company_id.id, "config_id": self.config.id,
            "external_reference": NINJA_PAYLOAD["partner_reference"],
            "idempotency_key": "pos_order_create:%s:%s" % (self.config.id, NINJA_PAYLOAD["partner_reference"]),
            "state": "failed", "attempt_count": 3, "http_status": 400,
            "request_payload": copy.deepcopy(NINJA_PAYLOAD), "error_message": "Original totals mismatch",
        })

    def _wizard(self, mode="fulfilled"):
        action = self.log.action_reprocess_inbound()
        self.assertEqual(action["target"], "new")
        with Form(self.env[action["res_model"]].with_context(action["context"])) as form:
            form.mode = mode
        return form.record

    def test_fulfilled_payload_recovers_once_without_side_effects(self):
        wizard = self._wizard()
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
            action = wizard.action_reprocess()
        order = self.log.order_id
        self.assertEqual(action["res_id"], order.id)
        self.assertEqual(self.log.state, "success")
        self.assertEqual(self.log.attempt_count, 4)
        wizard.mode = "new"
        self.assertEqual(wizard.action_reprocess()["res_id"], order.id)
        self.assertEqual(order.state, "draft")
        self.assertEqual(self.log.request_payload, NINJA_PAYLOAD)
        self.assertEqual(order.blend_payload, NINJA_PAYLOAD)
        self.assertTrue(order.blend_recovered_fulfilled)
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.account_move or order.payment_ids or order.picking_ids)
        self.assertAlmostEqual(order.amount_total, 141.4, places=2)
        self.assertAlmostEqual(order.blend_discount_amount, 60.6, places=2)
        self.assertEqual(self.log.reprocessed_by_id, self.env.user)
        self.assertEqual(self.log.reprocess_mode, "fulfilled")
        self.assertFalse(self._logs(order))
        self.assertFalse(order.blend_kitchen_print_ids)
        self.assertEqual(wizard.action_reprocess()["res_id"], order.id)
        self.assertEqual(self.log.attempt_count, 4)
        # A later ordinary API replay also returns the recovered draft as-is.
        self.assertEqual(self._create(copy.deepcopy(NINJA_PAYLOAD)), order)
        self.assertEqual(order.state, "draft")
        self.assertEqual(self.env["pos.order"].search_count([
            ("config_id", "=", self.config.id), ("blend_partner_reference", "=", NINJA_PAYLOAD["partner_reference"]),
        ]), 1)

    def test_new_order_uses_existing_auto_acceptance(self):
        self._wizard("new").action_reprocess()
        order = self.log.order_id
        self._assert_done(order, total=141.4)
        self.assertFalse(order.blend_recovered_fulfilled)
        self.assertEqual(len(self._logs(order)), 1)

    def test_existing_order_is_linked_without_changing_it(self):
        self.config.blend_auto_accept = False
        order = self._create(copy.deepcopy(NINJA_PAYLOAD))
        self.log.write({"state": "failed", "order_id": False, "error_message": "Lost response"})
        self.config.blend_auto_accept = True
        action = self._wizard().action_reprocess()
        self.assertEqual(action["res_id"], order.id)
        self.assertEqual(self.log.order_id, order)
        self.assertFalse(order.blend_recovered_fulfilled)
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.account_move or self._logs(order))

    def test_failure_keeps_original_payload_and_new_error(self):
        payload = copy.deepcopy(NINJA_PAYLOAD)
        payload["products"][0]["total"] = 125
        self.log.request_payload = payload
        action = self._wizard().action_reprocess()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "danger")
        self.assertEqual(self.log.state, "failed")
        self.assertEqual(self.log.attempt_count, 4)
        self.assertIn("sum of products", self.log.error_message)
        self.assertEqual(self.log.request_payload, payload)
        self.assertFalse(self.log.order_id)
        self.assertFalse(self.env["pos.order"].search([
            ("config_id", "=", self.config.id), ("blend_partner_reference", "=", payload["partner_reference"]),
        ]))

    def test_no_open_session_is_logged_without_creating_one(self):
        self.pos_session.sudo().write({"state": "closed"})
        self._wizard().action_reprocess()
        self.assertEqual(self.log.state, "failed")
        self.assertIn("No open POS session", self.log.error_message)
        self.assertFalse(self.log.order_id)

    def test_partial_creation_is_rolled_back_but_failure_is_recorded(self):
        wizard = self._wizard()
        with patch.object(type(self.env["pos.order"]), "_blend_prepare_imported_accounting",
                          side_effect=ValidationError("Failure after creating the lines")):
            wizard.action_reprocess()
        self.assertEqual(self.log.state, "failed")
        self.assertEqual(self.log.attempt_count, 4)
        self.assertIn("Failure after creating the lines", self.log.error_message)
        self.assertFalse(self.log.order_id)
        self.assertFalse(self.env["pos.order"].search([
            ("config_id", "=", self.config.id), ("blend_partner_reference", "=", NINJA_PAYLOAD["partner_reference"]),
        ]))

    def test_wrong_event_and_payload_branch_are_rejected(self):
        self.log.event = "order.updated"
        with self.assertRaises(UserError):
            self.log.action_reprocess_inbound()
        self.log.event = "pos.order.create"
        wizard = self._wizard()
        payload = copy.deepcopy(NINJA_PAYLOAD)
        payload["branch_id"] = "different-branch"
        self.log.request_payload = payload
        with self.assertRaises(UserError):
            wizard.action_reprocess()
        self.assertFalse(self.log.order_id)

    def test_current_access_rights_and_selected_company_are_required(self):
        cashier = new_test_user(self.env, login="blend_reprocess_cashier", groups="point_of_sale.group_pos_user",
                                company_id=self.env.company.id, company_ids=[Command.set(self.env.company.ids)])
        with self.assertRaises(AccessError):
            self.log.with_user(cashier).action_reprocess_inbound()
        wizard = self._wizard()
        other = self.setup_other_company(name="Unselected reprocess company")["company"]
        with self.assertRaises(AccessError):
            wizard.with_context(allowed_company_ids=other.ids).action_reprocess()

    def test_recovered_order_stays_silent_after_manual_accounting(self):
        self._wizard().action_reprocess()
        order = self.log.order_id
        order.action_blend_accept_order()
        order.with_context(resta_kds_fire=True, blend_kitchen_send=True)._resta_kds_dispatch(paid_now=True)
        self.assertEqual(order.blend_financial_state, "done", order.blend_financial_error)
        self.assertEqual(order.state, "done")
        self.assertEqual(order.account_move.state, "posted")
        self.assertAlmostEqual(order.account_move.amount_total, 141.4, places=2)
        self.assertAlmostEqual(order.amount_paid, 141.4, places=2)
        self.assertAlmostEqual(order.account_move.amount_residual, 0, places=2)
        self.assertFalse(self._logs(order))
        self.assertFalse(order.blend_kitchen_print_ids)
        order._blend_enqueue_status("prepared")
        order._blend_queue_kitchen_print()
        self.assertFalse(self._logs(order) or order.blend_kitchen_print_ids)
        self.assertFalse(self.env["resta.kds.ticket"].search([("pos_order_id", "=", order.id)]))

    def test_recovery_marker_cannot_be_supplied_by_cashier_clients(self):
        self._wizard().action_reprocess()
        order = self.log.order_id
        with self.assertRaises(AccessError):
            order.write({"blend_recovered_fulfilled": False})
        self.assertNotIn("blend_recovered_fulfilled", order._load_pos_data_fields(self.config))

    def test_reprocess_holds_the_incoming_request_lock(self):
        self._wizard().action_reprocess()
        with self.registry.cursor() as other_cr:
            other_cr.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", [self.log.idempotency_key])
            self.assertFalse(other_cr.fetchone()[0])
