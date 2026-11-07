# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_pos_session_handover import TestPosSessionHandover


@tagged("post_install", "-at_install")
class TestPosOpenerCloser(TestPosSessionHandover):
    """Foodics-like opener / closer on a floor shared by two POS."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cash_journal_b = cls.env["account.journal"].create({
            "name": "POS B Cash Journal",
            "code": "PBCJ",
            "type": "cash",
            "company_id": cls.company.id,
        })
        cls.cash_payment_method_b = cls.env["pos.payment.method"].create({
            "name": "POS B Cash",
            "journal_id": cls.cash_journal_b.id,
            "receivable_account_id": cls.receivable_account.id,
            "company_id": cls.company.id,
        })
        cls.pos_config_b = cls.env["pos.config"].create({
            "name": "POS B Shared Floor",
            "company_id": cls.company.id,
            "module_pos_restaurant": True,
            "module_pos_hr": True,
            "journal_id": cls.sale_journal.id,
            "invoice_journal_id": cls.sale_journal.id,
            "payment_method_ids": [Command.link(cls.cash_payment_method_b.id)],
        })
        cls.pos_config_b.write({
            "basic_employee_ids": [Command.link(cls.new_cashier.id)],
        })
        cls.floor.write({"pos_config_ids": [Command.link(cls.pos_config_b.id)]})

    def _open_session_on(self, config, cashier):
        session = self.env["pos.session"].create({
            "config_id": config.id,
            "user_id": self.env.uid,
            "employee_id": cashier.id,
        })
        session.set_opening_control(0, None)
        return session

    def _sync_as(self, session, cashier, order, state="draft", payments=None):
        """Mimic what the POS UI of ``session`` sends for ``order``.

        Like Odoo 19 does for orders received through live sync, the UI sends
        its own session_id / config_id, whoever owns the order.
        """
        payments = payments or []
        payload = {
            "uuid": order.uuid,
            "name": order.name,
            "session_id": session.id,
            "config_id": session.config_id.id,
            "state": state,
            "company_id": self.company.id,
            "pricelist_id": order.pricelist_id.id,
            "table_id": order.table_id.id,
            "amount_tax": 0.0,
            "amount_total": 10.0,
            "amount_paid": sum(p[2]["amount"] for p in payments),
            "amount_return": 0.0,
            "employee_id": cashier.id,
            "last_order_preparation_change": "{}",
            "lines": [],
            "payment_ids": payments,
            # what a POS that does not know the opener session sends back
            "pino_opened_session_id": False,
            "pino_opened_config_id": False,
            "pino_handover_state": False,
            "pino_bill_printed": False,
            "pino_owner_session_id": session.id,
        }
        self.env["pos.order"].with_context(
            pino_current_session_id=session.id,
            pino_current_employee_id=cashier.id,
        ).sync_from_ui([payload])
        order.invalidate_recordset()
        return order

    def _cash(self, method, amount=10.0):
        return (0, 0, {
            "amount": amount,
            "name": fields.Datetime.now(),
            "payment_method_id": method.id,
        })

    # ------------------------------------------------------------------

    def test_opener_is_stamped_on_create(self):
        session_a = self._open_session(self.old_cashier)
        order = self._create_table_order(session_a)
        self.assertEqual(order.pino_opened_session_id, session_a)
        self.assertEqual(order.pino_opened_config_id, self.pos_config)
        self.assertEqual(order.pino_opened_employee_id, self.old_cashier)
        self.assertEqual(order.pino_opened_by_name, "Old Cashier")
        self.assertEqual(order.pino_owner_session_id, session_a.id)

    def test_other_cashier_editing_keeps_opener_as_owner(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order = self._create_table_order(session_a)

        self._sync_as(session_b, self.new_cashier, order)

        self.assertEqual(order.session_id, session_a)
        self.assertEqual(order.config_id, self.pos_config)
        self.assertEqual(order.employee_id, self.old_cashier)
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.pino_owner_session_id, session_a.id)
        self.assertEqual(order.pino_opened_session_id, session_a)
        self.assertEqual(order.pino_handover_state, "none")

    def test_cashier_without_own_tables_can_close_while_colleague_has_tables(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order_b = self._create_table_order(session_b)

        session_a.action_pos_session_closing_control()

        self.assertEqual(session_a.state, "closed")
        self.assertEqual(order_b.session_id, session_b)
        self.assertEqual(order_b.state, "draft")

    def test_payment_by_other_cashier_moves_order_to_closer(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order = self._create_table_order(session_a)

        self._sync_as(
            session_b, self.new_cashier, order,
            state="paid", payments=[self._cash(self.cash_payment_method_b)],
        )

        self.assertEqual(order.state, "paid")
        self.assertEqual(order.session_id, session_b)
        self.assertEqual(order.config_id, self.pos_config_b)
        self.assertEqual(order.employee_id, self.new_cashier)
        self.assertEqual(order.payment_ids.session_id, session_b)
        # opener kept, closer stamped
        self.assertEqual(order.pino_opened_session_id, session_a)
        self.assertEqual(order.pino_opened_config_id, self.pos_config)
        self.assertEqual(order.pino_opened_employee_id, self.old_cashier)
        self.assertEqual(order.pino_handover_state, "none")
        self.assertEqual(order.pino_closed_session_id, session_b)
        self.assertEqual(order.pino_closed_employee_id, self.new_cashier)
        self.assertTrue(order.pino_closed_by_other)
        # the opener has nothing left and can close
        session_a.action_pos_session_closing_control()
        self.assertEqual(session_a.state, "closed")

    def test_payment_by_opener_stamps_same_closer(self):
        session_a = self._open_session(self.old_cashier)
        order = self._create_table_order(session_a)

        self._sync_as(
            session_a, self.old_cashier, order,
            state="paid", payments=[self._cash(self.cash_payment_method)],
        )

        self.assertEqual(order.state, "paid")
        self.assertEqual(order.pino_closed_session_id, session_a)
        self.assertEqual(order.pino_closed_employee_id, self.old_cashier)
        self.assertFalse(order.pino_closed_by_other)

    def test_partial_payment_of_opener_blocks_other_cashier(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order = self._create_table_order(
            session_a, payments=[self._cash(self.cash_payment_method, 4.0)],
        )

        with self.assertRaises(UserError):
            self._sync_as(
                session_b, self.new_cashier, order,
                state="paid", payments=[self._cash(self.cash_payment_method_b, 6.0)],
            )

    def test_pending_table_of_closed_session_is_taken_by_cashier_serving_it(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order = self._create_table_order(session_a)
        session_a.pino_prepare_table_handover(source="closing")
        session_a.action_pos_session_closing_control()

        self._sync_as(session_b, self.new_cashier, order)

        self.assertEqual(order.session_id, session_b)
        self.assertEqual(order.employee_id, self.new_cashier)
        self.assertEqual(order.pino_handover_state, "done")
        self.assertEqual(order.pino_handover_to_session_id, session_b)
        self.assertEqual(order.pino_opened_session_id, session_a)

    def test_handover_targets_and_direct_handover_to_open_colleague(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order = self._create_table_order(session_a)

        targets = session_a.pino_get_handover_targets()
        self.assertEqual([t["id"] for t in targets], [session_b.id])
        self.assertEqual(targets[0]["cashier_name"], "New Cashier")

        result = session_a.pino_prepare_table_handover(
            order.ids, source="closing", target_session_id=session_b.id,
        )
        order.invalidate_recordset()

        self.assertEqual(result["target_session_id"], session_b.id)
        self.assertEqual(order.session_id, session_b)
        self.assertEqual(order.employee_id, self.new_cashier)
        self.assertEqual(order.pino_handover_state, "done")
        self.assertEqual(order.pino_handover_from_session_id, session_a)
        self.assertEqual(order.pino_handover_to_session_id, session_b)
        self.assertEqual(order.pino_new_session_cashier_id, self.new_cashier)
        self.assertEqual(order.pino_opened_employee_id, self.old_cashier)

        session_a.action_pos_session_closing_control()
        self.assertEqual(session_a.state, "closed")

    def test_handover_to_session_outside_floor_is_refused(self):
        session_a = self._open_session(self.old_cashier)
        order = self._create_table_order(session_a)
        with self.assertRaises(UserError):
            session_a.pino_prepare_table_handover(
                order.ids, source="manual", target_session_id=session_a.id + 999,
            )

    # ------------------------------------------------------------------
    # Bill printed before payment
    # ------------------------------------------------------------------

    def test_bill_printed_records_printer_and_survives_ui_sync(self):
        session_a = self._open_session(self.old_cashier)
        session_b = self._open_session_on(self.pos_config_b, self.new_cashier)
        order = self._create_table_order(session_a)

        result = order.pino_mark_bill_printed(session_b.id, self.new_cashier.id)
        order.invalidate_recordset()
        self.assertEqual(result[0]["pino_bill_print_count"], 1)
        self.assertTrue(order.pino_bill_printed)
        self.assertEqual(order.pino_bill_printed_by_name, "New Cashier")
        self.assertEqual(order.pino_bill_printed_employee_id, self.new_cashier)
        self.assertEqual(order.pino_bill_printed_session_id, session_b)
        self.assertTrue(order.pino_bill_printed_at)
        # printing the bill does not change the owner
        self.assertEqual(order.session_id, session_a)

        # a POS sending stale values must not reset the printed flag
        self._sync_as(session_a, self.old_cashier, order)
        self.assertTrue(order.pino_bill_printed)

        # reprint by the opener
        order.pino_mark_bill_printed(session_a.id, self.old_cashier.id)
        order.invalidate_recordset()
        self.assertEqual(order.pino_bill_print_count, 2)
        self.assertEqual(order.pino_bill_printed_by_name, "Old Cashier")

    def test_bill_printed_ignored_on_paid_order_and_payment_still_allowed(self):
        session_a = self._open_session(self.old_cashier)
        order = self._create_table_order(session_a)
        order.pino_mark_bill_printed(session_a.id, self.old_cashier.id)

        self._sync_as(
            session_a, self.old_cashier, order,
            state="paid", payments=[self._cash(self.cash_payment_method)],
        )
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.pino_bill_printed_by_name, "Old Cashier")

        self.assertEqual(order.pino_mark_bill_printed(session_a.id, self.old_cashier.id), [])
        self.assertEqual(order.pino_bill_print_count, 1)

    # ------------------------------------------------------------------
    # Guest stay duration
    # ------------------------------------------------------------------

    def test_guest_stay_is_stored_when_table_is_paid(self):
        session_a = self._open_session(self.old_cashier)
        order = self._create_table_order(session_a)
        self.assertEqual(order.pino_stay_duration, 0.0)

        opened = fields.Datetime.now() - timedelta(minutes=95)
        order.write({"pino_opened_at": opened})
        self._sync_as(
            session_a, self.old_cashier, order,
            state="paid", payments=[self._cash(self.cash_payment_method)],
        )

        self.assertEqual(order.state, "paid")
        self.assertAlmostEqual(order.pino_stay_minutes, 95, delta=1)
        self.assertAlmostEqual(order.pino_stay_duration, 95 / 60.0, delta=0.02)

    def test_split_bill_keeps_service_start_of_original_table_order(self):
        session_a = self._open_session(self.old_cashier)
        original = self._create_table_order(session_a)
        opened = fields.Datetime.now() - timedelta(minutes=40)
        original.write({"pino_opened_at": opened})

        uuid = "pino-split-test-uuid"
        payload = {
            "uuid": uuid,
            "name": "Split",
            "session_id": session_a.id,
            "state": "draft",
            "company_id": self.company.id,
            "pricelist_id": original.pricelist_id.id,
            "floating_order_name": "Split 1",
            "amount_tax": 0.0,
            "amount_total": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "last_order_preparation_change": "{}",
            "lines": [],
            "payment_ids": [],
            "pino_split_from_uuid": original.uuid,
            "pino_opened_at": False,
        }
        self.env["pos.order"].with_context(
            pino_current_session_id=session_a.id,
            pino_current_employee_id=self.old_cashier.id,
        ).sync_from_ui([payload])
        split = self.env["pos.order"].search([("uuid", "=", uuid)])

        self.assertEqual(split.pino_split_from_uuid, original.uuid)
        self.assertEqual(split.pino_opened_at, opened)
        self.assertEqual(split.pino_opened_employee_id, self.old_cashier)
        self.assertNotEqual(split, original)
