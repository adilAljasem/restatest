import base64
from datetime import date
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "resta_account")
class TestRestaAccountExtra(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country("sa")
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("account.group_account_manager") | cls.env.ref("account.group_account_user")
        cls.Report = cls.env["resta.account.report"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.expense = cls.company_data["default_account_expense"]
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.plan = cls.env["account.analytic.plan"].create({"name": "الفروع"})
        cls.branch = cls.env["account.analytic.account"].create({"name": "فرع العليا", "plan_id": cls.plan.id})

    def _invoice(self, move_type, amount, day, partner=None, account=None, analytic=None, due=None, **line):
        move = self.env["account.move"].create({
            "move_type": move_type, "partner_id": (partner or self.partner_a).id, "invoice_date": day, "date": day,
            "invoice_date_due": due or day, "invoice_payment_term_id": False,
            "invoice_line_ids": [Command.create(dict({
                "name": "بند", "quantity": 1, "price_unit": amount, "tax_ids": [Command.clear()],
                "account_id": (account or (self.revenue if move_type.startswith("out") else self.expense)).id,
                "analytic_distribution": {str(analytic.id): 100} if analytic else False,
            }, **line))],
        })
        move.action_post()
        return move

    def _pay(self, move, day):
        self.env["account.payment.register"].with_context(active_model="account.move", active_ids=move.ids).create({
            "payment_date": day, "journal_id": self.bank_journal.id})._create_payments()

    def _row(self, report, key):
        return next(r for r in report["rows"] if r["key"] == key)

    # ------------------------------------------------------------------ cash flow
    def test_cash_flow_classifies_by_counterpart(self):
        inv = self._invoice("out_invoice", 1000.0, date(2026, 4, 2))
        bill = self._invoice("in_invoice", 300.0, date(2026, 4, 3), partner=self.partner_b)
        self.assertEqual(len(bill.line_ids.filtered(lambda l: l.account_type == "liability_payable")), 1)
        self._pay(inv, date(2026, 4, 10))
        self._pay(bill, date(2026, 4, 11))
        rep = self.Report.get_report("cf", {"date_from": "2026-04-01", "date_to": "2026-04-30"})
        self.assertEqual(self._row(rep, "cf_customers")["values"][0], 1000.0)
        self.assertEqual(self._row(rep, "cf_suppliers")["values"][0], -300.0)
        self.assertEqual(self._row(rep, "operating")["values"][0], 700.0)
        opening, net, closing = (self._row(rep, k)["values"][0] for k in ("cf_opening", "cf_net", "cf_closing"))
        self.assertAlmostEqual(opening + net, closing)
        self.assertFalse([r for r in rep["rows"] if r["key"] == "cf_diff"])
        drill = self._row(rep, "cf_customers")
        self.assertFalse(drill.get("domain"))  # group rows are not drillable; account rows are
        account_row = next(r for r in rep["rows"] if r.get("account_id") and r["values"][0] == 1000.0)
        self.assertTrue(account_row["domain"])

    # ------------------------------------------------------------------ budgets
    def test_budget_vs_actual(self):
        budget = self.env["resta.budget"].create({"name": "موازنة 2026", "date_from": date(2026, 1, 1),
                                                  "date_to": date(2026, 12, 31), "analytic_account_id": self.branch.id})
        self.env["resta.budget.fill"].create({"budget_id": budget.id, "method": "amount", "amount": 12000.0,
                                              "account_ids": [Command.set(self.revenue.ids)]}).action_fill()
        self.assertEqual(len(budget.line_ids), 12)
        self.assertAlmostEqual(sum(budget.line_ids.mapped("planned_amount")), 12000.0, places=2)
        budget.action_confirm()
        self._invoice("out_invoice", 1500.0, date(2026, 5, 10), analytic=self.branch)
        self._invoice("out_invoice", 999.0, date(2026, 5, 11))  # another branch: not in this budget
        rep = self.Report.get_report("budget", {"budget_id": budget.id, "date_from": "2026-05-01",
                                                "date_to": "2026-05-31"})
        revenue = self._row(rep, "revenue")["values"]
        self.assertAlmostEqual(revenue[0], 12000.0 * 31 / 365, places=0)
        self.assertEqual(revenue[1], 1500.0)
        account_row = self._row(rep, "a%d" % self.revenue.id)
        self.assertEqual(account_row["status"], "good")
        may = budget.line_ids.filtered(lambda l: l.date_from == date(2026, 5, 1))
        self.assertEqual(may.actual_amount, 1500.0)

    def test_budget_fill_from_last_year(self):
        self._invoice("in_invoice", 2000.0, date(2025, 3, 15), partner=self.partner_b)
        budget = self.env["resta.budget"].create({"name": "2026", "date_from": date(2026, 1, 1),
                                                  "date_to": date(2026, 12, 31)})
        self.env["resta.budget.fill"].create({"budget_id": budget.id, "method": "last_year",
                                              "growth": 10.0}).action_fill()
        march = budget.line_ids.filtered(lambda l: l.date_from == date(2026, 3, 1) and l.account_id == self.expense)
        self.assertEqual(march.planned_amount, 2200.0)

    # ------------------------------------------------------------------ deferred
    def test_deferred_expense_spreads_yearly_rent(self):
        with patch.object(fields.Date, "context_today", return_value=date(2026, 3, 15)):
            bill = self._invoice("in_invoice", 12000.0, date(2026, 1, 1), partner=self.partner_b,
                                 analytic=self.branch, resta_deferred_start=date(2026, 1, 1),
                                 resta_deferred_end=date(2026, 12, 31))
        deferrals = bill.resta_deferred_move_ids
        self.assertEqual(len(deferrals), 13)  # transfer to prepaid + 12 months
        self.assertEqual(set(deferrals.filtered(lambda m: m.date <= date(2026, 3, 15)).mapped("state")), {"posted"})
        future = deferrals.filtered(lambda m: m.date > date(2026, 3, 15))
        self.assertEqual(set(future.mapped("state")), {"draft"})
        self.assertEqual(set(future.mapped("auto_post")), {"at_date"})
        prepaid = self.env.company.resta_deferred_expense_account_id
        self.assertTrue(prepaid)
        # all deferral entries together cancel out: the expense ends at 12000 over the year
        total_expense = sum(deferrals.line_ids.filtered(lambda l: l.account_id == self.expense).mapped("balance"))
        self.assertAlmostEqual(total_expense, 0.0)
        january = deferrals.filtered(lambda m: m.date == date(2026, 1, 31)).line_ids.filtered(
            lambda l: l.account_id == self.expense)
        self.assertAlmostEqual(january.balance, round(12000 * 31 / 365, 2))
        self.assertEqual(january.analytic_distribution, {str(self.branch.id): 100})
        # P&L of January shows one month of rent only
        rep = self.Report.get_report("pl", {"date_from": "2026-01-01", "date_to": "2026-01-31"})
        self.assertAlmostEqual(self._row(rep, "opex")["values"][0], round(12000 * 31 / 365, 2))
        # reset to draft: future drafts removed, posted ones reversed
        with patch.object(fields.Date, "context_today", return_value=date(2026, 3, 15)):
            bill.button_draft()
        self.assertFalse(bill.resta_deferred_move_ids)
        self.assertFalse(future.exists())

    def test_single_month_line_is_not_deferred(self):
        bill = self._invoice("in_invoice", 500.0, date(2026, 6, 1), partner=self.partner_b,
                             resta_deferred_start=date(2026, 6, 1), resta_deferred_end=date(2026, 6, 30))
        self.assertFalse(bill.resta_deferred_move_ids)

    # ------------------------------------------------------------------ payment follow-up
    def test_followup_levels_and_reminders(self):
        self.partner_a.email = "client@example.com"
        self.partner_a.phone = "0501234567"
        inv = self._invoice("out_invoice", 800.0, date(2026, 1, 1), due=date(2026, 1, 10))
        Partner = self.env["res.partner"]
        with patch.object(fields.Date, "context_today", return_value=date(2026, 1, 20)):
            data = Partner.resta_followup_overview(only_overdue=True)
            row = next(r for r in data["rows"] if r["id"] == self.partner_a.id)
            self.assertEqual(row["overdue"], 800.0)
            self.assertEqual(row["max_days"], 10)
            self.assertEqual(row["next_level_id"], self.env.ref("resta_account.followup_level_1").id)
            self.assertTrue(row["action_due"])
            self.assertTrue(row["whatsapp"].startswith("https://wa.me/966501234567"))
            messages = self.partner_a.message_ids
            res = self.partner_a.resta_followup_send()
            self.assertEqual(res["sent"], 1)
            new = self.partner_a.message_ids - messages
            self.assertTrue(new.attachment_ids, "the statement is attached to the email")
            self.assertEqual(self.partner_a.resta_followup_level_id, self.env.ref("resta_account.followup_level_1"))
            row = next(r for r in Partner.resta_followup_overview()["rows"] if r["id"] == self.partner_a.id)
            self.assertFalse(row["action_due"], "the same level is not sent twice")
        with patch.object(fields.Date, "context_today", return_value=date(2026, 2, 15)):
            Partner._cron_resta_followup()
            self.assertEqual(self.partner_a.resta_followup_level_id, self.env.ref("resta_account.followup_level_2"))
            self.assertTrue(self.partner_a.activity_ids, "level 2 asks someone to call")
        self._pay(inv, date(2026, 2, 16))
        with patch.object(fields.Date, "context_today", return_value=date(2026, 2, 17)):
            Partner._cron_resta_followup()
        self.assertFalse(self.partner_a.resta_followup_level_id, "paid customers start again from level 1")

    def test_followup_postpone_and_block(self):
        self._invoice("out_invoice", 100.0, date(2026, 1, 1), due=date(2026, 1, 2))
        with patch.object(fields.Date, "context_today", return_value=date(2026, 1, 20)):
            self.partner_a.resta_followup_postpone(date(2026, 1, 25))
            row = next(r for r in self.env["res.partner"].resta_followup_overview()["rows"]
                       if r["id"] == self.partner_a.id)
            self.assertFalse(row["action_due"])
            self.assertEqual(row["postponed_until"], "2026-01-25")
            self.partner_a.resta_followup_postpone(False)
            self.partner_a.resta_followup_set_blocked(True)
            row = next(r for r in self.env["res.partner"].resta_followup_overview()["rows"]
                       if r["id"] == self.partner_a.id)
            self.assertFalse(row["action_due"])
            self.assertTrue(row["blocked"])

    # ------------------------------------------------------------------ bank statement import
    def _wizard(self, content, filename):
        return self.env["resta.bank.import"].create({
            "journal_id": self.bank_journal.id, "data_file": base64.b64encode(content), "filename": filename})

    def test_import_arabic_csv_and_skip_duplicates(self):
        csv_data = ("كشف حساب\n"
                    "التاريخ,البيان,مدين,دائن,الرصيد\n"
                    "01/10/2026,تحويل من فندق,,\"5,000.00\",15000\n"
                    "02/10/2026,رسوم مدى,57.50,,14942.50\n").encode()
        wiz = self._wizard(csv_data, "statement.csv")
        wiz.action_read()
        self.assertEqual(wiz.file_format, "table")
        self.assertEqual(wiz.date_col_id.name, "التاريخ")
        self.assertEqual(wiz.debit_col_id.name, "مدين")
        self.assertEqual(wiz.credit_col_id.name, "دائن")
        self.assertEqual(wiz.count_new, 2)
        self.assertEqual(wiz.total_in, 5000.0)
        self.assertEqual(wiz.total_out, 57.5)
        action = wiz.action_import()
        self.assertEqual(action["params"]["journal_id"], self.bank_journal.id)
        lines = self.env["account.bank.statement.line"].search([("journal_id", "=", self.bank_journal.id),
                                                                ("payment_ref", "in", ("تحويل من فندق", "رسوم مدى"))])
        self.assertEqual(sorted(lines.mapped("amount")), [-57.5, 5000.0])
        again = self._wizard(csv_data, "statement.csv")
        again.action_read()
        self.assertEqual(again.count_new, 0)
        self.assertEqual(again.count_duplicate, 2)
        with self.assertRaises(UserError):
            again.action_import()

    def test_import_ofx_uses_bank_ids(self):
        ofx = (b"OFXHEADER:100\n<OFX><BANKTRANLIST>"
               b"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261003<TRNAMT>-45.00<FITID>F-1<NAME>STC Pay\n"
               b"<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261004<TRNAMT>1500.00<FITID>F-2<NAME>Hotel\n"
               b"</BANKTRANLIST></OFX>")
        wiz = self._wizard(ofx, "october.ofx")
        wiz.action_read()
        self.assertEqual(wiz.file_format, "ofx")
        wiz.action_import()
        line = self.env["account.bank.statement.line"].search([("resta_import_id", "=", "F-2")])
        self.assertEqual(line.amount, 1500.0)
        again = self._wizard(ofx, "october.ofx")
        again.action_read()
        self.assertEqual(again.count_duplicate, 2)
