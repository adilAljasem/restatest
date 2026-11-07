"""End-to-end contract, order, POS, payment and provider update tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from ..exceptions import DeliveryBusinessError


@tagged("post_install", "-at_install")
class TestCoreV1Flow(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager",
        )
        cls.provider = cls.env["bs.delivery.provider"].create({
            "name": "Core V1 Test Provider", "code": "core_v1_test_provider",
        })
        cls.connection = cls.env["bs.delivery.connection"].create({
            "name": "Core V1 Test Connection",
            "provider_id": cls.provider.id,
            "company_id": cls.company.id,
            "state": "connected",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Core V1 Meal",
            "available_in_pos": True,
            "list_price": 20,
            "taxes_id": [Command.clear()],
            "property_account_income_id": cls.sales_account.id,
        })
        cls.basic_config.open_ui()
        cls.store = cls.env["bs.delivery.store.binding"].create({
            "connection_id": cls.connection.id,
            "pos_config_id": cls.basic_config.id,
            "remote_store_id": "CORE-V1-STORE",
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
                "aggregator_name": "marketplace", "payment_type": "credit_visa",
                "payment_method_id": cls.bank_pm1.id,
            }),
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "marketplace", "payment_type": "cash",
                "payment_method_id": cls.cash_pm1.id,
            }),
        ]})
        cls.Service = cls.env["bs.delivery.order.service"]
        cls.PaymentService = cls.env["bs.delivery.payment.service"]

    def _event(self, external_id, suffix="new", **changes):
        values = {
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "signature_verified": True,
            "external_id": external_id,
            "event_type": "test",
            "idempotency_key": "%s:%s:%s" % (self._testMethodName, external_id, suffix),
            "payload_digest": "0" * 64,
        }
        values.update(changes)
        return self.env["bs.delivery.webhook.event"].sudo().create(values)

    def _payment(self, *, code="card", collector="aggregator", amount=20, reference="PAY-1", status="collected"):
        payment = {
            "external_payment_code": code,
            "external_source": "marketplace",
            "amount": amount,
            "currency": self.basic_config.currency_id.name,
            "collected_by": collector,
            "collection_status": status,
        }
        if reference:
            payment["external_payment_reference"] = reference
        return payment

    def _payload(self, external_id="ORDER-1", *, payments=None, vat=0):
        return {
            "contract_version": 1,
            "external_order_id": external_id,
            "external_source": "marketplace",
            "currency": self.basic_config.currency_id.name,
            "fulfillment_type": "delivery",
            "price_basis": "tax_excluded",
            "customer": {"name": "Core Test Customer", "phone": "111222333"},
            "lines": [{
                "external_line_id": "line-1", "external_product_id": "MEAL",
                "name": "Meal", "quantity": 1, "price_unit": 20,
                "discount_amount": 0, "vat_amount": vat, "price_subtotal": 20,
            }],
            "totals": {
                "items_subtotal": 20, "discount_amount": 0,
                "fee_amount": 0, "vat_amount": vat,
                "rounding_amount": 0, "amount_total": 20 + vat,
            },
            "payments": payments if payments is not None else [self._payment()],
        }

    def test_blocked_pos_processing_has_synchronization_log(self):
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("POS-AUDIT-FAILURE"),
            event=self._event("POS-AUDIT-FAILURE"),
        )
        order._set_pos_processing_result("blocked", "POS accounting failed")
        order._set_pos_processing_result("blocked", "POS accounting failed")

        logs = self.env["bs.delivery.sync.log"].search([
            ("connection_id", "=", self.connection.id),
            ("operation", "=", "core:pos_processing"),
            ("external_id", "=", "POS-AUDIT-FAILURE"),
        ])
        self.assertEqual(len(logs), 2)
        for log in logs:
            self.assertEqual(log.status, "failed")
            self.assertEqual(log.binding_id, order.binding_id)
            self.assertIn("POS accounting failed", log.error_message)

    def test_exact_replay_and_changed_snapshot(self):
        payload = self._payload()
        first = self._event("ORDER-1")
        order = self.Service._receive_order(self.connection, self.store, payload, event=first)
        self.assertEqual(order.state, "received")
        self.assertEqual(order.contract_version, 1)
        self.assertEqual(order.payment_allocation_ids.collection_status, "collected")
        self.assertFalse(order.pos_order_id)

        duplicate = self.Service._receive_order(
            self.connection, self.store, deepcopy(payload), event=self._event("ORDER-1", "replay"),
        )
        self.assertEqual(duplicate, order)
        equivalent = deepcopy(payload)
        equivalent["lines"][0]["price_unit"] = 20.0
        self.assertEqual(self.Service._receive_order(
            self.connection, self.store, equivalent,
            event=self._event("ORDER-1", "equivalent"),
        ), order)
        self.assertEqual(self.env["bs.delivery.order"].search_count([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "ORDER-1"),
        ]), 1)
        changed = deepcopy(payload)
        changed["external_reference"] = "DIFFERENT"
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.Service._receive_order(
                self.connection, self.store, changed, event=self._event("ORDER-1", "conflict"),
            )
        self.assertEqual(raised.exception.code, "order_snapshot_conflict")

    def test_unknown_product_id_is_rejected_before_order_creation(self):
        payload = self._payload(external_id="UNKNOWN-PRODUCT-ORDER")
        payload["lines"][0]["external_product_id"] = "UNKNOWN-PRODUCT"
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.Service._receive_order(
                self.connection, self.store, payload,
                event=self._event("UNKNOWN-PRODUCT-ORDER"),
            )
        self.assertEqual(raised.exception.code, "product_not_found")
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id),
            ("external_order_id", "=", "UNKNOWN-PRODUCT-ORDER"),
        ]))

    def test_acceptance_creates_pos_and_posts_confirmed_payment_once(self):
        self.basic_config.iface_print_auto = True
        event = self._event("ORDER-1")
        order = self.Service._receive_order(
            self.connection, self.store, self._payload(), event=event,
        )
        order.transition_to("accepted", source="manual", expected_state="received")

        self.assertTrue(order.pos_order_id)
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        self.assertEqual(order.cashier_receipt_state, "pending")
        self.assertEqual(order.cashier_receipt_attempts, 0)
        self.assertTrue(order.cashier_receipt_requested_at)
        self.assertEqual(order.pos_order_id.amount_paid, 20)
        allocation = order.payment_allocation_ids
        self.assertEqual(allocation.pos_payment_id, order.pos_order_id.payment_ids)
        self.assertEqual(self.PaymentService._record_payment(
            order, self._payment(), event=event,
        ), allocation.pos_payment_id)
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)

    def test_cashier_receipt_is_claimed_once_for_current_pos_session(self):
        self.basic_config.iface_print_auto = True
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("RECEIPT-ONCE"),
            event=self._event("RECEIPT-ONCE"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        jobs = order.get_pending_cashier_receipts(self.basic_config.id)
        self.assertEqual([job["order_id"] for job in jobs], [order.id])
        first = "cashier-device-11111111-1111-1111-1111-111111111111"
        second = "cashier-device-22222222-2222-2222-2222-222222222222"
        self.assertTrue(order.claim_cashier_receipt(order.id, self.basic_config.id, first))
        self.assertFalse(order.claim_cashier_receipt(order.id, self.basic_config.id, second))
        self.assertEqual(order.cashier_receipt_attempts, 1)
        self.assertEqual(order.get_pending_cashier_receipts(self.basic_config.id), [])
        order.acknowledge_cashier_receipt(order.id, self.basic_config.id, first, True)
        self.assertEqual(order.cashier_receipt_state, "sent")
        self.assertFalse(order.claim_cashier_receipt(order.id, self.basic_config.id, second))

    def test_cashier_receipt_failure_waits_for_manager_retry(self):
        self.basic_config.iface_print_auto = True
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("RECEIPT-RETRY"),
            event=self._event("RECEIPT-RETRY"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        first = "cashier-retry-first-11111111-1111-1111-111111111111"
        second = "cashier-retry-second-22222222-2222-2222-222222222222"
        self.assertTrue(order.claim_cashier_receipt(order.id, self.basic_config.id, first))
        order.acknowledge_cashier_receipt(
            order.id, self.basic_config.id, first, False, "Printer unavailable",
        )
        self.assertEqual(order.cashier_receipt_state, "error")
        self.assertEqual(order.get_pending_cashier_receipts(self.basic_config.id), [])
        self.assertFalse(order.claim_cashier_receipt(order.id, self.basic_config.id, second))
        order.action_retry_cashier_receipt()
        self.assertTrue(order.claim_cashier_receipt(order.id, self.basic_config.id, second))
        self.assertEqual(order.cashier_receipt_attempts, 2)

    def test_cashier_receipt_skips_already_printed_pos_order(self):
        self.basic_config.iface_print_auto = True
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("RECEIPT-MANUAL"),
            event=self._event("RECEIPT-MANUAL"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        order.pos_order_id.nb_print = 1
        self.assertEqual(order.get_pending_cashier_receipts(self.basic_config.id), [])
        self.assertEqual(order.cashier_receipt_state, "sent")
        self.assertEqual(order.cashier_receipt_attempts, 0)

    def test_cashier_receipt_respects_pos_auto_print_setting(self):
        self.basic_config.iface_print_auto = False
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("RECEIPT-DISABLED"),
            event=self._event("RECEIPT-DISABLED"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.cashier_receipt_state, "not_requested")
        self.assertEqual(order.get_pending_cashier_receipts(self.basic_config.id), [])

    def test_cashier_notification_failure_does_not_undo_paid_order(self):
        self.basic_config.iface_print_auto = True
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("RECEIPT-BUS-FAIL"),
            event=self._event("RECEIPT-BUS-FAIL"),
        )
        with patch.object(
            type(order), "_dispatch_cashier_receipt_job",
            side_effect=RuntimeError("POS notification unavailable"),
        ):
            order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(order.cashier_receipt_state, "pending")

    def test_acceptance_requires_customer_without_partial_financial_records(self):
        payload = self._payload("NO-CUSTOMER")
        payload.pop("customer")
        order = self.Service._receive_order(
            self.connection, self.store, payload,
            event=self._event("NO-CUSTOMER"),
        )
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(order.state, "received")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.transition_ids)

    def test_core_acceptance_posts_paid_invoice_once(self):
        payload = self._payload("CORE-INVOICE")
        payload["customer"] = {"name": "Core Invoice Customer", "phone": "111222333"}
        order = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("CORE-INVOICE"),
        )
        first = order.transition_to(
            "accepted", source="manual", expected_state="received",
            idempotency_key="accept:CORE-INVOICE",
        )
        pos_order = order.pos_order_id
        self.assertEqual(pos_order.state, "done")
        self.assertEqual(pos_order.account_move.state, "posted")
        self.assertTrue(self.basic_config.currency_id.is_zero(pos_order.account_move.amount_residual))
        self.assertEqual(len(pos_order.payment_ids), 1)
        self.assertEqual(order.transition_to(
            "accepted", source="manual", idempotency_key="accept:CORE-INVOICE",
        ), first)
        self.assertEqual(len(pos_order.payment_ids), 1)

    def test_auto_accept_uses_connection_default_customer_when_payload_omits_customer(self):
        customer = self.env["res.partner"].create({
            "name": "Core Branch Invoice Customer", "company_id": self.company.id,
        })
        self.connection.write({
            "delivery_default_invoice_partner_id": customer.id,
            "delivery_accept_action": "accept",
        })
        payload = self._payload("DEFAULT-CUSTOMER")
        payload.pop("customer")
        order = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("DEFAULT-CUSTOMER"),
        )
        self.assertEqual(order.state, "accepted", order.pos_processing_error)
        self.assertEqual(order.pos_order_id.partner_id, customer)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_auto_accept_waits_for_closed_session_then_posts_once(self):
        customer = self.env["res.partner"].create({
            "name": "Waiting Session Customer", "company_id": self.company.id,
        })
        self.connection.write({
            "delivery_default_invoice_partner_id": customer.id,
            "delivery_accept_action": "accept",
        })
        self.basic_config.current_session_id.action_pos_session_closing_control()
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("WAITING-SESSION"),
            event=self._event("WAITING-SESSION"),
        )
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_processing_state, "waiting_session")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.payment_allocation_ids.pos_payment_id)
        self.assertTrue(order.session_activity_id)

        job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
            self.connection, "accept_order", order.binding_id,
            idempotency_key="waiting-session-accept",
        )

        self.basic_config.open_ui()
        if self.basic_config.current_session_id.state == "opening_control":
            self.basic_config.current_session_id.set_opening_control(0.0, "")
        order._resume_awaiting_pos_session()
        self.assertFalse(order.pos_order_id)
        with patch.object(type(job), "_dispatch_operation", return_value={"ok": True}):
            self.assertTrue(job._process_one())
        self.assertEqual(job.state, "done")
        self.env["bs.delivery.order"]._cron_resume_awaiting_pos_sessions()
        self.assertEqual(order.pos_processing_state, "done")
        self.assertFalse(order.session_activity_id)
        self.assertEqual(order.binding_id.sync_state, "synced")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        order._resume_awaiting_pos_session()
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)

    def test_cancelled_waiting_order_never_creates_pos(self):
        customer = self.env["res.partner"].create({"name": "Cancelled waiting customer"})
        self.connection.write({
            "delivery_default_invoice_partner_id": customer.id,
            "delivery_accept_action": "accept",
        })
        self.basic_config.current_session_id.action_pos_session_closing_control()
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("WAITING-CANCEL"),
            event=self._event("WAITING-CANCEL"),
        )
        job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
            self.connection, "accept_order", order.binding_id,
            idempotency_key="cancelled-waiting-accept",
        )
        order.transition_to("cancelled", source="provider", expected_state="accepted")
        with patch.object(type(job), "_dispatch_operation") as dispatch:
            self.assertFalse(job._process_one())
        dispatch.assert_not_called()
        self.assertEqual(job.state, "cancelled")
        self.basic_config.open_ui()
        if self.basic_config.current_session_id.state == "opening_control":
            self.basic_config.current_session_id.set_opening_control(0.0, "")
        self.env["bs.delivery.order"]._cron_resume_awaiting_pos_sessions()
        self.assertFalse(order.pos_order_id)

    def test_manual_policy_stays_received_while_session_is_closed(self):
        self.basic_config.current_session_id.action_pos_session_closing_control()
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("WAITING-MANUAL"),
            event=self._event("WAITING-MANUAL"),
        )
        self.assertEqual(order.state, "received")
        self.assertEqual(order.pos_processing_state, "pending")
        self.assertFalse(order.pos_order_id)

    def test_closed_session_does_not_hide_missing_invoice_journal(self):
        customer = self.env["res.partner"].create({"name": "No journal customer"})
        self.connection.write({
            "delivery_default_invoice_partner_id": customer.id,
            "delivery_accept_action": "accept",
        })
        self.basic_config.current_session_id.action_pos_session_closing_control()
        self.basic_config.invoice_journal_id = False
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("WAITING-NO-JOURNAL"),
            event=self._event("WAITING-NO-JOURNAL"),
        )
        self.assertEqual(order.state, "received")
        self.assertEqual(order.pos_processing_state, "blocked")
        self.assertFalse(order.pos_order_id)

    def test_expired_acceptance_never_dispatches_or_settles_pos(self):
        customer = self.env["res.partner"].create({"name": "Expired wait customer"})
        self.connection.write({
            "delivery_default_invoice_partner_id": customer.id,
            "delivery_accept_action": "accept",
        })
        self.basic_config.current_session_id.action_pos_session_closing_control()
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("WAITING-EXPIRED"),
            event=self._event("WAITING-EXPIRED"),
        )
        job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
            self.connection, "accept_order", order.binding_id,
            idempotency_key="expired-waiting-accept",
        )
        order._with_delivery_internal_update().write({
            "acceptance_deadline_at": fields.Datetime.now() - timedelta(seconds=1),
        })
        with patch.object(type(job), "_dispatch_operation") as dispatch:
            self.assertFalse(job._process_one())
        dispatch.assert_not_called()
        self.assertEqual(job.state, "failed")
        self.assertEqual(order.pos_processing_state, "blocked")
        job.write({"state": "retry", "next_attempt_at": fields.Datetime.now()})
        with patch.object(type(job), "_dispatch_operation") as late_dispatch:
            self.assertFalse(job._process_one())
        late_dispatch.assert_not_called()
        self.assertEqual(job.state, "failed")
        self.basic_config.open_ui()
        if self.basic_config.current_session_id.state == "opening_control":
            self.basic_config.current_session_id.set_opening_control(0.0, "")
        self.env["bs.delivery.order"]._cron_resume_awaiting_pos_sessions()
        self.assertFalse(order.pos_order_id)

    def test_manager_retry_settles_accounting_after_session_failure(self):
        customer = self.env["res.partner"].create({"name": "Retry waiting customer"})
        self.connection.write({
            "delivery_default_invoice_partner_id": customer.id,
            "delivery_accept_action": "accept",
        })
        self.basic_config.current_session_id.action_pos_session_closing_control()
        order = self.Service._receive_order(
            self.connection, self.store, self._payload("WAITING-RETRY"),
            event=self._event("WAITING-RETRY"),
        )
        job, _created = self.env["bs.delivery.outbox"].sudo().enqueue(
            self.connection, "accept_order", order.binding_id,
            idempotency_key="retry-waiting-accept",
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
        self.basic_config.invoice_journal_id = journal
        order.action_retry_pos_processing()
        self.assertEqual(order.pos_processing_state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_missing_payment_or_invoice_journal_rolls_back_acceptance(self):
        unpaid = self.Service._receive_order(
            self.connection, self.store, self._payload("UNPAID", payments=[]),
            event=self._event("UNPAID"),
        )
        with self.assertRaises(UserError), self.env.cr.savepoint():
            unpaid.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(unpaid.state, "received")
        self.assertFalse(unpaid.pos_order_id)

        no_journal = self.Service._receive_order(
            self.connection, self.store, self._payload("NO-JOURNAL"),
            event=self._event("NO-JOURNAL"),
        )
        self.basic_config.invoice_journal_id = False
        with self.assertRaises(UserError), self.env.cr.savepoint():
            no_journal.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(no_journal.state, "received")
        self.assertFalse(no_journal.pos_order_id)

    def test_manual_customer_wizard_completes_paid_invoice(self):
        customer = self.env["res.partner"].create({
            "name": "Chosen Delivery Customer", "company_id": self.company.id,
        })
        payload = self._payload("CHOOSE-CUSTOMER")
        payload.pop("customer")
        order = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("CHOOSE-CUSTOMER"),
        )
        action = order.action_accept()
        self.assertEqual(action["res_model"], "bs.delivery.accept.customer.wizard")
        wizard = self.env["bs.delivery.accept.customer.wizard"].create({
            "order_id": order.id, "partner_id": customer.id,
        })
        wizard.action_confirm()
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_order_id.partner_id, customer)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_pos_board_accepts_chosen_delivery_customer(self):
        customer = self.env["res.partner"].create({
            "name": "POS Delivery Customer", "company_id": self.company.id,
        })
        payload = self._payload("POS-CHOOSE-CUSTOMER")
        payload.pop("customer")
        order = self.Service._receive_order(
            self.connection, self.store, payload,
            event=self._event("POS-CHOOSE-CUSTOMER"),
        )
        self.assertTrue(order._pos_board_values()["needs_invoice_partner"])
        self.env["bs.delivery.order"].action_pos_transition(
            order.id, "accepted", provider_id=self.provider.id,
            pos_config_id=self.basic_config.id, invoice_partner_id=customer.id,
        )
        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_order_id.partner_id, customer)
        self.assertEqual(order.pos_order_id.account_move.state, "posted")

    def test_expected_cash_posts_at_acceptance_and_collection_does_not_duplicate(self):
        expected = self._payment(code="cash", collector="unknown", amount=10, reference=None, status="expected")
        collected = self._payment(amount=10)
        event = self._event("ORDER-2")
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload("ORDER-2", payments=[expected, collected]), event=event,
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(order.pos_order_id.amount_paid, 20)
        self.assertEqual(order.payment_allocation_ids.filtered(lambda row: row.external_payment_code == "cash").collection_status, "expected")
        with self.assertRaises(DeliveryBusinessError):
            self.PaymentService._record_payment(order, expected, event=event)
        self.assertEqual(order.pos_order_id.amount_paid, 20)

        confirmation = self._payment(code="cash", collector="restaurant", amount=10, reference="PAY-2")
        payment = self.PaymentService._record_payment(
            order, confirmation, event=self._event("ORDER-2", "payment"),
        )
        self.assertEqual(payment.amount, 10)
        self.assertEqual(order.pos_order_id.amount_paid, 20)
        self.assertEqual(len(order.pos_order_id.payment_ids), 2)
        self.assertEqual(len(order.payment_allocation_ids), 2)
        self.assertEqual(order.payment_allocation_ids.filtered(lambda row: row.external_payment_code == "cash").collection_status, "collected")
        self.assertTrue(order.is_prepaid)

    def test_collection_updates_operator_visible_expected_cash(self):
        expected = self._payment(
            code="cash", collector="unknown", reference=None, status="expected",
        )
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload(payments=[expected]), event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        operator = new_test_user(
            self.env, login="delivery_cash_operator",
            groups="bs_pos_delivery_core.group_delivery_operator",
            company_id=self.company.id,
        )
        allocation = order.payment_allocation_ids.with_user(operator)
        confirmation = self._payment(
            code="cash", collector="restaurant", reference="PAY-OPERATOR",
        )
        payment = self.PaymentService.sudo()._record_payment(
            order, confirmation, event=self._event("ORDER-1", "collection"),
            allocation=allocation,
        )

        self.assertEqual(order.pos_order_id.amount_paid, 20)
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        self.assertEqual(allocation.collection_status, "collected")
        self.assertEqual(allocation.pos_payment_id, payment)

    def test_operator_accept_action_posts_expected_cash(self):
        expected = self._payment(
            code="cash", collector="unknown", reference=None, status="expected",
        )
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload(payments=[expected]), event=self._event("ORDER-1"),
        )
        operator = new_test_user(
            self.env, login="delivery_cash_action_operator",
            groups="bs_pos_delivery_core.group_delivery_operator",
            company_id=self.company.id,
        )

        order.with_user(operator).action_accept()

        self.assertEqual(order.state, "accepted")
        self.assertEqual(order.pos_order_id.amount_paid, 20)
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)

    def test_expected_payment_with_reference_is_promoted_in_place(self):
        expected = self._payment(
            code="cash", collector="unknown", reference="PAY-EXPECTED",
            status="expected",
        )
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload(payments=[expected]), event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        allocation = order.payment_allocation_ids
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        self.assertEqual(allocation.collection_status, "expected")

        collected = self._payment(
            code="cash", collector="restaurant", reference="PAY-EXPECTED",
        )
        payment = self.PaymentService._record_payment(
            order, collected, event=self._event("ORDER-1", "collection"),
        )
        self.assertEqual(order.payment_allocation_ids, allocation)
        self.assertEqual(allocation.collection_status, "collected")
        self.assertEqual(allocation.pos_payment_id, payment)
        self.assertEqual(order.pos_order_id.amount_paid, 20)
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)

    def test_identical_expected_cash_allocations_each_post_once(self):
        expected = self._payment(
            code="cash", collector="unknown", amount=10,
            reference=None, status="expected",
        )
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload(payments=[expected, deepcopy(expected)]),
            event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")

        self.assertEqual(len(order.payment_allocation_ids), 2)
        self.assertEqual(len(order.pos_order_id.payment_ids), 2)
        self.assertEqual(order.pos_order_id.amount_paid, 20)
        self.assertEqual(len(order.payment_allocation_ids.mapped("pos_payment_id")), 2)

        confirmation = self._payment(
            code="cash", collector="restaurant", amount=10,
            reference="PAY-AMBIGUOUS",
        )
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.PaymentService._record_payment(
                order, confirmation, event=self._event("ORDER-1", "collection"),
            )
        self.assertEqual(raised.exception.code, "payment_reference_conflict")

    def test_invalid_event_and_pos_failure_leave_no_partial_pos(self):
        payload = self._payload(payments=[])
        wrong_event = self._event("OTHER")
        with self.assertRaises(DeliveryBusinessError):
            self.Service._receive_order(self.connection, self.store, payload, event=wrong_event)
        self.assertFalse(self.env["bs.delivery.order"].search([
            ("connection_id", "=", self.connection.id), ("external_order_id", "=", "ORDER-1"),
        ]))

        bad_payload = self._payload(vat=1, payments=[])
        order = self.Service._receive_order(
            self.connection, self.store, bad_payload, event=self._event("ORDER-1", "bad-tax"),
        )
        original_pos_count = self.env["pos.order"].search_count([])
        with self.assertRaises(UserError):
            order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(order.state, "received")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.binding_id.pos_order_id)
        self.assertEqual(self.env["pos.order"].search_count([]), original_pos_count)

    def test_draft_connection_cannot_enter_core_with_tryorder_test_context(self):
        self.connection.state = "draft"
        self.assertNotIn(
            self.connection,
            self.env["bs.delivery.connection"]._for_pos_board(self.basic_config),
        )
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.Service.with_context(
                bs_tryorder_draft_test_connection_id=self.connection.id,
            )._receive_order(
                self.connection, self.store, self._payload(),
                event=self._event("ORDER-DRAFT"),
            )
        self.assertEqual(raised.exception.code, "invalid_connection")

    def test_provider_progression_and_stale_update(self):
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload(), event=self._event("ORDER-1"),
        )
        when = datetime.now(timezone.utc)
        update = {
            "contract_version": 1,
            "state": "ready",
            "occurred_at": when.isoformat(),
            "external_status": "READY",
        }
        self.Service._apply_provider_update(
            self.connection, self.store, "ORDER-1", update,
            event=self._event("ORDER-1", "ready"),
        )
        self.assertEqual(order.state, "ready")
        self.assertEqual(order.external_status, "READY")
        self.assertEqual(len(order.transition_ids), 3)
        stale = dict(update, state="accepted", occurred_at=(when - timedelta(minutes=1)).isoformat())
        self.Service._apply_provider_update(
            self.connection, self.store, "ORDER-1", stale,
            event=self._event("ORDER-1", "stale"),
        )
        self.assertEqual(order.state, "ready")
        delivered = dict(update, state="delivered", occurred_at=(when + timedelta(minutes=1)).isoformat())
        self.Service._apply_provider_update(
            self.connection, self.store, "ORDER-1", delivered,
            event=self._event("ORDER-1", "delivered"),
        )
        self.assertEqual(order.state, "delivered")
        self.assertEqual(order.pos_processing_state, "done")

    def test_charged_modifier_and_fee_reach_pos_once(self):
        modifier = self.env["product.product"].create({
            "name": "Core V1 Extra",
            "available_in_pos": True,
            "list_price": 1,
            "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        fee_product = self.env["product.product"].create({
            "name": "Core V1 Delivery Fee",
            "available_in_pos": True,
            "list_price": 3,
            "taxes_id": [Command.clear()],
            "property_account_income_id": self.sales_account.id,
        })
        self.env["bs.delivery.modifier.binding"].create({
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_modifier_id": "EXTRA",
            "product_id": modifier.id,
        })
        self.store.delivery_fee_product_id = fee_product
        payload = self._payload(payments=[])
        payload["lines"].insert(0, {
            "external_line_id": "extra", "external_product_id": "EXTRA",
            "parent_external_line_id": "line-1", "name": "Extra",
            "quantity": 1, "price_unit": 1, "discount_amount": 0,
            "vat_amount": 0, "price_subtotal": 1,
        })
        payload["fees"] = [{"kind": "delivery", "amount": 3, "vat_amount": 0}]
        payload["totals"].update(items_subtotal=21, fee_amount=3, amount_total=24)
        payload["payments"] = [self._payment(amount=24)]
        event = self._event("ORDER-1")
        order = self.Service._receive_order(self.connection, self.store, payload, event=event)
        order.transition_to("accepted", source="manual", expected_state="received")

        self.assertEqual(len(order.line_ids), 2)
        self.assertEqual(order.line_ids.filtered("is_modifier").parent_line_id.external_line_id, "line-1")
        self.assertEqual(len(order.pos_order_id.lines), 3)
        self.assertEqual(order.pos_order_id.amount_total, 24)

    def test_tax_included_product_price_is_converted_from_contract_net_price(self):
        tax = self.env["account.tax"].create({
            "name": "Core V1 included 15%",
            "amount": 15,
            "amount_type": "percent",
            "price_include_override": "tax_included",
            "company_id": self.company.id,
        })
        self.product.taxes_id = [Command.set(tax.ids)]
        payload = self._payload(payments=[self._payment(amount=23)], vat=3)
        order = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(order.pos_order_id.amount_tax, 3)
        self.assertEqual(order.pos_order_id.amount_total, 23)

    def test_platform_collected_cancellation_requires_review(self):
        order = self.Service._receive_order(
            self.connection, self.store, self._payload(), event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        order.transition_to("cancelled", source="webhook", expected_state="accepted")

        self.assertEqual(order.state, "cancelled")
        self.assertEqual(order.pos_processing_state, "blocked")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertFalse(order.pos_refund_order_id)

    def test_restaurant_collected_full_payment_completes_native_pos_order(self):
        payload = self._payload(payments=[self._payment(code="cash", collector="restaurant")])
        order = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        for state in ("preparing", "ready", "picked_up", "delivered"):
            order.transition_to(state, source="webhook", expected_state=order.state)

        self.assertEqual(order.state, "delivered")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertIn(order.pos_order_id.state, ("paid", "done", "invoiced"))

    def test_post_invoice_payment_and_reference_conflict_leave_single_payment(self):
        initial_payment = self._payment(amount=20, reference="PAY-A")
        order = self.Service._receive_order(
            self.connection, self.store,
            self._payload(payments=[initial_payment]), event=self._event("ORDER-1"),
        )
        order.transition_to("accepted", source="manual", expected_state="received")
        self.assertEqual(self.PaymentService._record_payment(
            order, initial_payment, event=self._event("ORDER-1", "payment-a"),
        ), order.pos_order_id.payment_ids)
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.PaymentService._record_payment(
                order, self._payment(amount=1, reference="PAY-B"),
                event=self._event("ORDER-1", "payment-b"),
            )
        self.assertEqual(raised.exception.code, "pos_unavailable")
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.PaymentService._record_payment(
                order, self._payment(amount=14, reference="PAY-A"),
                event=self._event("ORDER-1", "payment-conflict"),
            )
        self.assertEqual(raised.exception.code, "payment_reference_conflict")
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        self.assertEqual(order.pos_order_id.amount_paid, 20)

    def test_existing_order_callbacks_work_while_new_intake_is_paused(self):
        payload = self._payload()
        order = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("ORDER-1"),
        )
        self.connection.paused = True
        duplicate = self.Service._receive_order(
            self.connection, self.store, payload, event=self._event("ORDER-1", "replay"),
        )
        self.assertEqual(duplicate, order)
        with self.assertRaises(DeliveryBusinessError) as raised:
            self.Service._receive_order(
                self.connection, self.store, self._payload("ORDER-2", payments=[]),
                event=self._event("ORDER-2"),
            )
        self.assertEqual(raised.exception.code, "receiving_paused")
        self.Service._apply_provider_update(
            self.connection, self.store, "ORDER-1",
            {
                "contract_version": 1, "state": "accepted",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
            event=self._event("ORDER-1", "status"),
        )
        self.assertEqual(order.state, "accepted")
        self.assertFalse(self.env["bs.delivery.outbox"].search([
            ("binding_id", "=", order.binding_id.id),
        ]))
