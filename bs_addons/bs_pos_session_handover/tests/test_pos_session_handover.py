# -*- coding: utf-8 -*-

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPosSessionHandover(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.receivable_account = cls.env["account.account"].search([
            ("account_type", "=", "asset_receivable"),
            ("company_ids", "in", cls.company.id),
        ], limit=1)
        if not cls.receivable_account:
            cls.receivable_account = cls.env["account.account"].create({
                "name": "POS Handover Receivable",
                "code": "XPHR",
                "account_type": "asset_receivable",
                "reconcile": True,
                "company_ids": [Command.link(cls.company.id)],
            })

        cls.sale_journal = cls.env["account.journal"].create({
            "name": "POS Handover Sale Journal",
            "code": "PHSJ",
            "type": "sale",
            "company_id": cls.company.id,
        })
        cls.cash_journal = cls.env["account.journal"].create({
            "name": "POS Handover Cash Journal",
            "code": "PHCJ",
            "type": "cash",
            "company_id": cls.company.id,
        })
        cls.cash_payment_method = cls.env["pos.payment.method"].create({
            "name": "POS Handover Cash",
            "journal_id": cls.cash_journal.id,
            "receivable_account_id": cls.receivable_account.id,
            "company_id": cls.company.id,
        })
        cls.product = cls.env["product.product"].create({
            "name": "Handover Product",
            "available_in_pos": True,
            "list_price": 10.0,
            "taxes_id": [Command.clear()],
        })
        cls.old_cashier = cls.env["hr.employee"].create({
            "name": "Old Cashier",
            "company_id": cls.company.id,
        })
        cls.new_cashier = cls.env["hr.employee"].create({
            "name": "New Cashier",
            "company_id": cls.company.id,
        })
        cls.pos_config = cls.env["pos.config"].create({
            "name": "POS Session Handover Test",
            "company_id": cls.company.id,
            "module_pos_restaurant": True,
            "module_pos_hr": True,
            "journal_id": cls.sale_journal.id,
            "invoice_journal_id": cls.sale_journal.id,
            "payment_method_ids": [Command.link(cls.cash_payment_method.id)],
        })
        cls.pos_config.write({
            "basic_employee_ids": [
                Command.link(cls.old_cashier.id),
                Command.link(cls.new_cashier.id),
            ],
        })
        cls.floor = cls.env["restaurant.floor"].create({
            "name": "Handover Test Floor",
            "pos_config_ids": [Command.link(cls.pos_config.id)],
        })
        cls.table = cls.env["restaurant.table"].create({
            "table_number": 7,
            "floor_id": cls.floor.id,
            "seats": 4,
            "position_h": 100,
            "position_v": 100,
        })
        cls.second_table = cls.env["restaurant.table"].create({
            "table_number": 8,
            "floor_id": cls.floor.id,
            "seats": 2,
            "position_h": 200,
            "position_v": 100,
        })

    def _open_session(self, cashier):
        session = self.env["pos.session"].create({
            "config_id": self.pos_config.id,
            "user_id": self.env.uid,
            "employee_id": cashier.id,
        })
        session.set_opening_control(0, None)
        return session

    def _create_table_order(self, session, payments=None, table=None):
        payments = payments or []
        table = table or self.table
        return self.env["pos.order"].create({
            "session_id": session.id,
            "company_id": self.company.id,
            "pricelist_id": session.config_id.pricelist_id.id,
            "table_id": table.id,
            "amount_tax": 0.0,
            "amount_total": 10.0,
            "amount_paid": sum(payment[2]["amount"] for payment in payments),
            "amount_return": 0.0,
            "employee_id": session.employee_id.id,
            "last_order_preparation_change": "{}",
            "lines": [(0, 0, {
                "name": "Handover Product",
                "product_id": self.product.id,
                "qty": 1.0,
                "price_unit": 10.0,
                "price_subtotal": 10.0,
                "price_subtotal_incl": 10.0,
                "tax_ids": [(6, 0, [])],
            })],
            "payment_ids": payments,
        })

    def test_prepare_handover_marks_order_and_allows_old_session_to_close(self):
        session = self._open_session(self.old_cashier)
        order = self._create_table_order(session)

        with self.assertRaises(UserError):
            session.action_pos_session_closing_control()

        result = session.pino_prepare_table_handover()
        order.invalidate_recordset()

        self.assertTrue(result["successful"])
        self.assertEqual(result["order_ids"], order.ids)
        self.assertTrue(order.pino_is_session_transferred)
        self.assertEqual(order.pino_handover_state, "pending")
        self.assertEqual(order.pino_handover_source, "backend_button")
        self.assertEqual(order.pino_handover_from_session_id, session)
        self.assertEqual(order.pino_old_session_cashier_id, self.old_cashier)
        self.assertFalse(order.pino_handover_to_session_id)
        self.assertFalse(order.pino_new_session_cashier_id)

        session.action_pos_session_closing_control()
        self.assertEqual(session.state, "closed")

    def test_new_session_claims_pending_orders_and_stamps_new_cashier(self):
        old_session = self._open_session(self.old_cashier)
        order = self._create_table_order(old_session)
        old_session.pino_prepare_table_handover(source="closing")
        old_session.action_pos_session_closing_control()

        new_session = self._open_session(self.new_cashier)
        opening_notification = new_session.pino_get_handover_opening_notification()
        repeated_notification = new_session.pino_get_handover_opening_notification()
        order.invalidate_recordset()

        self.assertEqual(order.session_id, new_session)
        self.assertEqual(order.pino_handover_state, "done")
        self.assertEqual(order.pino_handover_source, "closing")
        self.assertEqual(order.pino_handover_from_session_id, old_session)
        self.assertEqual(order.pino_handover_to_session_id, new_session)
        self.assertEqual(order.pino_old_session_cashier_id, self.old_cashier)
        self.assertEqual(order.pino_new_session_cashier_id, self.new_cashier)
        self.assertEqual(order.employee_id, self.new_cashier)
        self.assertEqual(opening_notification["count"], 1)
        self.assertEqual(opening_notification["order_ids"], order.ids)
        self.assertEqual(repeated_notification["count"], 0)

    def test_closing_handover_handles_multiple_table_orders(self):
        old_session = self._open_session(self.old_cashier)
        orders = self.env["pos.order"]
        for table in (self.table, self.second_table):
            orders |= self._create_table_order(old_session, table=table)
            orders |= self._create_table_order(old_session, table=table)

        result = old_session.pino_prepare_table_handover(source="closing")
        orders.invalidate_recordset()

        self.assertTrue(result["successful"])
        self.assertEqual(result["count"], 4)
        self.assertEqual(set(result["order_ids"]), set(orders.ids))
        self.assertTrue(all(order.pino_handover_state == "pending" for order in orders))

        old_session.action_pos_session_closing_control()
        new_session = self._open_session(self.new_cashier)
        opening_notification = new_session.pino_get_handover_opening_notification()
        orders.invalidate_recordset()

        self.assertEqual(opening_notification["count"], 4)
        self.assertEqual(set(opening_notification["order_ids"]), set(orders.ids))
        self.assertTrue(all(order.session_id == new_session for order in orders))
        self.assertTrue(all(order.pino_handover_state == "done" for order in orders))

    def test_paid_draft_table_order_is_not_transferred(self):
        session = self._open_session(self.old_cashier)
        order = self._create_table_order(session, payments=[(0, 0, {
            "amount": 1.0,
            "name": fields.Datetime.now(),
            "payment_method_id": self.cash_payment_method.id,
        })])

        with self.assertRaises(UserError):
            session.pino_prepare_table_handover(order.ids)

        order.invalidate_recordset()
        self.assertFalse(order.pino_is_session_transferred)
        self.assertEqual(order.pino_handover_state, "none")

    def test_non_table_draft_order_still_blocks_session_close(self):
        session = self._open_session(self.old_cashier)
        order = self._create_table_order(session)
        order.table_id = False

        result = session.pino_prepare_table_handover()

        self.assertTrue(result["successful"])
        self.assertFalse(result["order_ids"])
        with self.assertRaises(UserError):
            session.action_pos_session_closing_control()

    def test_prepare_handover_with_order_ids_only_marks_selected_orders(self):
        session = self._open_session(self.old_cashier)
        selected_order = self._create_table_order(session, table=self.table)
        other_order = self._create_table_order(session, table=self.second_table)

        result = session.pino_prepare_table_handover(selected_order.ids, source="manual")
        selected_order.invalidate_recordset()
        other_order.invalidate_recordset()

        self.assertTrue(result["successful"])
        self.assertEqual(result["source"], "manual")
        self.assertEqual(result["order_ids"], selected_order.ids)
        self.assertEqual(selected_order.pino_handover_state, "pending")
        self.assertEqual(selected_order.pino_handover_source, "manual")
        self.assertEqual(selected_order.pino_handover_from_session_id, session)
        self.assertFalse(other_order.pino_is_session_transferred)
        self.assertEqual(other_order.pino_handover_state, "none")

        with self.assertRaises(UserError):
            session.action_pos_session_closing_control()

    def test_bulk_session_write_only_stamps_pending_orders(self):
        old_session = self._open_session(self.old_cashier)
        pending_order = self._create_table_order(old_session)
        normal_order = self._create_table_order(old_session)
        normal_order.write({
            "table_id": False,
            "state": "cancel",
        })
        old_session.pino_prepare_table_handover(pending_order.ids)
        old_session.action_pos_session_closing_control()

        new_session = self.env["pos.session"].create({
            "config_id": self.pos_config.id,
            "user_id": self.env.uid,
            "employee_id": self.new_cashier.id,
        })
        (pending_order | normal_order).write({"session_id": new_session.id})

        self.assertEqual(pending_order.pino_handover_state, "done")
        self.assertEqual(pending_order.pino_handover_to_session_id, new_session)
        self.assertFalse(normal_order.pino_is_session_transferred)
        self.assertEqual(normal_order.pino_handover_state, "none")
