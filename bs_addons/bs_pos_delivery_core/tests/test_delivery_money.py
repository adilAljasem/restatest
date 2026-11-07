from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install")
class TestDeliveryMoney(TestPoSCommon):
    """Exercise imported totals against the real POS/account.tax computation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "bs_pos_delivery_core.group_delivery_integration_manager",
        )
        cls.provider = cls.env["bs.delivery.provider"].create(
            {"name": "Money Test Provider", "code": "money_test"},
        )
        cls.connection = cls.env["bs.delivery.connection"].create(
            {"name": "Money Test Connection", "provider_id": cls.provider.id},
        )
        cls.tax = cls.env["account.tax"].create(
            {
                "name": "Delivery 15% included",
                "amount": 15,
                "amount_type": "percent",
                "price_include_override": "tax_included",
                "company_id": cls.company.id,
            },
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Delivery meal",
                "available_in_pos": True,
                "list_price": 34.5,
                "taxes_id": [Command.set(cls.tax.ids)],
                "property_account_income_id": cls.sales_account.id,
            },
        )
        cls.fee_product = cls.env["product.product"].create(
            {
                "name": "Restaurant delivery fee",
                "type": "service",
                "taxes_id": [Command.set(cls.tax.ids)],
                "property_account_income_id": cls.sales_account.id,
            },
        )
        cls.adjustment_product = cls.env["product.product"].create(
            {
                "name": "Legacy adjustment",
                "type": "service",
                "taxes_id": [Command.clear()],
                "property_account_income_id": cls.sales_account.id,
            },
        )
        if "online_account_payment_id" in cls.env["pos.payment"]._fields:
            offline_provider = cls.env["payment.provider"].create({
                "name": "Delivery offline test provider", "code": "none",
                "state": "test", "is_published": True,
                "company_id": cls.company.id,
                "journal_id": cls.bank_pm1.journal_id.id,
            })
            cls.online_method = cls.env["pos.payment.method"].create({
                "name": "Delivery online payment", "is_online_payment": True,
                "company_id": cls.company.id,
                "online_payment_provider_ids": [Command.set(offline_provider.ids)],
            })
            cls.basic_config.payment_method_ids |= cls.online_method
        cls.basic_config.open_ui()
        cls.store = cls.env["bs.delivery.store.binding"].create(
            {
                "connection_id": cls.connection.id,
                "pos_config_id": cls.basic_config.id,
                "remote_store_id": "MONEY-STORE",
                "default_payment_method_id": cls.bank_pm1.id,
                "delivery_fee_product_id": cls.fee_product.id,
                "adjustment_product_id": cls.adjustment_product.id,
            },
        )
        cls.connection.write({"delivery_payment_mapping_ids": [
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "money_test", "payment_type": "credit_visa",
                "payment_method_id": cls.bank_pm1.id,
            }),
            Command.create({
                "pos_config_id": cls.basic_config.id,
                "aggregator_name": "money_test", "payment_type": "cash",
                "payment_method_id": cls.cash_pm1.id,
            }),
        ]})

    def _order(self, **overrides):
        values = {
            "connection_id": self.connection.id,
            "store_binding_id": self.store.id,
            "external_order_id": self._testMethodName,
            "currency_id": self.company.currency_id.id,
            "is_prepaid": True,
            "external_source": "money_test",
            "remote_payment_method": "visa" if overrides.get("is_prepaid", True) else "cash",
            "customer_name": "Money Test Customer",
            "items_subtotal": 90,
            "vat_amount": 13.5,
            "amount_total": 103.5,
            "line_ids": [
                Command.create(
                    {
                        "name": "Delivery meal",
                        "product_id": self.product.id,
                        "quantity": 3,
                        "price_unit": 34.5,
                        "price_subtotal": 90,
                        "vat_amount": 13.5,
                    },
                ),
            ],
        }
        values.update(overrides)
        return self.env["bs.delivery.order"]._with_delivery_internal_update().create(values)

    def test_included_tax_and_quantity_survive_pos_recomputation(self):
        order = self._order()
        pos_order = order.create_pos_order()
        pos_order._compute_prices()
        self.assertAlmostEqual(pos_order.amount_total, 103.5, places=2)
        self.assertAlmostEqual(pos_order.amount_tax, 13.5, places=2)
        self.assertAlmostEqual(pos_order.amount_paid, 103.5, places=2)
        self.assertEqual(order.create_pos_order(), pos_order)
        self.assertEqual(len(pos_order.payment_ids), 1)

    def test_restaurant_fee_keeps_product_taxes(self):
        self.assertTrue(self.fee_product, "The fee fixture must be a real product")
        self.assertEqual(self.store.delivery_fee_product_id, self.fee_product)
        order = self._order(delivery_fee=11.5, vat_amount=15, amount_total=115)
        self.assertEqual(order.store_binding_id, self.store)
        pos_order = order.create_pos_order()
        fee_line = pos_order.lines.filtered(lambda line: line.product_id == self.fee_product)
        self.assertEqual(fee_line.tax_ids, self.tax)
        pos_order._compute_prices()
        self.assertAlmostEqual(pos_order.amount_total, 115, places=2)
        self.assertAlmostEqual(pos_order.amount_tax, 15, places=2)
        self.assertAlmostEqual(pos_order.amount_paid, 115, places=2)

    def test_large_difference_is_not_hidden_in_adjustment_product(self):
        self.assertTrue(self.adjustment_product, "The adjustment fixture must exist")
        self.assertEqual(self.store.adjustment_product_id, self.adjustment_product)
        order = self._order(amount_total=113.5)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.create_pos_order()
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.binding_id.pos_order_id)

    def test_inconsistent_header_tax_blocks_pos_creation(self):
        order = self._order(vat_amount=20)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.create_pos_order()
        self.assertFalse(order.pos_order_id)

    def test_tax_rounding_halalas_are_accepted_with_same_total(self):
        """Platforms round VAT on the order, Odoo per line: halalas apart is fine."""
        self.assertEqual(self.connection.tax_rounding_tolerance, 5.0)
        order = self._order(vat_amount=13.52)
        pos_order = order.create_pos_order()
        pos_order._compute_prices()
        self.assertAlmostEqual(pos_order.amount_total, 103.5, places=2)
        self.assertAlmostEqual(order.vat_amount, pos_order.amount_tax, places=2)
        self.assertAlmostEqual(order.vat_amount, 13.5, places=2)
        self.assertTrue(any(
            "Tax rounding difference accepted" in str(body or "")
            for body in order.message_ids.mapped("body")
        ))

    def test_hunger_station_two_item_rounding_case(self):
        """The real case: 9.00 + 2 x 28.00 = 65.00, platform VAT 8.47 vs Odoo."""
        second = self.env["product.product"].create({
            "name": "Delivery burger",
            "available_in_pos": True,
            "list_price": 28,
            "taxes_id": [Command.set(self.tax.ids)],
            "property_account_income_id": self.sales_account.id,
        })
        order = self._order(
            items_subtotal=56.53, vat_amount=8.47, amount_total=65.0,
            line_ids=[
                Command.create({
                    "name": "Water", "product_id": self.product.id, "quantity": 1,
                    "price_unit": 9.0, "price_subtotal": 7.83, "vat_amount": 1.17,
                }),
                Command.create({
                    "name": "Burger", "product_id": second.id, "quantity": 2,
                    "price_unit": 28.0, "price_subtotal": 48.70, "vat_amount": 7.30,
                }),
            ],
        )
        pos_order = order.create_pos_order()
        pos_order._compute_prices()
        self.assertAlmostEqual(pos_order.amount_total, 65.0, places=2)
        self.assertAlmostEqual(order.vat_amount, pos_order.amount_tax, places=2)

    def test_five_riyal_tolerance_accepts_tax_rate_difference_with_same_total(self):
        """With 5 SAR, a 10% vs 15% rate on a 103.50 order (4.09) is accepted."""
        self.tax.amount = 10
        order = self._order()
        pos_order = order.create_pos_order()
        pos_order._compute_prices()
        self.assertAlmostEqual(pos_order.amount_total, 103.5, places=2)
        self.assertAlmostEqual(order.vat_amount, pos_order.amount_tax, places=2)

    def test_tax_difference_above_tolerance_still_blocks(self):
        self.connection.tax_rounding_tolerance = 0.0
        order = self._order(vat_amount=13.52)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.create_pos_order()
        self.assertFalse(order.pos_order_id)
        self.assertAlmostEqual(order.vat_amount, 13.52, places=2)

    def test_recomputed_total_must_match_before_payment(self):
        self.connection.tax_rounding_tolerance = 0.05
        self.tax.amount = 10
        order = self._order()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.create_pos_order()
        self.assertFalse(order.pos_order_id)

    def test_local_acceptance_cannot_hide_financial_failure(self):
        order = self._order(amount_total=113.5)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.transition_to("accepted", source="manual")
        self.assertEqual(order.state, "received")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.transition_ids)

    def test_automatic_acceptance_cannot_hide_financial_failure(self):
        order = self._order(amount_total=113.5)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            order.transition_to("accepted", source="automatic")
        self.assertEqual(order.state, "received")
        self.assertFalse(order.pos_order_id)
        self.assertFalse(order.transition_ids)

    def test_delivered_prepaid_order_completes_native_pos_workflow(self):
        order = self._order()
        for state in ("accepted", "preparing", "ready", "picked_up", "delivered"):
            order.transition_to(state, source="webhook", idempotency_key=state)
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertAlmostEqual(order.pos_order_id.amount_paid, 103.5, places=2)
        self.assertTrue(order.pos_order_id.picking_ids)

    def test_uncollected_cash_is_paid_and_invoiced_at_acceptance(self):
        order = self._order(is_prepaid=False)
        for state in ("accepted", "preparing", "ready", "picked_up", "delivered"):
            order.transition_to(state, source="webhook", idempotency_key=state)
        self.assertEqual(order.state, "delivered")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_order_id.account_move.state, "posted")
        self.assertEqual(order._pos_board_values().get("pos_processing_state"), "done")
        self.assertEqual(len(order.pos_order_id.payment_ids), 1)
        self.assertEqual(order.pos_order_id.payment_ids.payment_method_id, self.cash_pm1)

    def test_cancellation_before_acceptance_keeps_no_pos_payment(self):
        order = self._order()
        order.transition_to("cancelled", source="webhook")
        self.assertEqual(order.state, "cancelled")
        self.assertFalse(order.pos_order_id)

    def test_cancellation_refunds_paid_pos_order_once(self):
        order = self._order()
        order.transition_to("accepted", source="webhook")
        original = order.pos_order_id
        order.transition_to("cancelled", source="webhook", idempotency_key="cancel-once")
        order.transition_to("cancelled", source="webhook", idempotency_key="cancel-once")
        refunds = self.env["pos.order"].search(
            [("lines.refunded_orderline_id", "in", original.lines.ids)],
        )
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds.state, "done")
        self.assertEqual(refunds.account_move.state, "posted")
        self.assertAlmostEqual(refunds.amount_total, -103.5, places=2)
        self.assertAlmostEqual(refunds.amount_paid, -103.5, places=2)
        self.assertEqual(len(refunds.payment_ids), 1)

    def test_invoiced_cancellation_preserves_captured_terminal_payment(self):
        order = self._order()
        order.transition_to("accepted", source="webhook")
        original = order.pos_order_id
        payment = original.payment_ids
        payment.transaction_id = "CAPTURED-REFERENCE"
        order.transition_to("cancelled", source="webhook")
        self.assertEqual(order.state, "cancelled")
        self.assertEqual(original.state, "done")
        self.assertTrue(payment.exists())
        self.assertAlmostEqual(original.amount_paid, 103.5, places=2)
        self.assertEqual(order._pos_board_values().get("pos_processing_state"), "blocked")

    def test_paid_cancellation_does_not_fake_terminal_refund(self):
        order = self._order()
        order.transition_to("accepted", source="webhook")
        original = order.pos_order_id
        original.payment_ids.transaction_id = "CAPTURED-REFERENCE"
        order.transition_to("cancelled", source="webhook")
        self.assertEqual(original.state, "done")
        self.assertFalse(self.env["pos.order"].search([
            ("lines.refunded_orderline_id", "in", original.lines.ids),
        ]))
        self.assertEqual(order._pos_board_values().get("pos_processing_state"), "blocked")

    def test_manager_retry_does_not_duplicate_completed_stock(self):
        order = self._order(is_prepaid=False)
        for state in ("accepted", "preparing", "ready", "picked_up", "delivered"):
            order.transition_to(state, source="webhook", idempotency_key=state)
        self.assertEqual(order.pos_processing_state, "done")
        manager = new_test_user(
            self.env, login="delivery_retry_manager",
            groups="bs_pos_delivery_core.group_delivery_manager",
            company_id=self.company.id,
        )
        managed = self.env["bs.delivery.order"].with_user(manager).browse(order.id)
        managed.action_retry_pos_processing()
        pickings = order.pos_order_id.picking_ids
        managed.action_retry_pos_processing()
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertEqual(order.pos_processing_state, "done")
        self.assertFalse(order.pos_processing_error)
        self.assertTrue(pickings)
        self.assertEqual(order.pos_order_id.picking_ids, pickings)

    def test_operator_cannot_retry_or_forge_processing_result(self):
        order = self._order()
        operator = new_test_user(
            self.env, login="delivery_retry_operator",
            groups="bs_pos_delivery_core.group_delivery_operator",
            company_id=self.company.id,
        )
        operated = self.env["bs.delivery.order"].with_user(operator).browse(order.id)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            operated.action_retry_pos_processing()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            operated.write({"pos_processing_state": "done"})

    def test_existing_draft_refund_requires_review(self):
        order = self._order()
        order.transition_to("accepted", source="webhook")
        original = order.pos_order_id
        original._process_saved_order(False)
        draft_refund = original._refund()
        order.transition_to("cancelled", source="webhook")
        self.assertEqual(order.pos_processing_state, "blocked")
        self.assertEqual(draft_refund.state, "draft")
        self.assertFalse(order.pos_refund_order_id)
        self.assertEqual(self.env["pos.order"].search([
            ("lines.refunded_orderline_id", "in", original.lines.ids),
        ]), draft_refund)

    def test_cancellation_preserves_linked_online_account_payment(self):
        if "online_account_payment_id" not in self.env["pos.payment"]._fields:
            self.skipTest("pos_online_payment is not installed")
        order = self._order()
        order.create_pos_order()
        original = order.pos_order_id
        accounting_payment = self.env["account.payment"].create({
            "payment_type": "inbound", "partner_type": "customer",
            "partner_id": self.partner_a.id,
            "journal_id": self.bank_pm1.journal_id.id,
            "currency_id": self.company.currency_id.id,
            "amount": 103.5,
        })
        original.payment_ids.online_account_payment_id = accounting_payment
        payment = original.payment_ids
        order.transition_to("cancelled", source="webhook")
        self.assertEqual(order.pos_processing_state, "blocked")
        self.assertEqual(original.state, "draft")
        self.assertTrue(payment.exists())
        self.assertEqual(payment.online_account_payment_id, accounting_payment)

    def test_delivered_invoiced_order_survives_session_closing(self):
        order = self._order()
        for state in ("accepted", "preparing", "ready", "picked_up"):
            order.transition_to(state, source="webhook", idempotency_key=state)
        order.pos_order_id.session_id.state = "closing_control"
        order.transition_to("delivered", source="webhook")
        self.assertEqual(order.pos_order_id.state, "done")
        self.assertTrue(order.pos_order_id.picking_ids)
        self.assertEqual(order.pos_processing_state, "done")

    def test_refund_waits_while_session_is_closing(self):
        order = self._order()
        order.transition_to("accepted", source="webhook")
        order.pos_order_id._process_saved_order(False)
        order.pos_order_id.session_id.state = "closing_control"
        order.transition_to("cancelled", source="webhook")
        self.assertFalse(order.pos_refund_order_id)
        self.assertEqual(order.pos_processing_state, "blocked")
