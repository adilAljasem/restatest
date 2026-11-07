from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_payment_clearing import PosClearingCommon


@tagged("post_install", "-at_install")
class TestNetCashCollection(PosClearingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.drawer = cls.cash_payment_method.journal_id.default_account_id
        cls.branch_cash = cls.drawer.copy({"name": "Branch Cash Box", "code": "BSBOX"})
        cls.cash_payment_method.bs_branch_cash_account_id = cls.branch_cash
        cls.cash_payment_method.journal_id.loss_account_id = cls.company_data["default_account_expense"]
        cls.cash_payment_method.journal_id.profit_account_id = cls.company_data["default_account_revenue"]

    def _cash_component(self, session):
        return session.bs_collection_component_ids.filtered(lambda component: component.payment_method_id == self.cash_payment_method)

    def test_grouped_cash_difference_uses_cashier_work_contact(self):
        if "pcm_difference_partner" not in self.config._fields:
            self.skipTest("POS Cash Management is not installed")
        self.config.pcm_difference_partner = True
        contact = self.env["res.partner"].create({"name": "Cashier Work Contact"})
        employee = self.env["hr.employee"].sudo().create({
            "name": "Cashier Employee", "work_contact_id": contact.id,
            "company_id": self.company.id,
        })
        self.bank_payment_method.journal_id.loss_account_id = self.company_data["default_account_expense"]
        self._order(10)
        self._order(10, [(self.bank_payment_method, 10)])
        session = self.config.current_session_id
        session.employee_id = employee
        session.post_closing_cash_details(8)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])

        self.assertEqual(session.state, "closed")
        self.assertFalse(session.statement_line_ids)
        cash_component = self._cash_component(session)
        cash_difference = cash_component.line_ids.filtered(
            lambda line: line.account_id == self.cash_payment_method.journal_id.loss_account_id,
        )
        self.assertEqual(len(cash_difference), 1)
        self.assertEqual(cash_difference.partner_id, contact)
        self.assertFalse((cash_component.line_ids - cash_difference).partner_id)
        bank_component = session.bs_collection_component_ids - cash_component
        bank_difference = bank_component.line_ids.filtered(
            lambda line: line.account_id == self.bank_payment_method.journal_id.loss_account_id,
        )
        self.assertEqual(len(bank_difference), 1)
        self.assertFalse(bank_difference.partner_id)

        self.config.pcm_difference_partner = False
        self._order(10)
        disabled_session = self.config.current_session_id
        disabled_session.employee_id = employee
        disabled_session = self._close(counted=8)
        disabled_difference = self._cash_component(disabled_session).line_ids.filtered(
            lambda line: line.account_id == self.cash_payment_method.journal_id.loss_account_id,
        )
        self.assertEqual(len(disabled_difference), 1)
        self.assertFalse(disabled_difference.partner_id)

    def test_requested_150_cash_70_card_10_loss(self):
        self._open(50)
        self._order(150)
        self._order(70, [(self.bank_payment_method, 70)])
        session = self._close(counted=190)
        move = session.bs_collection_move_id
        component = self._cash_component(session)
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.journal_id, self.config.journal_id)
        self.assertEqual(session.cash_register_difference, -10)
        self.assertEqual(component.payment_amount, 150)
        self.assertEqual(component.difference_amount, -10)
        self.assertEqual(component.amount, 140)
        self.assertEqual(self._balance(move, self.drawer), 140)
        self.assertEqual(self._balance(move, self.cash_payment_method.journal_id.loss_account_id), 10)
        self.assertEqual(self._balance(move, self.bank_outstanding), 70)
        self.assertEqual(len(move.line_ids), 5)
        self.assertFalse(session.statement_line_ids)
        receivable = component.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        self.assertEqual(receivable.credit, 150)
        self.assertTrue(receivable.reconciled)
        self.assertEqual(len(move.bs_pos_settlement_ids), 2)
        self.assertEqual(set(move.bs_pos_settlement_ids.mapped("state")), {"draft"})
        cash = component.settlement_ids
        self.assertEqual(cash.journal_id, self.cash_payment_method.journal_id)
        self.assertEqual(self._balance(cash, self.branch_cash), 140)
        self.assertEqual(self._balance(cash, self.drawer), -140)
        cash.action_post()
        self.assertTrue(cash.bs_pos_source_line_id.reconciled)
        with self.assertRaises(UserError), self.cr.savepoint():
            move.button_draft()
        cash.button_draft()
        move.button_draft()
        move.action_post()
        cash.action_post()
        self.assertEqual(len(move.bs_pos_settlement_ids), 2)
        self.assertTrue(cash.bs_pos_source_line_id.reconciled)

    def test_posted_partial_cash_settlement_keeps_its_lines_on_reset(self):
        self._order(25)
        component = self._cash_component(self._close())
        settlement = component.settlement_ids
        source_line = settlement.bs_pos_source_line_id
        branch_line = settlement.line_ids.filtered(lambda line: line.account_id == self.branch_cash)
        drawer_line = settlement.line_ids.filtered(lambda line: line.account_id == self.drawer)
        self.assertEqual(source_line.balance, 25)
        settlement.write({"line_ids": [
            Command.update(branch_line.id, {"balance": 10, "amount_currency": 10}),
            Command.update(drawer_line.id, {"balance": -10, "amount_currency": -10}),
        ]})
        settlement.action_post()
        original_lines = {line.id: line.balance for line in settlement.line_ids}
        self.assertEqual(source_line.amount_residual, 15)

        settlement.button_draft()

        self.assertEqual(settlement.state, "draft")
        self.assertEqual({line.id: line.balance for line in settlement.line_ids}, original_lines)
        self.assertEqual(source_line.amount_residual, 25)
        self.assertFalse(source_line.matched_debit_ids | source_line.matched_credit_ids)

        settlement.action_post()
        self.assertEqual(settlement.state, "posted")
        self.assertEqual({line.id: line.balance for line in settlement.line_ids}, original_lines)
        self.assertEqual(source_line.amount_residual, 15)

    def test_cash_surplus(self):
        self._open(50)
        self._order(150)
        session = self._close(counted=210)
        component = self._cash_component(session)
        self.assertEqual(component.amount, 160)
        self.assertEqual(component.difference_amount, 10)
        self.assertEqual(self._balance(component.move_id, self.cash_payment_method.journal_id.profit_account_id), -10)
        self.assertEqual(self._balance(component.settlement_ids, self.branch_cash), 160)
        self.assertFalse(session.statement_line_ids)

    def test_zero_net_cash_has_no_transfer(self):
        self._order(10)
        session = self._close(counted=0)
        component = self._cash_component(session)
        self.assertEqual(component.amount, 0)
        self.assertEqual(component.payment_amount, 10)
        self.assertEqual(component.difference_amount, -10)
        self.assertEqual(component.move_id.state, "posted")
        self.assertFalse(component.settlement_ids)
        self.assertFalse(session.statement_line_ids)

    def test_split_customers_keep_gross_receivables_and_one_cash_transfer(self):
        self.cash_payment_method.write({
            "split_transactions": True, "partner_id": self.partner_mobt.id,
        })
        self._order(100)
        other_order = self._order(50)
        other_partner = self.partner_mobt.copy({"name": "Second Cash Customer"})
        other_order.partner_id = other_partner
        session = self._close(counted=140)
        component = self._cash_component(session)
        self.assertEqual(len(component), 1)
        self.assertEqual(component.amount, 140)
        receivables = component.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        self.assertEqual(len(receivables), 2)
        self.assertEqual(set(receivables.mapped("credit")), {100, 50})
        self.assertEqual(receivables.partner_id, self.partner_mobt | other_partner)
        self.assertTrue(all(receivables.mapped("reconciled")))
        self.assertEqual(len(component.settlement_ids), 1)
        component.settlement_ids.action_post()
        self.assertTrue(component.settlement_ids.bs_pos_source_line_id.reconciled)

    def test_empty_session_count_difference(self):
        session = self._open(50)
        session.start_at = fields.Datetime.now() - timedelta(days=2)
        opening_date = fields.Date.context_today(session, timestamp=session.start_at)
        session = self._close(counted=40)
        component = self._cash_component(session)
        self.assertEqual(component.kind, "difference")
        self.assertEqual(component.amount, -10)
        self.assertEqual(component.move_id.state, "posted")
        self.assertEqual(component.move_id.date, opening_date)
        self.assertEqual(self._balance(component.settlement_ids, self.branch_cash), -10)
        self.assertFalse(session.statement_line_ids)

    def test_card_only_count_difference(self):
        self._open(50)
        self._order(70, [(self.bank_payment_method, 70)])
        session = self._close(counted=40)
        component = self._cash_component(session)
        self.assertEqual(component.kind, "difference")
        self.assertEqual(component.amount, -10)
        self.assertEqual(len(session.bs_collection_move_id.bs_pos_settlement_ids), 2)
        self.assertFalse(session.statement_line_ids)

    def test_foreign_currency_count_difference(self):
        self._set_euro_rates()
        self.config.journal_id = self.pos_config_eur.journal_id
        self._open(50)
        self._order(150)
        session = self._close(counted=190)
        component = self._cash_component(session)
        self.assertEqual(component.amount, 140)
        self.assertEqual(self._balance(component.move_id, self.drawer), 70)
        source = component.line_ids.filtered("bs_pos_payment_journal_id")
        self.assertEqual(source.amount_currency, 140)
        component.settlement_ids.action_post()
        self.assertTrue(source.reconciled)

    def test_cash_refund_with_count_difference(self):
        self._open(50)
        self._order(-10)
        session = self._close(counted=38)
        component = self._cash_component(session)
        self.assertEqual(component.payment_amount, -10)
        self.assertEqual(component.amount, -12)
        self.assertEqual(self._balance(component.settlement_ids, self.branch_cash), -12)
        component.settlement_ids.action_post()
        self.assertTrue(component.settlement_ids.bs_pos_source_line_id.reconciled)

    def test_cash_in_out_does_not_become_sales_or_duplicate_loss(self):
        self._open(50)
        session = self.config.current_session_id
        session.try_cash_in_out("in", 20, "Float", False, {"translatedType": "Cash In"})
        session.try_cash_in_out("out", 5, "Expense", False, {"translatedType": "Cash Out"})
        self._order(150)
        session = self._close(counted=205)
        component = self._cash_component(session)
        self.assertEqual(component.amount, 140)
        self.assertEqual(component.difference_amount, -10)
        self.assertEqual(len(session.statement_line_ids), 2)
        self.assertEqual(sum(session.statement_line_ids.mapped("amount")), 15)

    def test_existing_grouped_session_keeps_separate_count_entry(self):
        session = self._open(50)
        session.start_at = fields.Datetime.now() - timedelta(days=2)
        opening_date = fields.Date.context_today(session, timestamp=session.start_at)
        session._write({"bs_net_cash_difference": False})
        session.invalidate_recordset(["bs_net_cash_difference"])
        self._order(150)
        session = self._close(counted=190)
        component = self._cash_component(session)
        self.assertEqual(component.amount, 150)
        self.assertEqual(component.collection_account_id, self.cash_outstanding)
        self.assertEqual(len(session.statement_line_ids), 1)
        self.assertEqual(session.statement_line_ids.amount, -10)
        self.assertEqual(session.statement_line_ids.move_id.date, opening_date)

    def test_no_branch_account_keeps_clearing_but_nets_difference(self):
        self.cash_payment_method.bs_branch_cash_account_id = False
        self._order(150)
        session = self._close(counted=140)
        component = self._cash_component(session)
        self.assertEqual(component.collection_account_id, self.cash_outstanding)
        self.assertEqual(component.settlement_account_id, self.drawer)
        self.assertEqual(component.amount, 140)
        component.settlement_ids.action_post()
        self.assertTrue(component.settlement_ids.bs_pos_source_line_id.reconciled)

    def test_branch_account_must_be_separate_cash_same_company(self):
        for account in self.drawer | self.cash_outstanding:
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.cash_payment_method.bs_branch_cash_account_id = account
        other_company = self.env["res.company"].create({"name": "Other cash company"})
        foreign_account = self.env["account.account"].create({
            "name": "Other Cash", "code": "OTHCASH", "account_type": "asset_cash",
            "company_ids": [Command.set(other_company.ids)],
        })
        with self.assertRaises(UserError), self.cr.savepoint():
            self.cash_payment_method.bs_branch_cash_account_id = foreign_account

    def test_changed_configuration_does_not_reroute_existing_transfer(self):
        self._order(150)
        session = self._close(counted=140)
        component = self._cash_component(session)
        transfer = component.settlement_ids
        self.cash_payment_method.bs_branch_cash_account_id = self.branch_cash.copy({"code": "BSBOX2"})
        transfer.action_post()
        self.assertEqual(self._balance(transfer, self.branch_cash), 140)
        with self.assertRaises(UserError), self.cr.savepoint():
            component.settlement_account_id = self.drawer
        with self.assertRaises(UserError), self.cr.savepoint():
            session.bs_net_cash_difference = False
