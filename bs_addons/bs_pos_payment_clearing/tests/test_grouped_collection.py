from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_payment_clearing import PosClearingCommon


@tagged("post_install", "-at_install")
class TestGroupedCollection(PosClearingCommon):
    def _assert_group(self, session, components=2):
        move = session.bs_collection_move_id
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.journal_id, session.bs_clearing_journal_id)
        self.assertFalse(move.origin_payment_id | move.payment_ids)
        self.assertFalse(move.statement_line_id | move.statement_line_ids)
        self.assertFalse(session.bank_payment_ids)
        self.assertNotEqual(move, session.move_id)
        self.assertEqual(len(session.bs_collection_component_ids), components)
        self.assertEqual(len(move.bs_pos_settlement_ids), components)
        self.assertAlmostEqual(sum(move.line_ids.mapped("balance")), 0)
        for draft in move.bs_pos_settlement_ids:
            self.assertEqual(draft.state, "draft")
            self.assertEqual(draft.bs_pos_source_move_id, move)
            self.assertEqual(draft.journal_id, draft.bs_pos_source_line_id.bs_pos_component_id.journal_id)
            self.assertTrue(draft._bs_settlement_lines_match(draft._bs_expected_settlement_lines()))
        self.assertIn(move, session._get_related_account_moves())
        return move

    def test_one_posted_two_drafts(self):
        self._order(440, [(self.bank_payment_method, 300), (self.cash_payment_method, 140)])
        session = self._close()
        move = self._assert_group(session)
        self.assertEqual(len(move.line_ids), 4)
        self.assertEqual(self._balance(move, self.cash_outstanding), 140)
        self.assertEqual(self._balance(move, self.bank_outstanding), 300)
        self.assertEqual(session.cash_register_balance_end_real, 140)
        self.assertEqual(session.move_id.state, "posted")
        self.assertFalse(session.statement_line_ids)
        drafts = move.bs_pos_settlement_ids
        bank = drafts.filtered(lambda draft: draft.journal_id == self.bank_payment_method.journal_id)
        cash = drafts - bank
        bank.action_post()
        self.assertTrue(bank.bs_pos_source_line_id.reconciled)
        self.assertFalse(cash.bs_pos_source_line_id.reconciled)
        self.assertEqual(cash.state, "draft")
        cash.action_post()
        self.assertTrue(cash.bs_pos_source_line_id.reconciled)
        self.assertEqual(self._balance(drafts, self.bank_payment_method.journal_id.default_account_id), 300)
        self.assertEqual(self._balance(drafts, self.cash_payment_method.journal_id.default_account_id), 140)

    def test_closing_entries_use_session_opening_date(self):
        session = self._open()
        session.start_at = fields.Datetime.now() - timedelta(days=2)
        opening_date = fields.Date.context_today(session, timestamp=session.start_at)
        self.assertNotEqual(opening_date, fields.Date.context_today(session))
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])

        session = self._close()
        collection = self._assert_group(session)
        self.assertEqual(session.move_id.date, opening_date)
        self.assertEqual(collection.date, opening_date)
        self.assertEqual(set(collection.bs_pos_settlement_ids.mapped("date")), {opening_date})

    def test_shared_outstanding_and_split_customers(self):
        self.bank_payment_method.outstanding_account_id = self.cash_outstanding
        (self.cash_payment_method | self.bank_payment_method).write({
            "split_transactions": True, "partner_id": self.partner_mobt.id,
        })
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        move = self._assert_group(self._close())
        first, second = move.bs_pos_settlement_ids
        first.action_post()
        self.assertTrue(first.bs_pos_source_line_id.reconciled)
        self.assertFalse(second.bs_pos_source_line_id.reconciled)
        self.assertEqual(move.line_ids.partner_id, self.partner_mobt)
        second.action_post()
        self.assertNotEqual(first.bs_pos_source_line_id.full_reconcile_id, second.bs_pos_source_line_id.full_reconcile_id)

    def test_refunds(self):
        self._order(-20, [(self.cash_payment_method, -10), (self.bank_payment_method, -10)])
        move = self._assert_group(self._close())
        move.bs_pos_settlement_ids.action_post()
        self.assertEqual(self._balance(move, self.cash_outstanding), -10)
        self.assertEqual(self._balance(move.bs_pos_settlement_ids, self.cash_payment_method.journal_id.default_account_id), -10)

    def test_foreign_currency(self):
        self._set_euro_rates()
        self.config.journal_id = self.pos_config_eur.journal_id
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        move = self._assert_group(self._close())
        self.assertEqual(self._balance(move, self.cash_outstanding), 5)
        move.bs_pos_settlement_ids.action_post()
        self.assertTrue(all(move.bs_pos_settlement_ids.bs_pos_source_line_id.mapped("reconciled")))

    def test_invoiced_order(self):
        order = self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)], invoice=True)
        if not order.is_invoiced:
            order.with_context(generate_pdf=False)._generate_pos_order_invoice()
        move = self._assert_group(self._close())
        self.assertEqual(order.account_move.amount_residual, 0)
        self.assertEqual(order.account_move.payment_state, "paid")
        move.bs_pos_settlement_ids.action_post()
        self.assertEqual(order.account_move.payment_state, "paid")

    def test_bank_and_cash_differences(self):
        self.bank_payment_method.journal_id.loss_account_id = self.company_data["default_account_expense"]
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self.config.current_session_id
        session.post_closing_cash_details(8)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])
        move = self._assert_group(session)
        self.assertEqual(self._balance(move, self.bank_outstanding), 8)
        self.assertEqual(self._balance(move, self.cash_outstanding), 8)
        self.assertFalse(session.statement_line_ids)
        move.bs_pos_settlement_ids.action_post()

    def test_split_bank_difference(self):
        self.bank_payment_method.write({
            "split_transactions": True, "partner_id": self.partner_mobt.id,
        })
        self.bank_payment_method.journal_id.loss_account_id = self.company_data["default_account_expense"]
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self.config.current_session_id
        session.post_closing_cash_details(10)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])
        move = self._assert_group(session, components=3)
        self.assertEqual(self._balance(move, self.bank_outstanding), 8)
        move.bs_pos_settlement_ids.action_post()

    def test_cashier_close_and_read_only_components(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self.config.current_session_id
        session.post_closing_cash_details(10)
        session.with_user(self.cashier).close_session_from_ui()
        move = self._assert_group(session)
        session.bs_collection_component_ids.with_user(self.cashier).check_access("read")
        with self.assertRaises(AccessError), self.cr.savepoint():
            move.bs_pos_settlement_ids.with_user(self.cashier).action_post()

    def test_no_duplicate_close_or_sync(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self._close()
        move = self._assert_group(session)
        drafts = move.bs_pos_settlement_ids
        move._bs_sync_pos_settlements()
        self.assertEqual(drafts, move.bs_pos_settlement_ids)
        with self.assertRaises(UserError), self.cr.savepoint():
            session.action_pos_session_close()
        self.assertEqual(session.bs_collection_move_id, move)

    def test_finalized_source_protected(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self._close()
        move = self._assert_group(session)
        move.button_draft()
        self.assertEqual(move.state, "draft")
        with self.assertRaises(UserError), self.cr.savepoint():
            move.write({"line_ids": [Command.update(line.id, {"balance": line.balance * 2}) for line in move.line_ids]})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            move.bs_pos_collection_ready = False

    def test_collection_reset_and_repost_keeps_links_and_matching(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)], invoice=True)
        session = self._close()
        move = self._assert_group(session)
        settlements = move.bs_pos_settlement_ids
        components = session.bs_collection_component_ids
        receivable = move.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        matches = receivable.matched_debit_ids | receivable.matched_credit_ids
        self.assertTrue(matches)

        move.button_draft()
        self.assertEqual(move.state, "draft")
        self.assertEqual(session.state, "closed")
        self.assertEqual(session.move_id.state, "posted")
        self.assertEqual(session.bs_collection_move_id, move)
        with self.assertRaisesRegex(UserError, "must be posted before its settlement"), self.cr.savepoint():
            settlements.action_post()

        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.bs_pos_settlement_ids, settlements)
        self.assertEqual(session.bs_collection_component_ids, components)
        self.assertEqual(receivable.matched_debit_ids | receivable.matched_credit_ids, matches)
        self.assertTrue(all(receivable.mapped("reconciled")))
        settlements.action_post()
        self.assertTrue(all(settlements.bs_pos_source_line_id.mapped("reconciled")))

    def test_collection_reset_requires_posted_settlements_reset_first(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        move = self._assert_group(self._close())
        settlement = move.bs_pos_settlement_ids[:1]
        settlement.action_post()
        with self.assertRaisesRegex(UserError, "Reset the linked POS settlement"), self.cr.savepoint():
            move.button_draft()
        self.assertEqual(move.state, "posted")
        self.assertTrue(settlement.bs_pos_source_line_id.reconciled)
        settlement.button_draft()
        move.button_draft()
        move.action_post()
        settlement.action_post()
        self.assertTrue(settlement.bs_pos_source_line_id.reconciled)

    def test_cashier_cannot_reset_collection(self):
        self._order(10)
        move = self._assert_group(self._close(), components=1)
        with self.assertRaises(AccessError), self.cr.savepoint():
            move.with_user(self.cashier).button_draft()
        self.assertEqual(move.state, "posted")

    def test_cannot_force_finalize_or_change_legacy_mode(self):
        session = self._open()
        move = session._bs_get_collection_move()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            move.bs_pos_collection_ready = True
        with self.assertRaises(UserError), self.cr.savepoint():
            move.action_post()
        session._write({"bs_group_payment_entries": False})
        session.invalidate_recordset(["bs_group_payment_entries"])
        with self.assertRaises(UserError), self.cr.savepoint():
            session.bs_group_payment_entries = True

    def test_cannot_add_items_after_finalize(self):
        self._order(10)
        move = self._assert_group(self._close(), components=1)
        with self.assertRaises(UserError), self.cr.savepoint():
            move.write({"line_ids": [
                Command.create({"account_id": self.cash_outstanding.id, "balance": 5}),
                Command.create({"account_id": self.cash_payment_method.receivable_account_id.id, "balance": -5}),
            ]})

    def test_settlement_reset_keeps_other_matching(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        move = self._assert_group(self._close())
        first, second = move.bs_pos_settlement_ids
        move.bs_pos_settlement_ids.action_post()
        first.button_draft()
        self.assertFalse(first.bs_pos_source_line_id.reconciled)
        self.assertTrue(second.bs_pos_source_line_id.reconciled)
        first.action_post()
        self.assertTrue(first.bs_pos_source_line_id.reconciled)

    def test_no_cash_control(self):
        self.config.cash_control = False
        self._order(10)
        session = self._close()
        self._assert_group(session, components=1)
        self.assertFalse(session.statement_line_ids)

    def test_cash_in_out_count_and_optional_transfer(self):
        if "pcm_auto_transfer" not in self.config._fields:
            self.skipTest("Optional pos_cash_management is not installed")
        self.config.pcm_auto_transfer = True
        session = self._open(100)
        session.try_cash_in_out("in", 20, "Float", False, {"translatedType": "Cash In"})
        session.try_cash_in_out("out", 5, "Petty cash", False, {"translatedType": "Cash Out"})
        self._order(115)
        session = self._close(counted=220)
        group = self._assert_group(session, components=1)
        other = session.statement_line_ids.move_id
        self.assertNotIn(group, other)
        self.assertEqual(self._balance(group, self.cash_outstanding), 115)
        (group.bs_pos_settlement_ids | other.bs_pos_settlement_ids).action_post()

    def test_foreign_bank_difference(self):
        self._set_euro_rates()
        self.config.journal_id = self.pos_config_eur.journal_id
        self.bank_payment_method.journal_id.loss_account_id = self.company_data["default_account_expense"]
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self.config.current_session_id
        session.post_closing_cash_details(10)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])
        move = self._assert_group(session)
        bank = move.bs_pos_settlement_ids.filtered(lambda draft: draft.journal_id == self.bank_payment_method.journal_id)
        self.assertEqual(bank.bs_pos_source_line_id.amount_currency, 8)
        self.assertEqual(bank.bs_pos_source_line_id.balance, 4)
        move.bs_pos_settlement_ids.action_post()

    def test_separate_sessions_and_journals(self):
        self._order(10, [(self.bank_payment_method, 10)])
        first = self._assert_group(self._close(), components=1)
        self.config = self.pos_config_eur
        self._order(10, [(self.bank_payment_method, 10)])
        second = self._assert_group(self._close(), components=1)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.journal_id, second.journal_id)

    def test_pay_later_does_not_create_empty_collection(self):
        self.bank_payment_method.write({
            "split_transactions": True, "partner_id": self.partner_mobt.id,
        })
        self._order(10, [(self.credit_payment_method, 10)])
        session = self._close()
        self.assertFalse(session.bs_collection_move_id)
        self.assertFalse(session.bs_collection_component_ids)

    def test_draft_collection_cannot_receive_manual_items(self):
        session = self._open()
        group = session._bs_get_collection_move()
        with self.assertRaises(UserError), self.cr.savepoint():
            group.write({"line_ids": [
                Command.create({"account_id": self.cash_outstanding.id, "balance": 5}),
                Command.create({"account_id": self.cash_payment_method.receivable_account_id.id, "balance": -5}),
            ]})

    def test_group_links_and_reversal(self):
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self._close()
        group = self._assert_group(session)
        action = group.action_bs_open_pos_settlement()
        self.assertEqual(action["view_mode"], "list,form")
        self.assertEqual(session.action_bs_collection_details()["res_model"], "bs.pos.collection.component")
        reversal = group._reverse_moves(cancel=True)
        self.assertEqual(reversal.state, "posted")
        self.assertFalse(reversal.bs_pos_grouped_collection)
        self.assertFalse(reversal.line_ids.bs_pos_component_id)
        with self.assertRaises(UserError), self.cr.savepoint():
            group.bs_pos_settlement_ids.action_post()
