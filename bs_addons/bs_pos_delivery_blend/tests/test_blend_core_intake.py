"""Blend orders must enter Core before POS and complete accounting on acceptance."""

from copy import deepcopy
from uuid import UUID
from unittest.mock import patch

from odoo import Command, fields
from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import new_test_user

from ..services.auth import BlendAuthService
from ..services.exceptions import BlendAPIError
from ..services.webhook import BlendWebhookService


@tagged("post_install", "-at_install")
class TestBlendCoreIntake(TestPoSCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        )
        cls.basic_config.write({
            "blend_branch_id": "BLEND-CORE-BRANCH",
            "blend_brand_id": "BLEND-CORE-BRAND",
        })
        cls.basic_config.open_ui()
        cls.provider = cls.env.ref("bs_pos_delivery_core.provider_blend")
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "Blend Core intake",
            "provider_id": cls.provider.id,
            "company_id": cls.company.id,
            "state": "connected",
            "webhook_secret": "blend-core-test-token",
            "pos_config_ids": [Command.set(cls.basic_config.ids)],
        })
        cls.store = cls.connection._blend_store_for_config(cls.basic_config)
        cls.product = cls.env["product.product"].create({
            "name": "Blend Core Meal",
            "available_in_pos": True,
            "list_price": 20,
            "taxes_id": [Command.clear()],
            "property_account_income_id": cls.sales_account.id,
        })
        cls.env["bs.delivery.product.binding"].create({
            "connection_id": cls.connection.id,
            "store_binding_id": cls.store.id,
            "external_product_id": "MEAL",
            "product_id": cls.product.id,
        })
        cls.connection.write({"delivery_payment_mapping_ids": [
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "Jahez",
                "payment_type": "credit_visa",
                "payment_method_id": cls.bank_pm1.id,
            }),
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "Jahez",
                "payment_type": "cash",
                "payment_method_id": cls.cash_pm1.id,
            }),
        ]})

    def _event(self, reference):
        return self.env["bs.delivery.webhook.event"].sudo().create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "signature_verified": True,
            "external_id": reference,
            "event_type": "order.created",
            "idempotency_key": "blend-core-test:%s" % reference,
            "payload_digest": "0" * 64,
        })

    def _enable_auto_accept(self):
        partner = self.env["res.partner"].create({"name": "Blend default invoice customer"})
        self.connection.write({
            "delivery_accept_action": "accept",
            "delivery_default_invoice_partner_id": partner.id,
        })

    def _attach_blend_modifier_group(self, product, options, *, maximum=2):
        group = self.env["pos.modifier.group"].create({
            "name": "Blend test extras %s" % product.id,
            "blend_config_id": self.basic_config.id,
            "blend_external_id": "test-group-%s" % product.id,
            "min_qty": 0,
            "max_qty": maximum,
            "selection_type": "multiple",
            "option_ids": [Command.create({
                "name": option.display_name,
                "blend_external_id": "test-option-%s" % option.id,
                "product_id": option.id,
                "option_type": "product",
                "price_mode": "fixed_extra",
                "extra_price": option.lst_price,
                "max_qty": 2,
            }) for option in options],
        })
        self.env["pos.product.modifier.group.line"].create({
            "product_tmpl_id": product.product_tmpl_id.id,
            "modifier_group_id": group.id,
            "company_id": self.company.id,
        })
        product.product_tmpl_id.allow_modifiers = True
        return group

    def _payload(self, reference, payment_type="card"):
        return {
            "contract_version": 1,
            "external_order_id": reference,
            "external_source": "Jahez",
            "currency": self.basic_config.currency_id.name,
            "fulfillment_type": "delivery",
            "price_basis": "tax_excluded",
            "customer": {"name": "Blend Customer"},
            "lines": [{
                "external_line_id": "line-1",
                "external_product_id": "MEAL",
                "name": "Blend Core Meal",
                "quantity": 1,
                "price_unit": 20,
                "discount_amount": 0,
                "vat_amount": 0,
                "price_subtotal": 20,
            }],
            "totals": {
                "items_subtotal": 20,
                "discount_amount": 0,
                "fee_amount": 0,
                "vat_amount": 0,
                "rounding_amount": 0,
                "amount_total": 20,
            },
            "payments": [{
                "external_payment_code": payment_type,
                "external_source": "Jahez",
                "amount": 20,
                "currency": self.basic_config.currency_id.name,
                "collected_by": "aggregator" if payment_type == "card" else "restaurant",
                "collection_status": "collected" if payment_type == "card" else "expected",
                **({"external_payment_reference": "blend:%s" % reference}
                   if payment_type == "card" else {}),
            }],
        }

    def test_acceptance_invoices_and_pays_blend_order_once(self):
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-INV-1"),
            event=self._event("BLEND-INV-1"),
        )
        self.assertEqual(order.state, "received")
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-INV-1")
        pos = order.pos_order_id
        self.assertEqual(pos.state, "done")
        self.assertEqual(len(pos.payment_ids), 1)
        self.assertEqual(pos.payment_ids.amount, 20)
        self.assertEqual(pos.account_move.state, "posted")
        self.assertFalse(pos.account_move.amount_residual)
        self.assertFalse(self.env["bs.delivery.outbox"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "send_order"),
        ]))
        pos._blend_enqueue_status("preparing", connection=self.connection)
        status_job = self.env["bs.delivery.outbox"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "update_order"),
            ("binding_id", "=", order.binding_id.id),
        ], order="id desc", limit=1)
        self.assertTrue(status_job)
        self.assertEqual(order.binding_id.pos_order_id, pos)

    def _kitchen(self, name):
        """A Resta kitchen screen with a «ready» column (to cook -> ready -> bump)."""
        display = self.env["resta.kds.display"].create({
            "name": name, "company_id": self.company.id,
            "pos_config_ids": [Command.link(self.basic_config.id)],
        })
        self.env["resta.kds.stage"].create({"display_id": display.id, "name": "Ready", "sequence": 50,
                                            "is_ready": True})
        return display

    def _tickets(self, pos_order, display=None):
        return pos_order.resta_kds_ticket_ids.filtered(
            lambda t: not t.cancelled and (not display or t.display_id == display))

    @staticmethod
    def _ready(tickets):
        for ticket in tickets:
            ticket._set_stage(ticket.display_id.stage_ids.filtered("is_ready")[:1])

    @staticmethod
    def _finish(tickets):
        for ticket in tickets:
            ticket._finish()

    def test_kitchen_ready_and_done_advance_blend_status(self):
        display = self._kitchen("Blend kitchen status")
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-KDS-STATUS"),
            event=self._event("BLEND-KDS-STATUS"),
        )
        order.transition_to(
            "accepted", source="automatic", expected_state="received",
            idempotency_key="accept:BLEND-KDS-STATUS",
        )
        tickets = self._tickets(order.pos_order_id, display)
        self.assertTrue(tickets)
        self._ready(tickets)
        self.assertEqual(order.state, "preparing")
        self.assertEqual(order.pos_order_id.blend_status, "preparing")
        self._finish(tickets)
        self.assertEqual(order.state, "ready")
        self.assertEqual(order.pos_order_id.blend_status, "prepared")
        jobs = self.env["bs.delivery.outbox"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "in", ["mark_order_preparing", "mark_order_ready"]),
        ])
        self.assertEqual(len(jobs), 2)
        adapter = self.env["bs.delivery.adapter.blend"]
        with patch.object(type(adapter), "_post", return_value={"status": "success"}) as post:
            for job in jobs.sorted("id"):
                job._dispatch_operation()
        self.assertEqual(
            [call.args[2]["status"] for call in post.call_args_list],
            ["PREPARING", "PREPARED"],
        )

    def test_kitchen_waits_for_all_displays_and_ignores_repeated_actions(self):
        displays = [self._kitchen("Blend kitchen station %s" % index) for index in (1, 2)]
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-KDS-MULTI"),
            event=self._event("BLEND-KDS-MULTI"),
        )
        order.transition_to(
            "accepted", source="automatic", expected_state="received",
            idempotency_key="accept:BLEND-KDS-MULTI",
        )
        tickets = [self._tickets(order.pos_order_id, display) for display in displays]
        self.assertTrue(all(tickets))
        for index, display_tickets in enumerate(tickets):
            self._ready(display_tickets)
            self.assertEqual(order.state, "preparing" if index else "accepted")
        job_count = self.env["bs.delivery.outbox"].search_count([("binding_id", "=", order.binding_id.id)])
        self._ready(tickets[0])  # repeated action: nothing new for Blend
        self.assertEqual(self.env["bs.delivery.outbox"].search_count([
            ("binding_id", "=", order.binding_id.id)]), job_count)
        for index, display_tickets in enumerate(tickets):
            self._finish(display_tickets)
            self.assertEqual(order.state, "ready" if index else "preparing")
        tickets[0].with_user(self.env.user).sudo().kds_recall()
        self._finish(tickets[0])
        self.assertEqual(order.state, "ready")
        self.assertEqual(self.env["bs.delivery.outbox"].search_count([
            ("binding_id", "=", order.binding_id.id)]), job_count + 1)

    def test_cashier_kitchen_action_does_not_enqueue_blend_status(self):
        display = self._kitchen("Cashier kitchen status")
        cashier_order = self.env["pos.order"].create({
            "session_id": self.basic_config.current_session_id.id,
            "company_id": self.company.id,
            "amount_total": 20,
            "amount_tax": 0,
            "amount_paid": 0,
            "amount_return": 0,
            "lines": [Command.create({
                "product_id": self.product.id,
                "qty": 1,
                "price_unit": 20,
                "price_subtotal": 20,
                "price_subtotal_incl": 20,
                "tax_ids": [Command.clear()],
            })],
        })
        cashier_order.with_context(resta_kds_fire=True)._resta_kds_dispatch()
        tickets = self._tickets(cashier_order, display)
        self.assertTrue(tickets)
        job_count = self.env["bs.delivery.outbox"].search_count([])
        self._ready(tickets)
        self._finish(tickets)
        self.assertFalse(self.env["bs.delivery.order"].search([("pos_order_id", "=", cashier_order.id)]))
        self.assertEqual(self.env["bs.delivery.outbox"].search_count([]), job_count)

    def test_kitchen_status_is_queued_when_blend_connection_is_down(self):
        display = self._kitchen("Blend kitchen offline")
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-KDS-OFFLINE"),
            event=self._event("BLEND-KDS-OFFLINE"),
        )
        order.transition_to(
            "accepted", source="automatic", expected_state="received",
            idempotency_key="accept:BLEND-KDS-OFFLINE",
        )
        tickets = self._tickets(order.pos_order_id, display)
        self.assertTrue(tickets)
        self.connection.state = "error"
        self._ready(tickets)
        self.assertEqual(order.state, "preparing")
        job = self.env["bs.delivery.outbox"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "=", "mark_order_preparing"),
        ])
        self.assertEqual(len(job), 1)
        self.assertEqual(job.state, "queued")

    def test_failed_blend_kitchen_print_is_not_retried_on_refresh(self):
        self.connection.auto_print_kitchen = True
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-PRINT-ONCE"),
            event=self._event("BLEND-PRINT-ONCE"),
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-PRINT-ONCE")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(order.kitchen_print_state, "pending")
        self.assertEqual(order.kitchen_print_attempts, 0)
        order.sudo()._with_delivery_internal_update().write({
            "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=31,
            ),
        })
        jobs = self.env["bs.delivery.order"].get_pending_kitchen_prints(
            self.basic_config.id,
        )
        self.assertIn(order.id, [job["order_id"] for job in jobs])
        claim = "blend-print-once-claim-key"
        self.assertTrue(order.claim_kitchen_print(order.id, self.basic_config.id, claim))
        order.acknowledge_kitchen_print(
            order.id, self.basic_config.id, claim, False, "Printer unavailable",
        )
        self.assertEqual(order.kitchen_print_state, "error")
        self.assertEqual(order.kitchen_print_attempts, 1)
        order.transition_to("preparing", source="manual", expected_state="accepted",
                            idempotency_key="preparing:BLEND-PRINT-ONCE")
        order.transition_to("ready", source="manual", expected_state="preparing",
                            idempotency_key="ready:BLEND-PRINT-ONCE")
        order._request_kitchen_autoprint()
        self.assertEqual(order.kitchen_print_state, "error")
        order.sudo()._with_delivery_internal_update().write({
            "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=31,
            ),
        })
        self.assertNotIn(order.id, [job["order_id"] for job in
                                    order.get_pending_kitchen_prints(self.basic_config.id)])

    def test_blend_kitchen_manual_retry_requires_manager_and_prints_once(self):
        self.connection.auto_print_kitchen = True
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-PRINT-MANUAL"),
            event=self._event("BLEND-PRINT-MANUAL"),
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-PRINT-MANUAL")
        first_claim = "blend-print-manual-first-claim"
        self.assertTrue(order.claim_kitchen_print(
            order.id, self.basic_config.id, first_claim,
        ))
        self.assertEqual(order.kitchen_print_state, "attempting")
        self.assertFalse(order.claim_kitchen_print(
            order.id, self.basic_config.id, "blend-print-manual-other-claim",
        ))
        with self.assertRaises(UserError):
            order.action_retry_blend_kitchen_print()
        order.acknowledge_kitchen_print(
            order.id, self.basic_config.id, first_claim, False, "Printer unavailable",
        )
        order.sudo()._with_delivery_internal_update().write({
            "accepted_at": fields.Datetime.subtract(
                fields.Datetime.now(), minutes=16,
            ),
        })
        operator = new_test_user(
            self.env, login="blend_print_operator",
            groups="bs_pos_delivery_core.group_delivery_operator",
        )
        with self.assertRaises(AccessError):
            order.with_user(operator).action_retry_blend_kitchen_print()
        self.assertEqual(order.kitchen_print_state, "error")

        order.action_retry_blend_kitchen_print()
        self.assertEqual(order.kitchen_print_state, "pending")
        self.assertTrue(order.blend_kitchen_manual_retry_requested)
        jobs = order.get_pending_kitchen_prints(self.basic_config.id)
        self.assertIn(order.id, [job["order_id"] for job in jobs])
        second_claim = "blend-print-manual-second-claim"
        self.assertTrue(order.claim_kitchen_print(
            order.id, self.basic_config.id, second_claim,
        ))
        self.assertEqual(order.kitchen_print_state, "attempting")
        self.assertFalse(order.blend_kitchen_manual_retry_requested)
        self.assertEqual(order.kitchen_print_attempts, 2)
        order.acknowledge_kitchen_print(
            order.id, self.basic_config.id, second_claim, True,
        )
        self.assertEqual(order.kitchen_print_state, "sent")
        order.transition_to("preparing", source="manual", expected_state="accepted",
                            idempotency_key="preparing:BLEND-PRINT-MANUAL")
        order.transition_to("ready", source="manual", expected_state="preparing",
                            idempotency_key="ready:BLEND-PRINT-MANUAL")
        self.assertEqual(order.kitchen_print_state, "sent")
        self.assertNotIn(order.id, [job["order_id"] for job in
                                    order.get_pending_kitchen_prints(self.basic_config.id)])

    def test_stale_blend_kitchen_ticket_needs_manual_review(self):
        self.connection.auto_print_kitchen = True
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-STALE-PRINT"),
            event=self._event("BLEND-STALE-PRINT"),
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-STALE-PRINT")
        old = fields.Datetime.subtract(fields.Datetime.now(), minutes=16)
        order.sudo()._with_delivery_internal_update().write({
            "accepted_at": old,
            "kitchen_print_requested_at": old,
            "kitchen_print_last_attempt_at": old,
        })
        self.assertNotIn(order.id, [
            job["order_id"] for job in order.get_pending_kitchen_prints(
                self.basic_config.id)
        ])
        self.assertEqual(order.kitchen_print_state, "error")
        self.assertEqual(order.kitchen_print_attempts, 0)
        self.assertTrue(order.blend_kitchen_can_retry)
        order.action_retry_blend_kitchen_print()
        self.assertIn(order.id, [
            job["order_id"] for job in order.get_pending_kitchen_prints(
                self.basic_config.id)
        ])
        self.assertTrue(order.claim_kitchen_print(
            order.id, self.basic_config.id, "blend-stale-manual-claim",
        ))
        self.assertEqual(order.kitchen_print_attempts, 1)

    def test_blend_kitchen_lost_ack_does_not_start_another_print(self):
        self.connection.auto_print_kitchen = True
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-PRINT-LOST-ACK"),
            event=self._event("BLEND-PRINT-LOST-ACK"),
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-PRINT-LOST-ACK")
        claim = "blend-print-lost-ack-claim"
        self.assertTrue(order.claim_kitchen_print(order.id, self.basic_config.id, claim))
        self.assertEqual(order.kitchen_print_state, "attempting")
        order.sudo()._with_delivery_internal_update().write({
            "kitchen_print_claim_until": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=1,
            ),
            "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=31,
            ),
        })
        self.assertNotIn(order.id, [job["order_id"] for job in
                                    order.get_pending_kitchen_prints(self.basic_config.id)])
        self.assertFalse(order.claim_kitchen_print(
            order.id, self.basic_config.id, "blend-print-lost-ack-other",
        ))
        order.action_retry_blend_kitchen_print()
        self.assertEqual(order.kitchen_print_state, "pending")
        self.assertTrue(order.blend_kitchen_manual_retry_requested)
        self.assertTrue(order.claim_kitchen_print(
            order.id, self.basic_config.id, "blend-print-lost-ack-manual-retry",
        ))
        self.assertEqual(order.kitchen_print_attempts, 2)

    def test_other_provider_kitchen_failure_requires_manual_retry(self):
        provider = self.env["bs.delivery.provider"].search([
            ("code", "=", "feedus"),
        ], limit=1) or self.env["bs.delivery.provider"].create({
            "name": "FeedUs Print Regression",
            "code": "feedus",
            "connector_kind": "aggregator",
        })
        connection = self.env["bs.delivery.connection"].create({
            "name": "Other Provider Kitchen Retry",
            "provider_id": provider.id,
            "company_id": self.company.id,
            "auto_print_kitchen": True,
        })
        store = self.env["bs.delivery.store.binding"].create({
            "connection_id": connection.id,
            "pos_config_id": self.basic_config.id,
            "remote_store_id": "other-provider-print",
        })
        pos_order = self.env["pos.order"].create({
            "session_id": self.basic_config.current_session_id.id,
            "company_id": self.company.id,
            "pricelist_id": self.basic_config.pricelist_id.id,
            "amount_total": 0,
            "amount_tax": 0,
            "amount_paid": 0,
            "amount_return": 0,
        })
        order = self.env["bs.delivery.order"]._with_delivery_internal_update().create({
            "connection_id": connection.id,
            "store_binding_id": store.id,
            "external_order_id": "OTHER-PROVIDER-PRINT-1",
            "currency_id": self.company.currency_id.id,
            "state": "accepted",
            "pos_order_id": pos_order.id,
            "kitchen_print_state": "pending",
            "kitchen_print_attempts": 0,
        })
        self.assertIn(order.id, [job["order_id"] for job in
                                 order.get_pending_kitchen_prints(self.basic_config.id)])
        claim = "other-provider-print-claim"
        self.assertTrue(order.claim_kitchen_print(order.id, self.basic_config.id, claim))
        order.acknowledge_kitchen_print(
            order.id, self.basic_config.id, claim, False, "Printer unavailable",
        )
        self.assertEqual(order.kitchen_print_state, "error")
        self.assertEqual(order.kitchen_print_attempts, 1)
        order.sudo()._with_delivery_internal_update().write({
            "kitchen_print_last_attempt_at": fields.Datetime.subtract(
                fields.Datetime.now(), seconds=31,
            ),
        })
        self.assertNotIn(order.id, [job["order_id"] for job in
                                    order.get_pending_kitchen_prints(self.basic_config.id)])

    def test_cash_is_posted_and_invoiced_at_acceptance(self):
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-CASH-1", "cash"),
            event=self._event("BLEND-CASH-1"),
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-CASH-1")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.payment_ids.payment_method_id, self.cash_pm1)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_cash_cancellation_after_invoice_creates_credit_note(self):
        order = self.env["bs.delivery.order.service"]._receive_order(
            self.connection, self.store, self._payload("BLEND-REFUND-1", "cash"),
            event=self._event("BLEND-REFUND-1"),
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:BLEND-REFUND-1")
        order.transition_to("cancelled", source="manual", expected_state="accepted",
                            idempotency_key="cancel:BLEND-REFUND-1", reason="Test refund")
        refund = order.pos_refund_order_id
        self.assertEqual(order.pos_processing_state, "done", order.pos_processing_error)
        self.assertTrue(refund)
        self.assertEqual(refund.account_move.state, "posted")
        self.assertEqual(refund.account_move.move_type, "out_refund")

    def test_cashier_pos_order_does_not_go_to_blend(self):
        session = self.basic_config.current_session_id
        cashier_order = self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.company.id,
            "amount_total": 20,
            "amount_tax": 0,
            "amount_paid": 0,
            "amount_return": 0,
            "lines": [Command.create({
                "product_id": self.product.id,
                "qty": 1,
                "price_unit": 20,
                "price_subtotal": 20,
                "price_subtotal_incl": 20,
                "tax_ids": [Command.clear()],
            })],
        })
        cashier_order.add_payment({
            "pos_order_id": cashier_order.id,
            "payment_method_id": self.bank_pm1.id,
            "amount": 20,
        })
        cashier_order._process_saved_order(False)
        self.assertFalse(cashier_order.blend_order_id)
        self.assertFalse(self.env["bs.delivery.outbox"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "send_order"),
        ]))
        Binding = self.env["bs.delivery.order.binding"].sudo()
        DeliveryOrder = self.env["bs.delivery.order"].sudo()
        cashier_order.write({"blend_order_id": False, "blend_status": False})
        self.assertFalse(Binding.search([("pos_order_id", "=", cashier_order.id)]))
        self.assertFalse(DeliveryOrder.search([("pos_order_id", "=", cashier_order.id)]))
        self.assertFalse(self.env["bs.delivery.blend.bridge"].ensure_order_binding(
            cashier_order, self.connection,
        ))

        cashier_order.write({"blend_order_id": "unverified-pos-value"})
        self.assertFalse(Binding.search([("pos_order_id", "=", cashier_order.id)]))
        self.assertFalse(DeliveryOrder.search([("pos_order_id", "=", cashier_order.id)]))

    def test_pos_write_does_not_replace_verified_blend_order_identity(self):
        self._enable_auto_accept()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._blend_request("JAHEZ-POS-WRITE"), "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-POS-WRITE"),
        ])
        self.assertEqual(order.pos_order_id.blend_order_id, result["order_id"])
        order.pos_order_id.write({"blend_status": "pending"})
        order.binding_id.invalidate_recordset()
        self.assertEqual(order.binding_id.external_order_id, "JAHEZ-POS-WRITE")
        self.assertEqual(order.blend_api_order_id, result["order_id"])

    def _blend_request(self, reference="JAHEZ-100", **overrides):
        payload = {
            "branch_id": "BLEND-CORE-BRANCH",
            "brand_id": "BLEND-CORE-BRAND",
            "partner_reference": reference,
            "aggregator_order_id": "100",
            "aggregator": "Jahez",
            "order_type": "delivery",
            "status": "PENDING",
            "subtotal": "20.00",
            "tax": "0.00",
            "total": "20.00",
            "customer": {"name": "Blend Customer"},
            "products": [{"id": "MEAL", "quantity": 1, "total": "20.00", "modifiers": []}],
        }
        payload.update(overrides)
        return payload

    def test_failed_http_order_log_retries_without_event_or_binding(self):
        self._enable_auto_accept()
        payload = self._blend_request("JAHEZ-LOG-RETRY", aggregator_order_id="LOG-RETRY")
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="blend:create_order",
            external_id="LOG-RETRY", request_method="POST", request_url="/api/orders",
            request_payload=payload,
        )
        log.mark_failed("Previous configuration error", http_status=422)
        self.assertFalse(log.binding_id)
        self.assertFalse(log.webhook_event_id)
        self.assertTrue(log.can_retry_processing)

        log_count = self.env["bs.delivery.sync.log"].search_count([
            ("correlation_id", "=", log.correlation_id),
        ])
        log.action_retry_processing()
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertFalse(order.pos_order_id.account_move.amount_residual)
        self.assertEqual(order.external_reference, "LOG-RETRY")
        self.assertEqual(log.status, "success")
        self.assertEqual(self.env["bs.delivery.sync.log"].search_count([
            ("correlation_id", "=", log.correlation_id),
        ]), log_count)
        with self.assertRaises(UserError):
            log.action_retry_processing()

    def test_force_send_tax_mismatch_order_to_branch(self):
        """A Blend order rejected over taxes can be force-sent by a manager."""
        self._enable_auto_accept()
        self.connection.auto_print_kitchen = True
        # Blend reports 15% VAT; the Odoo product has no tax: a normal intake
        # rejects it with "Blend totals differ from the configured Odoo
        # product taxes".
        payload = self._blend_request(
            "HS-FORCE-1", aggregator_order_id="FORCE-1",
            subtotal="17.39", tax="2.61", total="20.00",
        )
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="blend:create_order",
            external_id="FORCE-1", request_method="POST", request_url="/api/orders",
            request_payload=payload,
        )
        log.mark_failed(
            "Blend totals differ from the configured Odoo product taxes.", http_status=422,
        )
        log.action_retry_processing()
        self.assertEqual(log.status, "failed")
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "HS-FORCE-1"),
        ]))

        operator = new_test_user(
            self.env, login="blend_force_operator",
            groups="bs_pos_delivery_core.group_delivery_operator",
        )
        with self.assertRaises(AccessError):
            log.with_user(operator).action_force_send_to_branch()

        self.assertTrue(log.can_force_send)
        action = log.action_force_send_to_branch()
        self.assertEqual(action["params"]["type"], "success")
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "HS-FORCE-1"),
        ])
        self.assertEqual(order.state, "accepted")
        self.assertTrue(order.pos_order_id)
        # Odoo's product taxes decide the split; the paid total is Blend's.
        self.assertEqual(order.pos_order_id.amount_total, 20.0)
        self.assertEqual(order.pos_order_id.amount_tax, 0.0)
        self.assertEqual(order.kitchen_print_state, "pending")
        self.assertEqual(log.status, "success")
        self.assertTrue(any("Force-sent" in str(body or "") for body in order.message_ids.mapped("body")))
        # A second force send cannot duplicate the order.
        log.sudo().write({"status": "failed"})
        with self.assertRaises(UserError):
            log.action_force_send_to_branch()

    def test_force_send_still_reports_unknown_products(self):
        payload = self._blend_request(
            "HS-FORCE-UNKNOWN", aggregator_order_id="FORCE-UNKNOWN",
            products=[{"id": "NOT-MAPPED", "quantity": 1, "total": "20.00", "modifiers": []}],
        )
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="blend:create_order",
            external_id="FORCE-UNKNOWN", request_method="POST", request_url="/api/orders",
            request_payload=payload,
        )
        log.mark_failed("Blend product 'NOT-MAPPED' was not found.", http_status=404)
        action = log.action_force_send_to_branch()
        self.assertEqual(action["params"]["type"], "danger")
        self.assertEqual(log.status, "failed")
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "HS-FORCE-UNKNOWN"),
        ]))

    def test_failed_http_retry_can_be_retried_again_after_fix(self):
        self._enable_auto_accept()
        journal = self.basic_config.invoice_journal_id
        self.basic_config.invoice_journal_id = False
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="blend:create_order",
            external_id="LOG-RETRY-TWICE", request_method="POST",
            request_url="/api/orders",
            request_payload=self._blend_request(
                "JAHEZ-RETRY-TWICE", aggregator_order_id="LOG-RETRY-TWICE",
            ),
        )
        log.mark_failed("Invoice journal missing", http_status=422)
        log_count = self.env["bs.delivery.sync.log"].search_count([
            ("correlation_id", "=", log.correlation_id),
        ])
        log.action_retry_processing()
        self.assertEqual(log.attempt_number, 2)
        self.assertEqual(log.status, "failed")
        self.assertEqual(self.env["bs.delivery.sync.log"].search_count([
            ("correlation_id", "=", log.correlation_id),
        ]), log_count)
        self.basic_config.invoice_journal_id = journal
        log.action_retry_processing()
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-RETRY-TWICE"),
        ])
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.attempt_number, 3)
        self.assertEqual(self.env["bs.delivery.sync.log"].search_count([
            ("correlation_id", "=", log.correlation_id),
        ]), log_count)

    def test_failed_pos_log_finishes_existing_draft_order(self):
        payload = self._blend_request("JAHEZ-DRAFT-RETRY", aggregator_order_id="DRAFT-RETRY")
        self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        draft = order.create_pos_order()
        order._with_delivery_internal_update().write({"state": "accepted"})
        order._set_pos_processing_result("blocked", "Previous invoice error")
        log = self.env["bs.delivery.sync.log"].search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "=", "core:pos_processing"),
            ("status", "=", "failed"),
        ], limit=1)
        self.assertTrue(log.can_retry_processing)
        log.action_retry_processing()
        self.assertEqual(order.pos_order_id.id, draft.id)
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(draft.account_move.state, "posted")

    def test_failed_http_status_log_resumes_existing_order(self):
        self._enable_auto_accept()
        payload = self._blend_request("JAHEZ-STATUS-RETRY", aggregator_order_id="STATUS-RETRY")
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        log = self.env["bs.delivery.sync.log"].start_attempt(
            self.connection, direction="incoming", operation="blend:update_order",
            request_method="PUT", request_url="/api/order/update",
            request_payload={
                "branch_id": payload["branch_id"], "brand_id": payload["brand_id"],
                "order_id": result["order_id"], "status": "PREPARING",
            },
        )
        log.mark_failed("Previous status error", http_status=422)
        log.action_retry_processing()
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.state, "preparing")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_delivery_order_uses_selected_branch_preset(self):
        dine_in = self.env["pos.preset"].create({"name": "Dine In"})
        delivery = self.env["pos.preset"].create({"name": "Delivery"})
        self.basic_config.write({
            "use_presets": True,
            "default_preset_id": dine_in.id,
            "available_preset_ids": [Command.set((dine_in | delivery).ids)],
        })
        self.store.write({"blend_delivery_preset_id": delivery.id})
        self._enable_auto_accept()

        payload = self._blend_request("BLEND-PRESET-DELIVERY")
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )

        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.pos_order_id.preset_id, delivery)
        self.assertEqual(order.pos_order_id.amount_total, 20.0)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 20.0)

    def test_pickup_dine_in_and_unmapped_presets(self):
        presets = {
            name: self.env["pos.preset"].create({"name": name})
            for name in ("Default", "Delivery", "Pickup", "Dine In")
        }
        self.basic_config.write({
            "use_presets": True,
            "default_preset_id": presets["Default"].id,
            "available_preset_ids": [Command.set(
                [preset.id for preset in presets.values()]
            )],
        })
        self.store.write({
            "blend_pickup_preset_id": presets["Pickup"].id,
            "blend_dine_in_preset_id": presets["Dine In"].id,
        })
        self._enable_auto_accept()

        for order_type, reference, expected in (
            ("pickup", "BLEND-PRESET-PICKUP", presets["Pickup"]),
            ("dine_in", "BLEND-PRESET-DINE-IN", presets["Dine In"]),
            ("delivery", "BLEND-PRESET-FALLBACK", presets["Default"]),
        ):
            payload = self._blend_request(reference, order_type=order_type)
            result = self.env["bs.delivery.blend.intake"].receive_order(
                payload, "Bearer blend-core-test-token",
            )
            self.assertTrue(result["success"], result)
            order = self.env["bs.delivery.order"].search([
                ("connection_id", "=", self.connection.id),
                ("external_order_id", "=", reference),
            ])
            self.assertEqual(order.pos_order_id.preset_id, expected)
            self.assertEqual(order.pos_order_id.amount_total, 20.0)

    def test_branch_preset_must_be_available_and_not_return(self):
        delivery = self.env["pos.preset"].create({"name": "Delivery"})
        return_preset = self.env["pos.preset"].create({
            "name": "Return", "is_return": True,
        })
        self.basic_config.write({
            "use_presets": True,
            "available_preset_ids": [Command.set(return_preset.ids)],
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.store.write({"blend_delivery_preset_id": delivery.id})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.store.write({"blend_delivery_preset_id": return_preset.id})

        self.basic_config.write({
            "use_presets": False,
            "available_preset_ids": [Command.set(delivery.ids)],
        })
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.store.write({"blend_delivery_preset_id": delivery.id})

    def test_kitchen_display_receives_aggregator_name(self):
        self._enable_auto_accept()
        self.env["bs.delivery.pos.payment.mapping"].create({
            "connection_id": self.connection.id,
            "pos_config_id": self.basic_config.id,
            "aggregator_name": "keeta",
            "payment_type": "credit_visa",
            "payment_method_id": self.bank_pm1.id,
        })
        display = self.env["resta.kds.display"].create({
            "name": "Blend Kitchen",
            "company_id": self.basic_config.company_id.id,
            "pos_config_ids": [Command.set(self.basic_config.ids)],
        })
        payload = self._blend_request(
            "BLEND-KITCHEN-KEETA", aggregator="keeta",
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"], result)
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.pos_order_id.blend_aggregator, "keeta")
        tickets = order.pos_order_id.resta_kds_ticket_ids.filtered(lambda t: t.display_id == display)
        self.assertTrue(tickets, "the delivery order reaches the kitchen screen")
        self.assertIn("keeta", (tickets[0].info or "").lower())

    def test_order_api_uses_core_and_defaults_missing_payment_type_to_card(self):
        payload = self._blend_request()
        service = self.env["bs.delivery.blend.intake"]
        result = service.receive_order(payload, "Bearer blend-core-test-token")
        self.assertTrue(result["success"])
        UUID(result["order_id"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.contract_version, 1)
        self.assertFalse(order.pos_order_id)
        self.assertEqual(order.payment_allocation_ids.external_payment_code, "card")
        self.assertEqual(order.payment_allocation_ids.collected_by, "aggregator")
        self.assertEqual(service.receive_order(deepcopy(payload),
                                               "Bearer blend-core-test-token"), result)
        self.assertEqual(self.env["bs.delivery.order"].search_count([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ]), 1)
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:%s" % payload["partner_reference"])
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_order_api_cash_and_conflicting_replay(self):
        payload = self._blend_request("JAHEZ-CASH", payment_type="Cash")
        service = self.env["bs.delivery.blend.intake"]
        result = service.receive_order(payload, "Bearer blend-core-test-token")
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-CASH"),
        ])
        self.assertEqual(order.payment_allocation_ids.external_payment_code, "cash")
        self.assertEqual(order.payment_allocation_ids.collection_status, "expected")
        changed = deepcopy(payload)
        changed["products"][0]["total"] = "21.00"
        changed["subtotal"] = "21.00"
        changed["total"] = "21.00"
        with self.assertRaises(BlendAPIError) as error:
            service.receive_order(changed, "Bearer blend-core-test-token")
        self.assertEqual(error.exception.status_code, 409)

    def test_order_api_auto_accept_pays_and_invoices(self):
        self._enable_auto_accept()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._blend_request("JAHEZ-AUTO"), "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-AUTO"),
        ])
        self.assertEqual(order.state, "accepted", order.pos_processing_error)
        self.assertEqual(result["order_id"], order.blend_api_order_id)
        self.assertEqual(order.pos_order_id.blend_order_id, result["order_id"])
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_order_api_logs_unreconciled_invoice_before_rollback(self):
        self._enable_auto_accept()
        pos_model = type(self.env["pos.order"])
        with (
            patch.object(pos_model, "_reconcile_invoice_payments", return_value=None),
            self.assertLogs("odoo.addons.bs_pos_delivery_blend.models.delivery_order", level="ERROR") as logs,
            self.assertRaises(BlendAPIError) as error,
        ):
            self.env["bs.delivery.blend.intake"].receive_order(
                self._blend_request("JAHEZ-UNRECONCILED"), "Bearer blend-core-test-token",
            )
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn("The delivery order was not fully paid and invoiced.", str(error.exception))
        self.assertTrue(any("invoice_residual=20.0" in message for message in logs.output))

    def test_closed_session_acceptance_keeps_response_and_status_shape(self):
        self._enable_auto_accept()
        self.basic_config.current_session_id.action_pos_session_closing_control()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._blend_request("JAHEZ-WAIT"), "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-WAIT"),
        ])
        self.assertEqual(set(result), {"success", "message", "order_id"})
        self.assertEqual(order.pos_processing_state, "waiting_session")
        self.assertFalse(order.pos_order_id)
        adapter = self.connection._get_adapter()
        self.connection.blend_schema = "primary"
        payload = adapter._blend_status_payload_for_binding(
            self.connection, order.binding_id, "accepted",
        )
        self.assertEqual(set(payload), {"event", "event_time", "branch_id", "payload"})
        self.assertEqual(set(payload["payload"]), {"order_id", "status"})
        self.assertEqual(payload["payload"]["order_id"], result["order_id"])
        self.connection.blend_schema = "pos"
        pos_payload = adapter._blend_status_payload_for_binding(
            self.connection, order.binding_id, "accepted",
        )
        self.assertEqual(set(pos_payload), {
            "brand_id", "event_time", "event", "branch_id", "order_id", "status",
        })
        self.assertEqual(pos_payload["order_id"], result["order_id"])
        self.connection.blend_outbound_webhook_url = "https://blend.example.test/webhook"
        with patch.object(type(adapter), "_post", return_value={"success": True}) as send:
            adapter.update_order(self.connection, order.binding_id, "accepted")
        self.assertEqual(send.call_args.args[1], self.connection.blend_outbound_webhook_url)
        self.assertEqual(send.call_args.args[2]["event"], "order.updated")

    def test_waiting_session_resumes_as_selected_odoo_user(self):
        user = new_test_user(
            self.env,
            login="blend-session-execution-user", password="SessionExecution#123",
            groups="base.group_user,point_of_sale.group_pos_user,bs_pos_delivery_core.group_delivery_operator",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
        )
        self.connection.write({
            "blend_login_method": "odoo_user", "blend_login_user_id": user.id,
        })
        token = BlendAuthService(self.env).login_connection(user.login, "SessionExecution#123")
        self._enable_auto_accept()
        self.basic_config.current_session_id.action_pos_session_closing_control()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._blend_request("JAHEZ-USER-RESUME"), "Bearer " + token,
        )
        order = self.env["bs.delivery.order"].search([("blend_api_order_id", "=", result["order_id"])])
        self.assertEqual(order.pos_processing_state, "waiting_session")
        acceptance_job = self.env["bs.delivery.outbox"].sudo().search([
            ("binding_id", "=", order.binding_id.id),
            ("operation", "in", ["accept_order", "update_order"]),
        ], order="id desc", limit=1)
        self.assertTrue(acceptance_job)
        with patch.object(type(acceptance_job), "_dispatch_operation", return_value={"ok": True}):
            self.assertTrue(acceptance_job._process_one())
        self.basic_config.open_ui()
        if self.basic_config.current_session_id.state == "opening_control":
            self.basic_config.current_session_id.set_opening_control(0.0, "")
        order._resume_awaiting_pos_session()
        self.assertEqual(order.pos_processing_state, "done", order.pos_processing_error)
        self.assertEqual(order.pos_order_id.create_uid, user)
        self.assertEqual(order.pos_order_id.account_move.create_uid, user)

    def test_invalid_execution_user_does_not_abort_waiting_order_batch(self):
        self._enable_auto_accept()
        self.basic_config.current_session_id.action_pos_session_closing_control()
        orders = self.env["bs.delivery.order"]
        for reference in ("JAHEZ-BLOCKED-USER-1", "JAHEZ-BLOCKED-USER-2"):
            result = self.env["bs.delivery.blend.intake"].receive_order(
                self._blend_request(reference), "Bearer blend-core-test-token",
            )
            orders |= orders.search([("blend_api_order_id", "=", result["order_id"])])
        self.assertEqual(len(orders), 2)
        self.connection.write({
            "blend_login_method": "odoo_user", "blend_login_user_id": self.env.ref("base.public_user").id,
        })
        self.assertTrue(orders._resume_awaiting_pos_session())
        self.assertTrue(all(order.pos_processing_state == "blocked" for order in orders))
        self.assertTrue(all(order.pos_processing_error for order in orders))

    def test_public_http_order_acceptance_pays_and_invoices(self):
        self._enable_auto_accept()
        result = self.env["bs.delivery.blend.intake"].receive_order(
            self._blend_request("JAHEZ-HTTP"),
            "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-HTTP"),
        ])
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        status_result = self.env["bs.delivery.blend.intake"].update_order(
            {
                "branch_id": "BLEND-CORE-BRANCH",
                "brand_id": "BLEND-CORE-BRAND",
                "order_id": result["order_id"],
                "status": "PREPARING",
            },
            "Bearer blend-core-test-token",
        )
        self.assertEqual(status_result["status"], "PREPARING")
        self.assertEqual(order.state, "preparing")
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)

    def test_status_api_accepts_through_core_and_ignores_older_state(self):
        service = self.env["bs.delivery.blend.intake"]
        created = service.receive_order(
            self._blend_request("JAHEZ-STATUS"), "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-STATUS"),
        ])
        update = {
            "branch_id": "BLEND-CORE-BRANCH", "brand_id": "BLEND-CORE-BRAND",
            "order_id": created["order_id"], "status": "ACCEPTED",
        }
        result = service.update_order(update, "Bearer blend-core-test-token")
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        self.assertEqual(service.update_order(update, "Bearer blend-core-test-token"), result)
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        old = dict(update, status="PENDING")
        self.assertEqual(service.update_order(old, "Bearer blend-core-test-token")["status"],
                         "ACCEPTED")
        self.assertEqual(order.state, "accepted")
        stale_key = dict(old, event_id="stale-event-1")
        service.update_order(stale_key, "Bearer blend-core-test-token")
        with self.assertRaises(BlendAPIError) as error:
            service.update_order(dict(stale_key, status="PREPARING"),
                                 "Bearer blend-core-test-token")
        self.assertEqual(error.exception.status_code, 409)

    def test_status_api_rejects_other_branch(self):
        service = self.env["bs.delivery.blend.intake"]
        created = service.receive_order(
            self._blend_request("JAHEZ-OTHER"), "Bearer blend-core-test-token",
        )
        with self.assertRaises(BlendAPIError):
            service.update_order({
                "branch_id": "OTHER", "brand_id": "BLEND-CORE-BRAND",
                "order_id": created["order_id"], "status": "ACCEPTED",
            }, "Bearer blend-core-test-token")

    def test_status_api_rejects_conflicting_terminal_and_financial_update(self):
        service = self.env["bs.delivery.blend.intake"]
        created = service.receive_order(
            self._blend_request("JAHEZ-TERMINAL"), "Bearer blend-core-test-token",
        )
        update = {
            "branch_id": "BLEND-CORE-BRANCH", "brand_id": "BLEND-CORE-BRAND",
            "order_id": created["order_id"], "status": "REJECTED",
        }
        service.update_order(update, "Bearer blend-core-test-token")
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-TERMINAL"),
        ])
        self.assertEqual(order.state, "rejected")
        with self.assertRaises(BlendAPIError) as terminal:
            service.update_order(dict(update, status="CANCELLED"),
                                 "Bearer blend-core-test-token")
        self.assertEqual(terminal.exception.status_code, 409)
        with self.assertRaises(BlendAPIError) as financial:
            service.update_order(dict(update, amount=50),
                                 "Bearer blend-core-test-token")
        self.assertEqual(financial.exception.status_code, 422)
        self.assertEqual(order.state, "rejected")

    def test_legacy_status_webhook_updates_core_before_pos(self):
        created = self.env["bs.delivery.blend.intake"].receive_order(
            self._blend_request("JAHEZ-WEBHOOK"), "Bearer blend-core-test-token",
        )
        response = BlendWebhookService(self.env).handle({
            "event": "order_updated", "event_time": "2026-10-05T10:00:00Z",
            "branch_id": "BLEND-CORE-BRANCH",
            "payload": {"order_id": created["order_id"], "status": "accepted"},
        }, "Bearer blend-core-test-token")
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-WEBHOOK"),
        ])
        self.assertEqual(response["result"]["status"], "accepted")
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_order_api_reverses_vat_and_header_discount(self):
        vat = self.env["account.tax"].create({
            "name": "Blend VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "company_id": self.company.id,
        })
        self.product.write({"taxes_id": [Command.set(vat.ids)]})
        payload = self._blend_request(
            "JAHEZ-VAT", subtotal="100.00", tax="13.50", total="103.50",
            products=[{"id": "MEAL", "quantity": 1,
                       "total": "103.50", "modifiers": []}],
        )
        self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-VAT"),
        ])
        self.assertEqual(order.discount_amount, 10)
        self.assertEqual(order.vat_amount, 13.5)
        self.connection.auto_print_kitchen = True
        with patch.object(type(self.basic_config), "_notify", autospec=True) as notify:
            order.transition_to("accepted", source="automatic", expected_state="received",
                                idempotency_key="accept:JAHEZ-VAT")
        sync_records = next(
            call.args[2]["static_records"] for call in notify.call_args_list
            if call.args[1] == "SYNCHRONISATION"
            and "product.product" in call.args[2]["static_records"]
        )
        discount_product = order.pos_order_id.lines.product_id.filtered(
            lambda product: product.default_code == "BLEND-DISCOUNT"
        )
        self.assertIn(discount_product.id, [
            row["id"] for row in sync_records["product.product"]
        ])
        self.assertFalse(discount_product.available_in_pos)
        self.assertEqual(order.kitchen_print_state, "pending")
        self.assertEqual(order.pos_order_id.amount_total, 103.5)
        self.assertEqual(order.pos_order_id.amount_tax, 13.5)
        item_line = order.pos_order_id.lines.filtered(
            lambda line: line.product_id == self.product
        )
        self.assertEqual(item_line.discount, 0.0)
        self.assertEqual(order.pos_order_id.lines.filtered(
            lambda line: line.product_id.default_code == "BLEND-DISCOUNT"
        ).price_subtotal_incl, -11.50)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertFalse(order.pos_order_id.account_move.amount_residual)

    def test_order_api_accepts_included_vat_when_blend_reports_zero_tax(self):
        vat = self.env["account.tax"].create({
            "name": "Blend included VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        self.product.write({"taxes_id": [Command.set(vat.ids)]})
        second = self.env["product.product"].create({
            "name": "Blend second item", "available_in_pos": True,
            "list_price": 76, "taxes_id": [Command.set(vat.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "SECOND",
            "product_id": second.id,
        })
        self.connection.write({"delivery_payment_mapping_ids": [Command.create({
            "pos_config_id": self.basic_config.id,
            "aggregator_name": "Ninja",
            "payment_type": "credit_visa",
            "payment_method_id": self.bank_pm1.id,
        })]})
        self._enable_auto_accept()
        payload = self._blend_request(
            "ninja: 201346117", aggregator="Ninja", aggregator_order_id="201346117",
            subtotal="202.00", tax="0.00", total="141.40",
            customer={"name": "NINJA", "phone": "555550005"},
            products=[
                {"id": "MEAL", "quantity": 2, "total": 126, "modifiers": []},
                {"id": "SECOND", "quantity": 1, "total": 76, "modifiers": []},
            ],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.amount_total, 141.40)
        self.assertEqual(order.vat_amount, 18.44)
        self.assertEqual(order.pos_order_id.amount_total, 141.40)
        self.assertEqual(order.pos_order_id.amount_tax, 18.44)
        item_lines = order.pos_order_id.lines.filtered(
            lambda line: line.product_id in (self.product | second)
        )
        self.assertCountEqual(
            [(line.qty, order.currency_id.round(line.price_unit),
              line.discount, line.price_subtotal_incl)
             for line in item_lines],
            [(2.0, 63.0, 0.0, 126.0), (1.0, 76.0, 0.0, 76.0)],
        )
        promotion_line = order.pos_order_id.lines - item_lines
        self.assertEqual(len(promotion_line), 1)
        self.assertEqual(promotion_line.product_id.default_code,
                         "BLEND-DISCOUNT")
        self.assertEqual(promotion_line.price_subtotal_incl, -60.60)
        self.assertEqual(promotion_line.price_subtotal, -52.70)
        self.assertEqual(promotion_line.tax_ids, vat)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(order.pos_order_id.account_move.amount_total, 141.40)
        self.assertEqual(order.pos_order_id.account_move.amount_tax, 18.44)
        invoice_lines = order.pos_order_id.account_move.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(len(invoice_lines), 3)
        self.assertCountEqual(
            [(line.quantity, order.currency_id.round(line.price_unit), line.discount)
             for line in invoice_lines.filtered(
                 lambda line: line.product_id in (self.product | second)
             )],
            [(2.0, 63.0, 0.0), (1.0, 76.0, 0.0)],
        )
        self.assertEqual(invoice_lines.filtered(
            lambda line: line.product_id == promotion_line.product_id
        ).price_subtotal, -52.70)
        self.assertEqual(
            self.env["bs.delivery.blend.intake"].receive_order(
                deepcopy(payload), "Bearer blend-core-test-token",
            ), result,
        )
        self.assertEqual(self.env["bs.delivery.order"].search_count([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ]), 1)

    def test_order_api_splits_promotion_by_product_tax(self):
        vat_15 = self.env["account.tax"].create({
            "name": "Blend VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        vat_5 = self.env["account.tax"].create({
            "name": "Blend VAT 5%", "amount_type": "percent", "amount": 5,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        self.product.write({"taxes_id": [Command.set(vat_15.ids)]})
        second = self.env["product.product"].create({
            "name": "Blend 5% item", "available_in_pos": True,
            "list_price": 105, "taxes_id": [Command.set(vat_5.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "SECOND",
            "product_id": second.id,
        })
        self._enable_auto_accept()
        payload = self._blend_request(
            "JAHEZ-MIXED-PROMOTION", subtotal="220.00", tax="0.00",
            total="198.00", payment_type="Cash", products=[
                {"id": "MEAL", "quantity": 1, "total": 115, "modifiers": []},
                {"id": "SECOND", "quantity": 1, "total": 105, "modifiers": []},
            ],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        promotion_lines = order.pos_order_id.lines.filtered(
            lambda line: line.product_id.default_code == "BLEND-DISCOUNT"
        )
        self.assertEqual(len(promotion_lines), 2)
        self.assertCountEqual(
            [(line.price_subtotal_incl, tuple(line.tax_ids.ids))
             for line in promotion_lines],
            [(-11.50, (vat_15.id,)), (-10.50, (vat_5.id,))],
        )
        self.assertEqual(order.pos_order_id.amount_total, 198.0)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        order.transition_to("cancelled", source="manual", expected_state="accepted",
                            idempotency_key="cancel:JAHEZ-MIXED-PROMOTION",
                            reason="Test promotion refund")
        self.assertEqual(order.pos_refund_order_id.amount_total, -198.0)
        self.assertEqual(order.pos_refund_order_id.account_move.state, "posted")

    def test_order_api_accepts_included_vat_without_discount(self):
        vat = self.env["account.tax"].create({
            "name": "Blend included VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        self.product.write({"taxes_id": [Command.set(vat.ids)]})
        self._enable_auto_accept()
        payload = self._blend_request(
            "JAHEZ-INCLUDED-VAT", subtotal="49.00", tax="0.00", total="49.00",
            products=[{"id": "MEAL", "quantity": 1, "total": 49, "modifiers": []}],
        )
        self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.pos_order_id.amount_total, 49)
        self.assertEqual(order.pos_order_id.amount_tax, 6.39)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 49)

    def test_order_api_uses_odoo_total_after_whole_riyal_match(self):
        vat = self.env["account.tax"].create({
            "name": "Jahez included VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        taxable = self.env["product.product"].create({
            "name": "Jahez taxable item", "available_in_pos": True,
            "list_price": 3, "taxes_id": [Command.set(vat.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        second_taxable = self.env["product.product"].create({
            "name": "Jahez second taxable item", "available_in_pos": True,
            "list_price": 3, "taxes_id": [Command.set(vat.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        combo = self.env["product.combo"].create({
            "name": "Jahez untaxed combo choice",
            "combo_item_ids": [Command.create({"product_id": taxable.id})],
        })
        self.product.write({
            "list_price": 96,
            "taxes_id": [Command.clear()],
            "combo_ids": [Command.set(combo.ids)],
            "type": "combo",
        })
        for external_id, product in (
            ("1485", taxable), ("61", second_taxable), ("1483", self.product),
        ):
            self.env["bs.delivery.product.binding"].create({
                "connection_id": self.connection.id,
                "store_binding_id": self.store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })
        self.store.write({"remote_store_id": "1", "remote_vendor_id": "1"})
        self._enable_auto_accept()
        payload = self._blend_request(
            "jahez: 496398788", aggregator="jahez", aggregator_order_id="496398788",
            branch_id="1", brand_id="1", subtotal="111.00", tax="0.00",
            total="111.00", note="", schedule_at=None,
            customer={"name": "JAHEZ", "phone": "555550002"},
            products=[
                {"id": "1485", "quantity": 3, "total": 9, "note": "", "modifiers": []},
                {"id": "61", "quantity": 2, "total": 6, "note": "", "modifiers": []},
                {"id": "1483", "quantity": 1, "total": 96, "note": "", "modifiers": []},
            ],
        )
        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.amount_total, 110.99)
        self.assertEqual(order.vat_amount, 1.96)
        self.assertEqual(order.payment_allocation_ids.amount, 110.99)
        self.assertEqual(order.pos_order_id.amount_total, 110.99)
        self.assertEqual(order.pos_order_id.amount_paid, 110.99)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 110.99)
        self.assertEqual(order.pos_order_id.account_move.amount_tax, 1.96)
        self.assertTrue(order.currency_id.is_zero(order.pos_order_id.account_move.amount_residual))
        self.assertEqual(sum(order.line_ids.mapped("vat_amount")), 1.96)
        self.assertEqual(
            order.line_ids.filtered(lambda line: line.product_id == self.product).vat_amount,
            0.0,
        )
        self.assertIn('"total":"111.00"', order.webhook_event_id.raw_payload)
        replay = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertEqual(replay["order_id"], result["order_id"])

        boundary = deepcopy(payload)
        boundary["partner_reference"] = "jahez: 496398789"
        boundary["aggregator_order_id"] = "496398789"
        boundary["products"][-1]["total"] = "96.50"
        boundary["subtotal"] = "111.50"
        boundary["total"] = "111.50"
        with self.assertRaisesRegex(BlendAPIError, "Delivery financial review required"):
            self.env["bs.delivery.blend.intake"].receive_order(
                boundary, "Bearer blend-core-test-token",
            )
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", boundary["partner_reference"]),
        ]))

    def test_order_api_accepts_jahez_included_vat_with_cent_rounding(self):
        vat = self.env["account.tax"].create({
            "name": "Jahez included VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        self.product.write({"taxes_id": [Command.set(vat.ids)]})
        second = self.env["product.product"].create({
            "name": "Jahez second item", "available_in_pos": True,
            "list_price": 7, "taxes_id": [Command.set(vat.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        for external_id, product in (("1485", self.product), ("51", second)):
            self.env["bs.delivery.product.binding"].create({
                "connection_id": self.connection.id,
                "store_binding_id": self.store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })
        self.store.write({"remote_store_id": "1", "remote_vendor_id": "1"})
        self._enable_auto_accept()
        payload = self._blend_request(
            "jahez: 383881348", aggregator="jahez", aggregator_order_id="383881348",
            branch_id="1", brand_id="1",
            subtotal="16.00", tax="0.00", total="16.00", note="",
            schedule_at=None, customer={"name": "JAHEZ", "phone": "555550001"},
            products=[
                {"id": "1485", "quantity": 3, "total": 9, "note": "", "modifiers": []},
                {"id": "51", "quantity": 1, "total": 7, "note": "", "modifiers": []},
            ],
        )

        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.amount_total, 16.00)
        self.assertEqual(order.vat_amount, 2.09)
        self.assertEqual(order.pos_order_id.amount_total, 16.00)
        self.assertEqual(order.pos_order_id.amount_tax, 2.09)
        self.assertCountEqual(
            [(line.product_id.id, line.qty, line.price_subtotal_incl)
             for line in order.pos_order_id.lines],
            [(self.product.id, 3.0, 9.00), (second.id, 1.0, 7.00)],
            str([(line.product_id.id, line.price_unit, line.price_subtotal,
                  line.price_subtotal_incl) for line in order.pos_order_id.lines]),
        )
        self.assertEqual(order.pos_order_id.account_move.amount_total, 16.00)
        self.assertEqual(order.pos_order_id.account_move.amount_tax, 2.09)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertCountEqual(
            [(line.product_id.id, line.quantity, line.price_total)
             for line in order.pos_order_id.account_move.invoice_line_ids],
            [(self.product.id, 3.0, 9.00), (second.id, 1.0, 7.00)],
        )

    def test_order_api_prices_modifier_quantity_per_parent(self):
        extra = self.env["product.product"].create({
            "name": "Blend Extra", "available_in_pos": True,
            "list_price": 2.5, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "EXTRA", "product_id": extra.id,
        })
        self._attach_blend_modifier_group(self.product, extra)
        payload = self._blend_request(
            "JAHEZ-EXTRAS", subtotal="30.00", total="30.00",
            products=[{
                "id": "MEAL", "quantity": 2, "total": "20.00",
                "modifiers": [{"id": "EXTRA", "quantity": 2, "total": "5.00"}],
            }],
        )
        self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-EXTRAS"),
        ])
        self.assertEqual(len(order.line_ids), 2)
        self.assertEqual(order.line_ids.filtered("is_modifier").quantity, 4)
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:JAHEZ-EXTRAS")
        self.assertEqual(order.pos_order_id.amount_total, 30)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_order_api_accepts_modifier_unit_total_when_header_matches(self):
        extra = self.env["product.product"].create({
            "name": "Blend Unit Extra", "available_in_pos": True,
            "list_price": 2, "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.product.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_product_id": "UNIT-EXTRA", "product_id": extra.id,
        })
        self._attach_blend_modifier_group(self.product, extra)
        payload = self._blend_request(
            "JAHEZ-UNIT-EXTRAS", subtotal="28.00", total="28.00",
            products=[{
                "id": "MEAL", "quantity": 2, "total": "20.00",
                "modifiers": [{"id": "UNIT-EXTRA", "quantity": 2, "total": "2.00"}],
            }],
        )
        self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "JAHEZ-UNIT-EXTRAS"),
        ])
        modifier = order.line_ids.filtered("is_modifier")
        self.assertEqual(modifier.quantity, 4)
        self.assertEqual(modifier.price_subtotal + modifier.vat_amount, 8)
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:JAHEZ-UNIT-EXTRAS")
        self.assertEqual(order.pos_order_id.amount_total, 28)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_order_api_reconciles_blend_modifier_units_across_products(self):
        vat = self.env["account.tax"].create({
            "name": "Blend modifier VAT 15%",
            "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        products = {}
        for external_id in ("28", "61", "49", "11", "32", "17", "35"):
            product = self.env["product.product"].create({
                "name": "Blend item %s" % external_id,
                "available_in_pos": True,
                "list_price": {"17": 0.5, "35": 2}.get(external_id, 1),
                "taxes_id": [Command.set(vat.ids)],
                "property_account_income_id": self.sales_account.id,
            })
            products[external_id] = product
            self.env["bs.delivery.product.binding"].create({
                "connection_id": self.connection.id,
                "store_binding_id": self.store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })
        self._attach_blend_modifier_group(
            products["28"], products["11"] | products["32"], maximum=3,
        )
        self._attach_blend_modifier_group(
            products["49"], products["17"] | products["35"], maximum=3,
        )
        payload = self._blend_request(
            "jahez: 178550709", aggregator="jahez", aggregator_order_id="178550709",
            subtotal="174.00", tax="0.00", total="174.00", products=[
                {"id": "28", "quantity": 3, "total": 108, "modifiers": [
                    {"id": "11", "quantity": 1, "total": "1.00"},
                    {"id": "32", "quantity": 2, "total": "2.00"},
                ]},
                {"id": "61", "quantity": 1, "total": 3, "modifiers": []},
                {"id": "49", "quantity": 2, "total": 48, "modifiers": [
                    {"id": "17", "quantity": 2, "total": "1.00"},
                    {"id": "35", "quantity": 1, "total": "2.00"},
                ]},
            ],
        )

        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(order.amount_total, 174)
        self.assertEqual(order.discount_amount, 0)
        self.assertCountEqual(
            [(line.external_product_id, line.quantity,
              line.price_subtotal + line.vat_amount)
             for line in order.line_ids.filtered("is_modifier")],
            [("11", 3, 3), ("32", 6, 6), ("17", 4, 2), ("35", 2, 4)],
        )
        order.transition_to("accepted", source="automatic", expected_state="received",
                            idempotency_key="accept:jahez: 178550709")
        self.assertEqual(order.pos_order_id.amount_total, 174)
        self.assertEqual(order.pos_order_id.account_move.amount_total, 174)
        self.assertGreater(order.pos_order_id.amount_tax, 0)
        self.assertEqual(order.pos_order_id.amount_tax,
                         order.pos_order_id.account_move.amount_tax)
        self.assertCountEqual(
            [(line.product_id.id, line.qty) for line in order.pos_order_id.lines
             if line.product_id in (products["11"] | products["32"] | products["17"] | products["35"])],
            [(products["11"].id, 3), (products["32"].id, 6),
             (products["17"].id, 4), (products["35"].id, 2)],
        )

    def test_auto_accept_configuration_failure_does_not_acknowledge_order(self):
        self._enable_auto_accept()
        self.basic_config.write({"invoice_journal_id": False})
        with self.assertRaises(BlendAPIError) as error:
            self.env["bs.delivery.blend.intake"].receive_order(
                self._blend_request("JAHEZ-NO-JOURNAL"), "Bearer blend-core-test-token",
            )
        self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("external_order_id", "=", "JAHEZ-NO-JOURNAL"),
            ("connection_id", "=", self.connection.id),
        ]))

    def test_order_api_rejects_untrusted_or_invalid_payment(self):
        service = self.env["bs.delivery.blend.intake"]
        for payload, authorization, expected_status in (
            (self._blend_request(), "Bearer bad-token", 401),
            (self._blend_request(payment_type=""), "Bearer blend-core-test-token", 422),
            (self._blend_request(payment_type="crypto"), "Bearer blend-core-test-token", 422),
            (self._blend_request(branch_id="OTHER"), "Bearer blend-core-test-token", 404),
        ):
            with self.subTest(payload=payload, expected_status=expected_status):
                with self.assertRaises(BlendAPIError) as error:
                    service.receive_order(payload, authorization)
                self.assertEqual(error.exception.status_code, expected_status)

    def test_order_api_rejects_product_hidden_from_pos(self):
        self.product.available_in_pos = False
        with self.assertRaises(BlendAPIError) as error:
            self.env["bs.delivery.blend.intake"].receive_order(
                self._blend_request("HIDDEN-PRODUCT"), "Bearer blend-core-test-token"
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_order_api_invoices_jahez_196_with_included_vat(self):
        vat = self.env["account.tax"].create({
            "name": "Jahez included VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        self.product.write({"taxes_id": [Command.set(vat.ids)]})
        second = self.env["product.product"].create({
            "name": "Jahez second item", "available_in_pos": True,
            "list_price": 4, "taxes_id": [Command.set(vat.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        for external_id, product in (("1483", self.product), ("47", second)):
            self.env["bs.delivery.product.binding"].create({
                "connection_id": self.connection.id,
                "store_binding_id": self.store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })
        self.store.write({"remote_store_id": "1", "remote_vendor_id": "1"})
        self._enable_auto_accept()
        payload = self._blend_request(
            "jahez: 997585655", aggregator="jahez", aggregator_order_id="997585655",
            branch_id="1", brand_id="1", subtotal="196.00", tax="0.00",
            total="196.00", note="", schedule_at=None,
            customer={"name": "JAHEZ", "phone": "555550001"},
            products=[
                {"id": "1483", "quantity": 2, "total": 192, "note": "", "modifiers": []},
                {"id": "47", "quantity": 1, "total": 4, "note": "", "modifiers": []},
            ],
        )

        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        self.assertEqual(len(order), 1)
        pos = order.pos_order_id
        self.assertEqual(order.vat_amount, 25.57)
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.amount_total, 196.00)
        self.assertEqual(pos.amount_tax, 25.57)
        self.assertEqual(pos.amount_paid, 196.00)
        self.assertCountEqual(
            [(line.product_id.id, line.qty, line.price_subtotal_incl)
             for line in pos.lines],
            [(self.product.id, 2.0, 192.00), (second.id, 1.0, 4.00)],
        )
        self.assertEqual(pos.account_move.state, "posted")
        self.assertEqual(pos.account_move.amount_total, 196.00)
        self.assertEqual(pos.account_move.amount_tax, pos.amount_tax)
        self.assertTrue(order.currency_id.is_zero(pos.account_move.amount_residual))

    def test_order_api_invoices_priced_combo_parent_without_selections(self):
        vat = self.env["account.tax"].create({
            "name": "Jahez item VAT 15%", "amount_type": "percent", "amount": 15,
            "type_tax_use": "sale", "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        second = self.env["product.product"].create({
            "name": "Jahez separate item", "available_in_pos": True,
            "list_price": 4, "taxes_id": [Command.set(vat.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        combo = self.env["product.combo"].create({
            "name": "Jahez combo choice",
            "combo_item_ids": [Command.create({"product_id": second.id})],
        })
        self.product.combo_ids = combo
        self.product.type = "combo"
        for external_id, product in (("1483", self.product), ("47", second)):
            self.env["bs.delivery.product.binding"].create({
                "connection_id": self.connection.id,
                "store_binding_id": self.store.id,
                "external_product_id": external_id,
                "product_id": product.id,
            })
        self.store.write({"remote_store_id": "1", "remote_vendor_id": "1"})
        self._enable_auto_accept()
        payload = self._blend_request(
            "jahez: 99758565512222", aggregator="jahez",
            aggregator_order_id="99758565512222", branch_id="1", brand_id="1",
            subtotal="196.00", tax="0.00", total="196.00", note="",
            schedule_at=None,
            customer={"name": "JAHEZ", "phone": "555550001"},
            products=[
                {"id": "1483", "quantity": 2, "total": 192, "note": "", "modifiers": []},
                {"id": "47", "quantity": 1, "total": 4, "note": "", "modifiers": []},
            ],
        )

        result = self.env["bs.delivery.blend.intake"].receive_order(
            payload, "Bearer blend-core-test-token",
        )
        self.assertTrue(result["success"])
        order = self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", payload["partner_reference"]),
        ])
        pos = order.pos_order_id
        self.assertEqual(pos.state, "done")
        self.assertEqual(pos.amount_total, 196.00)
        self.assertEqual(pos.amount_tax, 0.52)
        self.assertEqual(pos.amount_paid, 196.00)
        invoice = pos.account_move
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.amount_total, 196.00)
        self.assertEqual(invoice.amount_tax, 0.52)
        self.assertCountEqual(
            [(line.product_id.id, line.price_total) for line in invoice.invoice_line_ids],
            [(self.product.id, 192.00), (second.id, 4.00)],
        )
        self.assertTrue(order.currency_id.is_zero(invoice.amount_residual))
