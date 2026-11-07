from datetime import datetime, time, timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.point_of_sale.tests.common import CommonPosTest


@tagged("post_install", "-at_install")
class TestPosAnalyticAccount(CommonPosTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.analytic_plan = cls.env["account.analytic.plan"].create(
            {"name": "POS Branches"}
        )
        cls.analytic_account = cls.env["account.analytic.account"].create(
            {
                "name": "Riyadh Branch",
                "plan_id": cls.analytic_plan.id,
                "company_id": cls.company.id,
            }
        )
        cls.pos_config_usd.analytic_account_id = cls.analytic_account

    def _create_paid_order(self, to_invoice=False):
        return self.create_backend_pos_order(
            {
                "order_data": {
                    "partner_id": self.partner_mobt.id,
                    "to_invoice": to_invoice,
                },
                "line_data": [
                    {
                        "product_id": self.ten_dollars_no_tax.product_variant_id.id,
                        "qty": 1,
                    }
                ],
                "payment_data": [
                    {
                        "payment_method_id": self.bank_payment_method.id,
                        "amount": 10,
                    }
                ],
            }
        )[0]

    def test_config_rejects_cross_company_analytic_account(self):
        other_company = self.env["res.company"].create({"name": "Other Company"})
        other_account = self.env["account.analytic.account"].create(
            {
                "name": "Other Branch",
                "plan_id": self.analytic_plan.id,
                "company_id": other_company.id,
            }
        )
        with self.assertRaises((UserError, ValidationError)):
            self.pos_config_usd.analytic_account_id = other_account

    def test_distribution_keeps_other_analytic_plan(self):
        other_plan = self.env["account.analytic.plan"].create(
            {"name": "POS Departments"}
        )
        other_account = self.env["account.analytic.account"].create(
            {
                "name": "Food Department",
                "plan_id": other_plan.id,
                "company_id": self.company.id,
            }
        )
        move = self.env["account.move"].create(
            {
                "journal_id": self.company_data["default_journal_misc"].id,
                "line_ids": [
                    Command.create(
                        {
                            "name": "POS Revenue",
                            "account_id": self.company_data[
                                "default_account_revenue"
                            ].id,
                            "debit": 10,
                            "analytic_distribution": {str(other_account.id): 100},
                        }
                    ),
                    Command.create(
                        {
                            "name": "Counterpart",
                            "account_id": self.company_data[
                                "default_account_expense"
                            ].id,
                            "credit": 10,
                        }
                    ),
                ],
            }
        )
        revenue_line = move.line_ids.filtered(
            lambda line: line.account_id
            == self.company_data["default_account_revenue"]
        )
        revenue_line._apply_pos_analytic_account(self.analytic_account)
        self.assertEqual(
            revenue_line.analytic_distribution,
            {str(other_account.id): 100, str(self.analytic_account.id): 100.0},
        )

    def test_pos_invoice_and_refund_keep_analytic_account(self):
        order = self._create_paid_order(to_invoice=True)
        invoice = order.with_context(generate_pdf=False)._generate_pos_order_invoice()
        self.assertEqual(order.pos_analytic_account_id, self.analytic_account)
        self.assertTrue(invoice.invoice_line_ids)
        self.assertTrue(
            all(
                line.analytic_distribution.get(str(self.analytic_account.id))
                == 100.0
                for line in invoice.invoice_line_ids
                if line.display_type == "product"
            )
        )

        replacement_account = self.env["account.analytic.account"].create(
            {
                "name": "Jeddah Branch",
                "plan_id": self.analytic_plan.id,
                "company_id": self.company.id,
            }
        )
        self.pos_config_usd.analytic_account_id = replacement_account
        refund = order._refund()
        self.assertEqual(refund.pos_analytic_account_id, self.analytic_account)
        refund.to_invoice = True
        payment_context = {"active_ids": refund.ids, "active_id": refund.id}
        refund_payment = self.env["pos.make.payment"].with_context(
            **payment_context
        ).create(
            {
                "payment_method_id": self.bank_payment_method.id,
                "amount": refund.amount_total,
            }
        )
        refund_payment.with_context(**payment_context).check()
        refund_invoice = refund.with_context(
            generate_pdf=False
        )._generate_pos_order_invoice()
        self.assertTrue(
            all(
                line.analytic_distribution.get(str(self.analytic_account.id))
                == 100.0
                for line in refund_invoice.invoice_line_ids
                if line.display_type == "product"
            )
        )

        credit_note = invoice._reverse_moves(cancel=False)
        self.assertTrue(
            all(
                line.analytic_distribution.get(str(self.analytic_account.id))
                == 100.0
                for line in credit_note.invoice_line_ids
                if line.display_type == "product"
            )
        )

    def test_pos_invoice_accounting_date_uses_session_opening(self):
        order = self._create_paid_order(to_invoice=True)
        opening_utc_date = fields.Date.today() - timedelta(days=3)
        order.session_id.start_at = datetime.combine(opening_utc_date, time(23, 30))

        invoice = order.with_context(
            tz="Africa/Cairo", generate_pdf=False
        )._generate_pos_order_invoice()

        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.date, opening_utc_date + timedelta(days=1))
        self.assertEqual(
            invoice.invoice_date,
            fields.Datetime.context_timestamp(
                order.with_context(tz="Africa/Cairo"), order.date_order
            ).date(),
        )

    def test_grouped_pos_invoice_from_different_sessions_keeps_default_date(self):
        first_order = self._create_paid_order()
        second_order = self._create_paid_order()
        opening = fields.Datetime.now() - timedelta(days=1)
        first_order.session_id.start_at = opening
        other_session = self.env["pos.session"].create(
            {
                "config_id": self.pos_config_usd.id,
                "state": "closed",
                "start_at": opening - timedelta(days=2),
            }
        )
        second_order.session_id = other_session

        orders = first_order | second_order
        invoice = orders._create_invoice(orders._prepare_invoice_vals())

        self.assertEqual(invoice.date, invoice.invoice_date)

    def test_different_pos_configurations_keep_separate_accounts(self):
        jeddah_account = self.env["account.analytic.account"].create(
            {
                "name": "Jeddah Branch",
                "plan_id": self.analytic_plan.id,
                "company_id": self.company.id,
            }
        )
        self.pos_config_eur.analytic_account_id = jeddah_account
        riyadh_order = self._create_paid_order()
        jeddah_order = self.create_backend_pos_order(
            {
                "pos_config": self.pos_config_eur,
                "line_data": [
                    {
                        "product_id": self.ten_dollars_no_tax.product_variant_id.id,
                        "qty": 1,
                    }
                ],
            }
        )[0]
        self.assertEqual(riyadh_order.pos_analytic_account_id, self.analytic_account)
        self.assertEqual(jeddah_order.pos_analytic_account_id, jeddah_account)

    def test_session_closing_applies_only_to_profit_and_loss_lines(self):
        self._create_paid_order()
        session = self.pos_config_usd.current_session_id
        session.action_pos_session_closing_control()
        eligible_lines = session.move_id.line_ids.filtered(
            lambda line: line._is_pos_analytic_eligible()
        )
        excluded_lines = session.move_id.line_ids - eligible_lines
        self.assertTrue(eligible_lines)
        self.assertTrue(
            all(
                line.analytic_distribution.get(str(self.analytic_account.id))
                == 100.0
                for line in eligible_lines
            )
        )
        self.assertTrue(
            all(
                str(self.analytic_account.id)
                not in (line.analytic_distribution or {})
                for line in excluded_lines
            )
        )

    def test_split_payment_difference_applies_only_to_profit_and_loss(self):
        payment_method = self.bank_payment_method
        payment_method.outstanding_account_id = self.company_data[
            "default_account_receivable"
        ]
        payment_method.journal_id.profit_account_id = self.company_data[
            "default_account_revenue"
        ]
        values = self.pos_config_usd.current_session_id._get_diff_vals(
            payment_method.id,
            5,
        )
        profit_values = next(
            line_values
            for line_values in values
            if line_values["account_id"]
            == payment_method.journal_id.profit_account_id.id
        )
        counterpart_values = next(
            line_values for line_values in values if line_values is not profit_values
        )
        self.assertEqual(
            profit_values["analytic_distribution"],
            {str(self.analytic_account.id): 100.0},
        )
        self.assertNotIn("analytic_distribution", counterpart_values)

    def test_cash_closing_over_and_shortage_use_journal_difference_accounts(self):
        self.pos_config_usd.cash_control = True
        self.pos_config_usd.open_ui()
        session = self.pos_config_usd.current_session_id
        cash_journal = session.cash_journal_id
        cash_journal.write(
            {
                "profit_account_id": self.company_data[
                    "default_account_revenue"
                ].id,
                "loss_account_id": self.company_data[
                    "default_account_expense"
                ].id,
            }
        )

        for amount, difference_account in (
            (10, cash_journal.profit_account_id),
            (-10, cash_journal.loss_account_id),
        ):
            with self.subTest(amount=amount):
                session._post_statement_difference(amount)
                statement_line = self.env["account.bank.statement.line"].search(
                    [("pos_session_id", "=", session.id)],
                    order="id desc",
                    limit=1,
                )
                difference_lines = statement_line.move_id.line_ids.filtered(
                    lambda line: line.account_id == difference_account
                )
                other_lines = statement_line.move_id.line_ids - difference_lines

                self.assertTrue(difference_lines)
                self.assertTrue(
                    all(
                        line.analytic_distribution.get(
                            str(self.analytic_account.id)
                        )
                        == 100.0
                        for line in difference_lines
                    )
                )
                self.assertTrue(
                    all(
                        str(self.analytic_account.id)
                        not in (line.analytic_distribution or {})
                        for line in other_lines
                    )
                )

    def test_cash_closing_tax_line_does_not_get_pos_analytic(self):
        self.pos_config_usd.cash_control = True
        self.pos_config_usd.open_ui()
        session = self.pos_config_usd.current_session_id
        cash_journal = session.cash_journal_id
        cash_journal.profit_account_id = self.company_data[
            "default_account_revenue"
        ]
        cash_journal.profit_account_id.tax_ids = [
            Command.set(self.tax_five_incl.ids)
        ]

        session._post_statement_difference(10)
        statement_line = self.env["account.bank.statement.line"].search(
            [("pos_session_id", "=", session.id)], order="id desc", limit=1
        )
        difference_line = statement_line.move_id.line_ids.filtered(
            lambda line: line.account_id == cash_journal.profit_account_id
            and line.display_type != "tax"
        )
        tax_lines = statement_line.move_id.line_ids.filtered(
            lambda line: line.display_type == "tax"
        )
        cash_lines = statement_line.move_id.line_ids.filtered(
            lambda line: line.account_id == cash_journal.default_account_id
        )

        self.assertEqual(
            difference_line.analytic_distribution,
            {str(self.analytic_account.id): 100.0},
        )
        self.assertTrue(tax_lines)
        self.assertTrue(
            all(not line.analytic_distribution for line in tax_lines + cash_lines)
        )

    def test_cash_closing_without_pos_analytic_keeps_standard_behavior(self):
        self.pos_config_usd.analytic_account_id = False
        self.pos_config_usd.cash_control = True
        self.pos_config_usd.open_ui()
        session = self.pos_config_usd.current_session_id

        session._post_statement_difference(10)
        statement_line = self.env["account.bank.statement.line"].search(
            [("pos_session_id", "=", session.id)], order="id desc", limit=1
        )

        self.assertTrue(statement_line)
        self.assertTrue(
            all(
                not line.analytic_distribution
                for line in statement_line.move_id.line_ids
            )
        )

    def test_pos_without_analytic_account_keeps_standard_behavior(self):
        self.pos_config_usd.analytic_account_id = False
        order = self._create_paid_order()
        self.assertFalse(order.pos_analytic_account_id)
        session = self.pos_config_usd.current_session_id
        session.action_pos_session_closing_control()
        self.assertEqual(session.state, "closed")
