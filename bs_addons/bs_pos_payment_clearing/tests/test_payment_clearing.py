from datetime import timedelta

from psycopg2.errors import UniqueViolation

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.point_of_sale.tests.common import CommonPosTest


@tagged("post_install", "-at_install")
class PosClearingCommon(CommonPosTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.pos_config_usd
        cls.config.write({"journal_id": cls.company_data["default_journal_misc"].id, "cash_control": True})
        cls.cash_outstanding = cls.env["account.account"].create({
            "name": "POS Cash Clearing", "code": "BSCLR1",
            "account_type": "asset_current", "reconcile": True,
            "company_ids": [Command.set(cls.company.ids)],
        })
        cls.bank_outstanding = cls.cash_outstanding.copy({"name": "POS Bank Clearing", "code": "BSCLR2"})
        cls.cash_payment_method.outstanding_account_id = cls.cash_outstanding
        cls.bank_payment_method.outstanding_account_id = cls.bank_outstanding
        cls.cashier = cls.env["res.users"].create({
            "name": "Clearing Cashier", "login": "bs_clearing_cashier",
            "company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)],
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref("point_of_sale.group_pos_user").id,
            ])],
        })

    def _open(self, opening=0):
        self.config.open_ui()
        session = self.config.current_session_id
        session.set_opening_control(opening, None)
        return session

    def _order(self, amount=10, payments=None, invoice=False):
        if not self.config.current_session_id:
            self._open()
        return self.create_backend_pos_order({
            "pos_config": self.config,
            "order_data": {"partner_id": self.partner_mobt.id, "to_invoice": invoice},
            "line_data": [{
                "product_id": self.ten_dollars_no_tax.product_variant_id.id,
                "price_unit": abs(amount), "qty": 1 if amount >= 0 else -1,
            }],
            "payment_data": [
                {"payment_method_id": method.id, "amount": value}
                for method, value in (payments or [(self.cash_payment_method, amount)])
            ],
        })[0]

    def _close(self, counted=None):
        session = self.config.current_session_id
        session.invalidate_recordset()
        if session.cash_control:
            session.post_closing_cash_details(session.cash_register_balance_end if counted is None else counted)
        session.invalidate_recordset()
        result = session.close_session_from_ui()
        self.assertEqual(session.state, "closed", result)
        return session

    def _balance(self, moves, account):
        return sum(moves.line_ids.filtered(lambda line: line.account_id == account).mapped("balance"))

    def _set_euro_rates(self):
        euro = self.env.ref("base.EUR")
        self.assertNotEqual(euro, self.company.currency_id)
        for currency, value in ((self.company.currency_id, 1), (euro, 2)):
            rate = self.env["res.currency.rate"].search([
                ("company_id", "=", self.company.id), ("currency_id", "=", currency.id),
                ("name", "=", fields.Date.today()),
            ])
            if rate:
                rate.rate = value
            else:
                self.env["res.currency.rate"].create({
                    "company_id": self.company.id, "currency_id": currency.id,
                    "name": fields.Date.today(), "rate": value,
                })

    def _assert_routing(self, session):
        moves = session.statement_line_ids.move_id | session.bank_payment_ids.move_id
        self.assertTrue(moves)
        self.assertEqual(moves.journal_id, session.bs_clearing_journal_id)
        self.assertEqual(moves.mapped("state"), ["posted"] * len(moves))
        for move in moves:
            self.assertAlmostEqual(sum(move.line_ids.mapped("balance")), 0)
            self._assert_settlement(move)
        self.assertAlmostEqual(self._balance(moves, self.cash_payment_method.journal_id.default_account_id), 0)
        return moves

    def _assert_settlement(self, source):
        settlement = source.bs_pos_settlement_ids
        self.assertEqual(len(settlement), 1)
        self.assertEqual(settlement.state, "draft")
        self.assertEqual(settlement.auto_post, "no")
        self.assertEqual(settlement.date, source.date)
        self.assertEqual(settlement.journal_id, source.bs_pos_payment_journal_id)
        self.assertEqual(settlement.company_id, source.company_id)
        self.assertFalse(settlement.origin_payment_id | settlement.payment_ids)
        self.assertFalse(settlement.statement_line_id)
        self.assertFalse(settlement.bs_pos_clearing_session_id)
        self.assertFalse(settlement.bs_pos_settlement_ids)
        self.assertTrue(settlement._bs_settlement_lines_match(source._bs_prepare_settlement_lines()))
        self.assertAlmostEqual(sum(settlement.line_ids.mapped("balance")), 0)
        return settlement


@tagged("post_install", "-at_install")
class TestPosStockExpenseFiscalPosition(PosClearingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source_expense = cls.company_data["default_account_expense"]
        cls.mapped_expense = cls.source_expense.copy({
            "name": "Mapped POS stock expense", "code": "POSFPCOST",
        })
        cls.fiscal_position = cls.env["account.fiscal.position"].create({
            "name": "POS stock expense mapping",
            "account_ids": [Command.create({
                "account_src_id": cls.source_expense.id,
                "account_dest_id": cls.mapped_expense.id,
            })],
        })
        cls.config.write({
            "tax_regime_selection": True,
            "default_fiscal_position_id": cls.fiscal_position.id,
            "fiscal_position_ids": [Command.set(cls.fiscal_position.ids)],
        })
        cls.analytic_account = False
        if "analytic_account_id" in cls.config._fields:
            plan = cls.env["account.analytic.plan"].create({"name": "POS cost mapping test"})
            cls.analytic_account = cls.env["account.analytic.account"].create({
                "name": "POS cost mapping test", "plan_id": plan.id,
                "company_id": cls.company.id,
            })
            cls.config.analytic_account_id = cls.analytic_account
        cls.stock_account_product_categ.property_cost_method = "standard"
        cls.ten_dollars_no_tax.write({
            "is_storable": True,
            "categ_id": cls.stock_account_product_categ.id,
            "property_account_expense_id": cls.source_expense.id,
            "standard_price": 3,
        })

    def test_closing_maps_stock_expense_from_order_fiscal_position(self):
        order = self._order()
        self.assertEqual(order.fiscal_position_id, self.fiscal_position)
        self.assertTrue(order.picking_ids.move_ids)

        session = self._close()
        self.assertEqual(self._balance(session.move_id, self.mapped_expense), 3)
        self.assertEqual(self._balance(session.move_id, self.source_expense), 0)
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0)
        if self.analytic_account:
            mapped_line = session.move_id.line_ids.filtered(
                lambda line: line.account_id == self.mapped_expense,
            )
            self.assertEqual(mapped_line.analytic_distribution, {
                str(self.analytic_account.id): 100.0,
            })

    def test_mixed_fiscal_positions_keep_original_stock_expense(self):
        self._order()
        second_order = self._order()
        second_order.fiscal_position_id = False

        session = self._close()
        self.assertEqual(self._balance(session.move_id, self.source_expense), 6)
        self.assertEqual(self._balance(session.move_id, self.mapped_expense), 0)
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0)


@tagged("post_install", "-at_install")
class TestPosPaymentClearing(PosClearingCommon):
    """Regression coverage for sessions created before grouping was introduced."""

    def _open(self, opening=0):
        session = super()._open(opening)
        session._write({"bs_group_payment_entries": False})
        session.invalidate_recordset(["bs_group_payment_entries"])
        return session

    def test_legacy_closing_entries_use_session_opening_date(self):
        session = self._open()
        session.start_at = fields.Datetime.now() - timedelta(days=2)
        opening_date = fields.Date.context_today(session, timestamp=session.start_at)
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])

        session = self._close()
        self.assertEqual(session.move_id.date, opening_date)
        self.assertEqual(session.statement_line_ids.date, opening_date)
        self.assertEqual(session.statement_line_ids.move_id.date, opening_date)
        self.assertEqual(session.bank_payment_ids.date, opening_date)
        self.assertEqual(session.bank_payment_ids.move_id.date, opening_date)

    def test_outstanding_mandatory_for_cash_and_bank(self):
        for method in self.cash_payment_method | self.bank_payment_method:
            with self.subTest(method=method.type), self.assertRaises(ValidationError):
                method.outstanding_account_id = False
        self.credit_payment_method.outstanding_account_id = False
        for method in self.cash_payment_method | self.bank_payment_method:
            with self.subTest(create=method.type), self.assertRaises(ValidationError):
                self.env["pos.payment.method"].create({
                    "name": "Invalid", "journal_id": method.journal_id.id, "outstanding_account_id": False,
                })

    def test_invalid_outstanding_account(self):
        for account in (
            self.company_data["default_account_expense"],
            self.cash_payment_method.journal_id.default_account_id,
            self.cash_payment_method.journal_id.suspense_account_id,
        ):
            with self.subTest(account=account.display_name), self.assertRaises(ValidationError):
                self.cash_payment_method.outstanding_account_id = account

    def test_cash_collection(self):
        self._order(115)
        session = self._close()
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), 115)
        self.assertEqual(session.cash_register_balance_end_real, 115)
        self.assertTrue(self.cash_payment_method.is_cash_count)
        self.assertTrue(session.statement_line_ids.move_id.line_ids.filtered(
            lambda line: line.account_id == self.cash_payment_method.receivable_account_id,
        ).reconciled)
        self.assertFalse(session.statement_line_ids.move_id.line_ids.filtered(
            lambda line: line.account_id == self.cash_outstanding,
        ).reconciled)

    def test_bank_collection(self):
        self._order(69, [(self.bank_payment_method, 69)])
        session = self._close()
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.bank_outstanding), 69)
        self.assertEqual(session.bank_payment_ids.journal_id, self.bank_payment_method.journal_id)
        self.assertTrue(session.bank_payment_ids.is_reconciled)
        self.assertFalse(session.bank_payment_ids.is_matched)

    def test_mixed_payment(self):
        self._order(184, [(self.cash_payment_method, 115), (self.bank_payment_method, 69)])
        session = self._close()
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), 115)
        self.assertEqual(self._balance(moves, self.bank_outstanding), 69)

    def test_cash_change(self):
        self._open()
        order = self.create_backend_pos_order({
            "line_data": [{"product_id": self.ten_dollars_no_tax.product_variant_id.id, "qty": 1}],
        })[0]
        order.add_payment({"pos_order_id": order.id, "payment_method_id": self.cash_payment_method.id, "amount": 20})
        order.add_payment({"pos_order_id": order.id, "payment_method_id": self.cash_payment_method.id, "amount": -10, "is_change": True})
        order.action_pos_order_paid()
        session = self._close()
        self.assertEqual(self._balance(self._assert_routing(session), self.cash_outstanding), 10)

    def test_refunds_cash_and_bank(self):
        self._order(-115, [(self.cash_payment_method, -115)])
        self._order(-69, [(self.bank_payment_method, -69)])
        session = self._close()
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), -115)
        self.assertEqual(self._balance(moves, self.bank_outstanding), -69)

    def test_identified_customer(self):
        (self.cash_payment_method | self.bank_payment_method).write({
            "split_transactions": True, "partner_id": self.partner_mobt.id,
        })
        self._order(184, [(self.cash_payment_method, 115), (self.bank_payment_method, 69)])
        session = self._close()
        moves = self._assert_routing(session)
        self.assertEqual(moves.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable",
        ).partner_id, self.partner_mobt)
        self.assertTrue(all(moves.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable",
        ).mapped("reconciled")))

    def test_invoiced_mixed_order(self):
        order = self._order(184, [(self.cash_payment_method, 115), (self.bank_payment_method, 69)], invoice=True)
        if not order.is_invoiced:
            order.with_context(generate_pdf=False)._generate_pos_order_invoice()
        session = self._close()
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), 115)
        self.assertEqual(self._balance(moves, self.bank_outstanding), 69)
        self.assertEqual(order.account_move.amount_residual, 0)

    def test_cash_in_out_and_difference(self):
        session = self._open(100)
        session.try_cash_in_out("in", 20, "Float", False, {"translatedType": "Cash In"})
        session.try_cash_in_out("out", 5, "Petty cash", False, {"translatedType": "Cash Out"})
        self._order(115)
        session = self._close(counted=220)
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), 120)
        self.assertEqual(self._balance(moves, session.cash_journal_id.loss_account_id), 10)
        self.assertEqual(session.cash_register_difference, -10)
        self.assertEqual(session.cash_register_balance_end_real, 220)

    def test_cash_overage(self):
        self._order(10)
        session = self._close(counted=12)
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), 12)
        self.assertEqual(self._balance(moves, session.cash_journal_id.profit_account_id), -2)

    def test_statement_synchronization_keeps_clearing(self):
        session = self._open()
        session.try_cash_in_out("in", 20, "Float", False, {"translatedType": "Cash In"})
        line = session.statement_line_ids
        line.payment_ref = "Updated label"
        line.amount = 25
        self.assertEqual(line._seek_for_lines()[0].account_id, self.cash_outstanding)
        self.assertEqual(self._balance(line.move_id, self.cash_outstanding), 25)
        self.assertEqual(line.journal_id, session.bs_clearing_journal_id)
        line.move_id.line_ids.filtered(lambda item: item.account_id == self.cash_outstanding).name = "Renamed"
        self.assertEqual(line.amount, 25)

    def test_bank_settlement_remains_standard(self):
        self._order(69, [(self.bank_payment_method, 69)])
        session = self._close()
        payment = session.bank_payment_ids
        statement = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank_payment_method.journal_id.id,
            "amount": 69, "payment_ref": "Bank settlement",
            "counterpart_account_id": self.bank_outstanding.id,
        })
        lines = (payment.move_id | statement.move_id).line_ids.filtered(
            lambda line: line.account_id == self.bank_outstanding,
        )
        lines.reconcile()
        self.assertTrue(payment.is_matched)
        self.assertEqual(self._balance(payment.move_id | statement.move_id, self.bank_outstanding), 0)
        self.assertEqual(statement.journal_id.type, "bank")

    def test_non_pos_statement_unchanged(self):
        journal = self.cash_payment_method.journal_id
        line = self.env["account.bank.statement.line"].create({
            "journal_id": journal.id, "amount": 40, "payment_ref": "Outside POS",
        })
        self.assertFalse(line.bs_source_cash_journal_id)
        self.assertEqual(line.journal_id, journal)
        self.assertEqual(line._seek_for_lines()[0].account_id, journal.default_account_id)

    def test_cashier_can_close_without_accounting_permissions(self):
        self._order(10)
        session = self.config.current_session_id
        session.post_closing_cash_details(10)
        session.invalidate_recordset()
        session.with_user(self.cashier).close_session_from_ui()
        self.assertEqual(session.state, "closed")
        self._assert_routing(session)

    def test_session_routing_cannot_be_changed(self):
        session = self._open()
        with self.assertRaises(UserError):
            session.bs_clearing_journal_id = False

    def test_pos_journal_cannot_change_while_open(self):
        self._open()
        with self.assertRaises(UserError):
            self.config.journal_id = self.company_data["default_journal_sale"]

    def test_cashier_can_open_session(self):
        self.config.with_user(self.cashier).open_ui()
        self.assertTrue(self.config.current_session_id.bs_clearing_journal_id)

    def test_cross_company_account_rejected(self):
        company = self.env["res.company"].create({"name": "Other Clearing Company"})
        account = self.env["account.account"].create({
            "name": "Other Clearing", "code": "BSOTHER", "account_type": "asset_current",
            "reconcile": True, "company_ids": [Command.set(company.ids)],
        })
        with self.assertRaises(UserError):
            self.cash_payment_method.with_context(allowed_company_ids=(self.company | company).ids).outstanding_account_id = account

    def test_archived_account_blocks_opening(self):
        self.cash_outstanding.active = False
        with self.assertRaises(ValidationError):
            self._open()

    def test_cash_control_disabled(self):
        self.config.cash_control = False
        self._order(10)
        self._assert_routing(self._close())

    def test_cash_rounding(self):
        self.config.write({
            "cash_rounding": True, "rounding_method": self.account_cash_rounding_up.id,
            "only_round_cash_method": True,
        })
        self._order(10.02, [(self.cash_payment_method, 10.05)])
        session = self._close()
        self.assertEqual(self._balance(self._assert_routing(session), self.cash_outstanding), 10.05)
        self.assertAlmostEqual(sum(session.move_id.line_ids.mapped("balance")), 0)

    def test_invoiced_refund(self):
        order = self._order(20, [(self.bank_payment_method, 20)], invoice=True)
        if not order.is_invoiced:
            order.with_context(generate_pdf=False)._generate_pos_order_invoice()
        self._close()
        self._open()
        action = order.refund()
        refund = self.env["pos.order"].browse(action["res_id"])
        refund.add_payment({"pos_order_id": refund.id, "payment_method_id": self.bank_payment_method.id, "amount": -20})
        refund.action_pos_order_paid()
        if not refund.is_invoiced:
            refund.with_context(generate_pdf=False)._generate_pos_order_invoice()
        session = self._close()
        self.assertEqual(self._balance(self._assert_routing(session), self.bank_outstanding), -20)
        self.assertEqual(refund.account_move.move_type, "out_refund")
        self.assertEqual(refund.account_move.amount_residual, 0)

    def test_bank_refund_difference(self):
        self.bank_payment_method.journal_id.profit_account_id = self.company_data["default_account_revenue"]
        self._order(-20, [(self.bank_payment_method, -20)])
        session = self.config.current_session_id
        session.post_closing_cash_details(0)
        session.close_session_from_ui([(self.bank_payment_method.id, 2)])
        self.assertEqual(session.state, "closed")
        self.assertEqual(self._balance(self._assert_routing(session), self.bank_outstanding), -18)

    def test_optional_cash_count_controls(self):
        if "pcm_require_count" not in self.config._fields:
            self.skipTest("Optional pos_cash_management is not installed")
        self.config.write({"pcm_require_count": True, "pcm_blind_count": True})
        self._order(10)
        session = self.config.current_session_id
        with self.assertRaises(UserError):
            session.with_user(self.cashier).post_closing_cash_details(0)
        data = session.with_user(self.cashier).get_closing_control_data()
        self.assertEqual(data["default_cash_details"]["amount"], 0)
        self.assertEqual(data["default_cash_details"]["payment_amount"], 0)
        self._assert_routing(self._close(10))

    def test_zero_net_collections(self):
        self._order(10)
        self._order(-10)
        self._order(15, [(self.bank_payment_method, 15)])
        self._order(-15, [(self.bank_payment_method, -15)])
        session = self._close()
        moves = session.statement_line_ids.move_id | session.bank_payment_ids.move_id
        self.assertAlmostEqual(self._balance(moves, self.cash_outstanding), 0)
        self.assertAlmostEqual(self._balance(moves, self.bank_outstanding), 0)

    def test_same_payment_method_uses_each_pos_journal(self):
        self._order(10, [(self.bank_payment_method, 10)])
        first = self._close()
        self.config = self.pos_config_eur
        self._order(20, [(self.bank_payment_method, 20)])
        second = self._close()
        self.assertNotEqual(first.bank_payment_ids.move_id.journal_id, second.bank_payment_ids.move_id.journal_id)
        self.assertEqual(second.bank_payment_ids.move_id.journal_id, self.config.journal_id)

    def test_cash_outstanding_can_be_settled(self):
        self._order(115)
        session = self._close()
        statement = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank_payment_method.journal_id.id,
            "amount": 115, "payment_ref": "Cash deposited",
            "counterpart_account_id": self.cash_outstanding.id,
        })
        clearing_lines = session.statement_line_ids.move_id.line_ids.filtered(
            lambda line: line.account_id == self.cash_outstanding,
        )
        candidates = self.env["account.move.line"].search(
            statement._get_default_amls_matching_domain() + [("id", "in", clearing_lines.ids)],
        )
        self.assertEqual(candidates, clearing_lines)
        lines = (session.statement_line_ids.move_id | statement.move_id).line_ids.filtered(
            lambda line: line.account_id == self.cash_outstanding,
        )
        lines.reconcile()
        self.assertTrue(all(lines.mapped("reconciled")))

    def test_enterprise_cash_deposit_widget_reconciliation(self):
        if not hasattr(self.env["account.bank.statement.line"], "set_line_bank_statement_line"):
            self.skipTest("Enterprise bank reconciliation is not installed")
        self._order(115)
        session = self._close()
        clearing = session.statement_line_ids.move_id.line_ids.filtered(
            lambda line: line.account_id == self.cash_outstanding,
        )
        deposit = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank_payment_method.journal_id.id,
            "amount": 115, "payment_ref": "Actual cash deposit",
        })
        deposit.set_line_bank_statement_line(clearing.ids)
        self.assertTrue(clearing.reconciled)
        self.assertEqual(self._balance(session.statement_line_ids.move_id | deposit.move_id, self.cash_outstanding), 0)
        self.assertEqual(deposit.journal_id, self.bank_payment_method.journal_id)

    def test_enterprise_bank_deposit_widget_reconciliation(self):
        if not hasattr(self.env["account.bank.statement.line"], "set_line_bank_statement_line"):
            self.skipTest("Enterprise bank reconciliation is not installed")
        self._order(69, [(self.bank_payment_method, 69)])
        session = self._close()
        payment = session.bank_payment_ids
        clearing = payment.move_id.line_ids.filtered(lambda line: line.account_id == self.bank_outstanding)
        deposit = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank_payment_method.journal_id.id,
            "amount": 69, "payment_ref": "Actual card settlement",
        })
        deposit.set_line_bank_statement_line(clearing.ids)
        self.assertTrue(clearing.reconciled)
        self.assertTrue(payment.is_matched)

    def test_legacy_session_keeps_original_cash_flow(self):
        session = self._open()
        # Simulate a session persisted before the nullable routing column existed.
        session._write({"bs_clearing_journal_id": None})
        session.invalidate_recordset(["bs_clearing_journal_id"])
        self._order(10)
        session = self._close()
        line = session.statement_line_ids
        self.assertFalse(line.bs_source_cash_journal_id)
        self.assertEqual(line.journal_id, session.cash_journal_id)
        self.assertEqual(self._balance(line.move_id, session.cash_journal_id.default_account_id), 10)

    def test_bank_difference(self):
        journal = self.bank_payment_method.journal_id
        journal.write({
            "loss_account_id": self.company_data["default_account_expense"].id,
            "profit_account_id": self.company_data["default_account_revenue"].id,
        })
        self._order(69, [(self.bank_payment_method, 69)])
        session = self.config.current_session_id
        session.post_closing_cash_details(0)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])
        self.assertEqual(session.state, "closed")
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.bank_outstanding), 67)
        self.assertEqual(self._balance(moves, journal.loss_account_id), 2)

    def test_split_bank_difference_journal(self):
        self.bank_payment_method.write({
            "split_transactions": True, "partner_id": self.partner_mobt.id,
        })
        self.bank_payment_method.journal_id.loss_account_id = self.company_data["default_account_expense"]
        self._order(69, [(self.bank_payment_method, 69)])
        session = self.config.current_session_id
        session.start_at = fields.Datetime.now() - timedelta(days=2)
        opening_date = fields.Date.context_today(session, timestamp=session.start_at)
        session.post_closing_cash_details(0)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])
        self.assertEqual(session.state, "closed")
        difference = self.env["account.move"].search([
            ("bs_pos_clearing_session_id", "=", session.id), ("origin_payment_id", "=", False),
        ])
        self.assertEqual(len(difference), 1)
        self.assertEqual(difference.journal_id, session.bs_clearing_journal_id)
        self.assertEqual(difference.date, opening_date)
        self.assertEqual(self._balance(difference, self.bank_outstanding), -2)
        self._assert_settlement(difference)

    def test_draft_settlements_post_and_reconcile_individually(self):
        self._order(184, [(self.cash_payment_method, 115), (self.bank_payment_method, 69)])
        session = self._close()
        sources = self._assert_routing(session)
        settlements = sources.bs_pos_settlement_ids
        settlements.action_post()
        self.assertEqual(settlements.mapped("state"), ["posted"] * 2)
        self.assertTrue(session.bank_payment_ids.is_matched)
        for source in sources:
            settlement = source.bs_pos_settlement_ids
            outstanding = source._bs_get_outstanding_line()
            counterpart = settlement.line_ids.filtered(lambda line: line.account_id == outstanding.account_id)
            self.assertTrue(outstanding.reconciled)
            self.assertEqual(outstanding.full_reconcile_id, counterpart.full_reconcile_id)
            self.assertEqual(settlement.line_ids.account_id, source.bs_pos_payment_journal_id.default_account_id | outstanding.account_id)
        self.assertEqual(self._balance(settlements, self.cash_payment_method.journal_id.default_account_id), 115)
        self.assertEqual(self._balance(settlements, self.bank_payment_method.journal_id.default_account_id), 69)

    def test_bank_settlement_difference_survives_reset_to_draft(self):
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        settlement = source.bs_pos_settlement_ids
        outstanding = source._bs_get_outstanding_line()
        bank_account = self.bank_payment_method.journal_id.default_account_id
        bank_line = settlement.line_ids.filtered(lambda line: line.account_id == bank_account)
        counterpart = settlement.line_ids.filtered(lambda line: line.account_id == outstanding.account_id)
        settlement.write({"line_ids": [
            Command.update(bank_line.id, {"balance": 8, "amount_currency": 8}),
            Command.update(counterpart.id, {"balance": -8, "amount_currency": -8}),
            Command.create({"account_id": bank_account.id, "balance": 4, "amount_currency": 4}),
            Command.create({"account_id": outstanding.account_id.id, "balance": -4, "amount_currency": -4}),
        ]})
        settlement.action_post()
        line_balances = {line.id: line.balance for line in settlement.line_ids}
        self.assertEqual(len(line_balances), 4)
        self.assertEqual(self._balance(settlement, outstanding.account_id), -12)
        self.assertTrue(outstanding.reconciled)

        settlement.button_draft()

        self.assertEqual(settlement.state, "draft")
        self.assertEqual({line.id: line.balance for line in settlement.line_ids}, line_balances)
        self.assertFalse(outstanding.matched_debit_ids | outstanding.matched_credit_ids)
        source._bs_sync_pos_settlements()
        self.assertEqual({line.id: line.balance for line in settlement.line_ids}, line_balances)

    def test_refund_settlements_post(self):
        self._order(-184, [(self.cash_payment_method, -115), (self.bank_payment_method, -69)])
        sources = self._assert_routing(self._close())
        settlements = sources.bs_pos_settlement_ids
        settlements.action_post()
        self.assertEqual(self._balance(settlements, self.cash_payment_method.journal_id.default_account_id), -115)
        self.assertEqual(self._balance(settlements, self.bank_payment_method.journal_id.default_account_id), -69)
        for source in sources:
            self.assertTrue(source._bs_get_outstanding_line().reconciled)

    def test_shared_outstanding_matches_only_own_source(self):
        self.bank_payment_method.outstanding_account_id = self.cash_outstanding
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self._close()
        cash = session.statement_line_ids.move_id
        bank = session.bank_payment_ids.move_id
        cash.bs_pos_settlement_ids.action_post()
        self.assertTrue(cash._bs_get_outstanding_line().reconciled)
        self.assertFalse(bank._bs_get_outstanding_line().reconciled)
        bank.bs_pos_settlement_ids.action_post()
        self.assertTrue(bank._bs_get_outstanding_line().reconciled)
        self.assertNotEqual(cash._bs_get_outstanding_line().full_reconcile_id, bank._bs_get_outstanding_line().full_reconcile_id)

    def test_cash_movements_and_count_settlements_post(self):
        session = self._open(100)
        session.try_cash_in_out("in", 20, "Float", False, {"translatedType": "Cash In"})
        session.try_cash_in_out("out", 5, "Petty cash", False, {"translatedType": "Cash Out"})
        self._order(115)
        sources = self._assert_routing(self._close(220))
        self.assertEqual(len(sources.bs_pos_settlement_ids), 4)
        sources.bs_pos_settlement_ids.action_post()
        for source in sources:
            self.assertTrue(source._bs_get_outstanding_line().reconciled)

    def test_settlement_tracks_cash_edit(self):
        session = self._open()
        session.try_cash_in_out("in", 20, "Float", False, {"translatedType": "Cash In"})
        line = session.statement_line_ids
        settlement = line.move_id.bs_pos_settlement_ids
        line.amount = 25
        self.assertEqual(line.move_id.bs_pos_settlement_ids, settlement)
        self._assert_settlement(line.move_id)
        settlement.action_post()
        self.assertEqual(self._balance(settlement, self.cash_outstanding), -25)
        with self.assertRaises(UserError), self.cr.savepoint():
            line.amount = 30

    def test_settlement_reset_repost_and_no_duplicates(self):
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        settlement = self._assert_settlement(source)
        source._bs_sync_pos_settlements()
        self.assertEqual(source.bs_pos_settlement_ids, settlement)
        settlement.action_post()
        with self.assertRaises(UserError), self.cr.savepoint():
            source.button_draft()
        settlement.button_draft()
        self.assertFalse(source._bs_get_outstanding_line().reconciled)
        settlement.action_post()
        self.assertTrue(source._bs_get_outstanding_line().reconciled)
        self.assertEqual(source.bs_pos_settlement_ids, settlement)

    def test_settlement_cancellation_not_recreated(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        settlement = source.bs_pos_settlement_ids
        settlement.button_cancel()
        source._bs_sync_pos_settlements()
        self.assertEqual(source.bs_pos_settlement_ids, settlement)
        self.assertEqual(settlement.state, "cancel")
        self.assertFalse(source._bs_get_outstanding_line().reconciled)
        with self.assertRaises(UserError), self.cr.savepoint():
            settlement.unlink()

    def test_settlement_does_not_match_already_settled_source(self):
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        statement = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank_payment_method.journal_id.id, "amount": 10,
            "counterpart_account_id": self.bank_outstanding.id,
        })
        (source | statement.move_id).line_ids.filtered(lambda line: line.account_id == self.bank_outstanding).reconcile()
        with self.assertRaises(UserError), self.cr.savepoint():
            source.bs_pos_settlement_ids.action_post()
        self.assertEqual(source.bs_pos_settlement_ids.state, "draft")

    def test_settlement_cannot_change_routing_or_amount(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        settlement = source.bs_pos_settlement_ids
        for vals in (
            {"bs_pos_source_move_id": False},
            {"journal_id": self.bank_payment_method.journal_id.id},
        ):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                settlement.write(vals)
        with self.assertRaises(UserError), self.cr.savepoint():
            settlement.write({"line_ids": [
                Command.update(line.id, {"balance": line.balance * 2, "amount_currency": line.amount_currency * 2})
                for line in settlement.line_ids
            ]})
            settlement.action_post()

    def test_settlement_duplicate_constraint_and_copy(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        settlement = source.bs_pos_settlement_ids
        duplicate = settlement.copy()
        self.assertFalse(duplicate.bs_pos_source_move_id)
        self.assertFalse(duplicate.bs_pos_payment_journal_id)
        with self.assertRaises(UniqueViolation), mute_logger("odoo.sql_db"), self.cr.savepoint():
            settlement.copy({"bs_pos_source_move_id": source.id})

    def test_settlement_foreign_currency_posts(self):
        self._set_euro_rates()
        self.config.journal_id = self.pos_config_eur.journal_id
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        sources = self._assert_routing(self._close())
        sources.bs_pos_settlement_ids.action_post()
        for source in sources:
            self.assertTrue(source._bs_get_outstanding_line().reconciled)
            self.assertAlmostEqual(self._balance(source.bs_pos_settlement_ids, source.bs_pos_outstanding_account_id), -5)

    def test_source_draft_blocks_settlement_post(self):
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        source.button_draft()
        with self.assertRaises(UserError), self.cr.savepoint():
            source.bs_pos_settlement_ids.action_post()

    def test_settlement_reversal_does_not_generate_another_settlement(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        settlement = source.bs_pos_settlement_ids
        settlement.action_post()
        reverse = settlement._reverse_moves(cancel=True)
        self.assertEqual(reverse.state, "posted")
        self.assertFalse(reverse.bs_pos_source_move_id)
        self.assertFalse(reverse.bs_pos_settlement_ids)
        self.assertFalse(source._bs_get_outstanding_line().reconciled)

    def test_cashier_cannot_post_settlement(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        with self.assertRaises(AccessError), self.cr.savepoint():
            source.bs_pos_settlement_ids.with_user(self.cashier).action_post()
        self.assertEqual(source.bs_pos_settlement_ids.state, "draft")
        self.assertFalse(source._bs_get_outstanding_line().reconciled)

    def test_settlement_cross_company_link_rejected(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        other_company = self.env["res.company"].create({"name": "Other Settlement Company"})
        with self.assertRaises(UserError), self.cr.savepoint():
            source.bs_pos_settlement_ids.company_id = other_company

    def test_foreign_bank_journal_settlement(self):
        self._set_euro_rates()
        self.config.journal_id = self.pos_config_eur.journal_id
        journal = self.bank_payment_method.journal_id
        journal.currency_id = self.env.ref("base.EUR")
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        settlement = self._assert_settlement(source)
        liquidity = settlement.line_ids.filtered(lambda line: line.account_id == journal.default_account_id)
        self.assertEqual(liquidity.currency_id, journal.currency_id)
        self.assertEqual(liquidity.amount_currency, 10)
        self.assertEqual(liquidity.balance, 5)
        settlement.action_post()
        self.assertTrue(source._bs_get_outstanding_line().reconciled)

    def test_partial_external_matching_blocks_full_settlement(self):
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        statement = self.env["account.bank.statement.line"].create({
            "journal_id": self.bank_payment_method.journal_id.id, "amount": 4,
            "counterpart_account_id": self.bank_outstanding.id,
        })
        (source | statement.move_id).line_ids.filtered(lambda line: line.account_id == self.bank_outstanding).reconcile()
        self.assertEqual(source._bs_get_outstanding_line().amount_residual, 6)
        with self.assertRaises(UserError), self.cr.savepoint():
            source.bs_pos_settlement_ids.action_post()
        self.assertEqual(source.bs_pos_settlement_ids.state, "draft")

    def test_source_repost_updates_existing_draft(self):
        self._order(10, [(self.bank_payment_method, 10)])
        session = self._close()
        payment = session.bank_payment_ids
        source = payment.move_id
        settlement = source.bs_pos_settlement_ids
        source.button_draft()
        payment.amount = 12
        source.action_post()
        self.assertEqual(source.bs_pos_settlement_ids, settlement)
        self._assert_settlement(source)
        settlement.action_post()
        self.assertEqual(self._balance(settlement, self.bank_outstanding), -12)

    def test_source_redate_updates_draft_date(self):
        self._order(10, [(self.bank_payment_method, 10)])
        source = self._close().bank_payment_ids.move_id
        settlement = source.bs_pos_settlement_ids
        source.button_draft()
        source.date = fields.Date.subtract(source.date, days=1)
        source.action_post()
        self.assertEqual(settlement.date, source.date)
        self.assertEqual(source.bs_pos_settlement_ids, settlement)

    def test_posted_settlement_financial_edits_rejected(self):
        self._order(10)
        source = self._close().statement_line_ids.move_id
        settlement = source.bs_pos_settlement_ids
        settlement.action_post()
        with self.assertRaises(UserError), self.cr.savepoint():
            settlement.write({"line_ids": [
                Command.update(line.id, {"balance": line.balance * 2, "amount_currency": line.amount_currency * 2})
                for line in settlement.line_ids
            ]})
        self.assertTrue(source._bs_get_outstanding_line().reconciled)

    def test_cancelled_settlement_reset_updates_from_source(self):
        self._order(10, [(self.bank_payment_method, 10)])
        payment = self._close().bank_payment_ids
        source = payment.move_id
        settlement = source.bs_pos_settlement_ids
        settlement.button_cancel()
        source.button_draft()
        payment.amount = 12
        source.action_post()
        self.assertEqual(settlement.state, "cancel")
        settlement.button_draft()
        self._assert_settlement(source)
        settlement.action_post()
        self.assertEqual(self._balance(settlement, self.bank_outstanding), -12)

    def test_bank_difference_in_foreign_currency(self):
        self._set_euro_rates()
        self.config.journal_id = self.pos_config_eur.journal_id
        self.bank_payment_method.journal_id.loss_account_id = self.company_data["default_account_expense"]
        self._order(69, [(self.bank_payment_method, 69)])
        session = self.config.current_session_id
        session.post_closing_cash_details(0)
        session.close_session_from_ui([(self.bank_payment_method.id, -2)])
        self.assertEqual(session.state, "closed")
        self.assertEqual(session.bank_payment_ids.amount, 67)
        lines = session.bank_payment_ids.move_id.line_ids.filtered(lambda line: line.account_id == self.bank_outstanding)
        self.assertEqual(lines.currency_id, session.currency_id)
        self.assertAlmostEqual(sum(lines.mapped("amount_currency")), 67)

    def test_missing_settings_prevent_session(self):
        self.config.journal_id = False
        with self.assertRaises(UserError):
            self._open()

    def test_sale_journal_supported(self):
        self.config.journal_id = self.company_data["default_journal_sale"]
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        self._assert_routing(self._close())

    def test_repeated_close_does_not_duplicate(self):
        self._order(10)
        session = self._close()
        moves = self._assert_routing(session)
        with self.assertRaises(UserError):
            session.action_pos_session_close()
        self.assertEqual(session.statement_line_ids.move_id | session.bank_payment_ids.move_id, moves)

    def test_foreign_currency_cash_and_bank(self):
        self._set_euro_rates()
        euro = self.env.ref("base.EUR")
        self.config.write({"journal_id": self.pos_config_eur.journal_id.id})
        self._order(20, [(self.cash_payment_method, 10), (self.bank_payment_method, 10)])
        session = self._close()
        moves = self._assert_routing(session)
        for account in self.cash_outstanding | self.bank_outstanding:
            lines = moves.line_ids.filtered(lambda line: line.account_id == account)
            self.assertEqual(lines.currency_id, euro)
            self.assertEqual(sum(lines.mapped("amount_currency")), 10)
            self.assertAlmostEqual(sum(lines.mapped("balance")), euro._convert(10, self.company.currency_id, self.company, fields.Date.today()))

    def test_optional_cash_transfer_and_analytic_compatibility(self):
        if "pcm_auto_transfer" not in self.config._fields:
            self.skipTest("Optional pos_cash_management is not installed")
        self.config.pcm_auto_transfer = True
        self.config.pcm_transfer_reason = "Clearing handover"
        if "analytic_account_id" in self.config._fields:
            plan = self.env["account.analytic.plan"].create({"name": "Clearing Test"})
            self.config.analytic_account_id = self.env["account.analytic.account"].create({
                "name": "Clearing Branch", "plan_id": plan.id, "company_id": self.company.id,
            })
        self._order(115)
        session = self._close(counted=110)
        moves = self._assert_routing(session)
        self.assertEqual(self._balance(moves, self.cash_outstanding), 0)
        self.assertEqual(session.pcm_transferred_amount, 110)
        self.assertEqual(session.cash_register_balance_end_real, 110)
        session._pcm_transfer_counted_cash()
        self.assertEqual(session.statement_line_ids.move_id, moves)
        if "analytic_account_id" in self.config._fields:
            difference = moves.line_ids.filtered(lambda line: line.account_id == session.cash_journal_id.loss_account_id)
            self.assertEqual(difference.analytic_distribution, {str(self.config.analytic_account_id.id): 100})
