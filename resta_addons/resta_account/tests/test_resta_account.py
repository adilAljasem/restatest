from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "resta_account")
class TestRestaAccount(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country("sa")
    def setUpClass(cls):
        super().setUpClass()
        cls.Report = cls.env["resta.account.report"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.expense = cls.company_data["default_account_expense"]
        cls.branch_plan = cls.env["account.analytic.plan"].create({"name": "الفروع"})
        cls.branch_a = cls.env["account.analytic.account"].create({"name": "فرع العليا", "plan_id": cls.branch_plan.id})
        cls.branch_b = cls.env["account.analytic.account"].create({"name": "فرع الملقا", "plan_id": cls.branch_plan.id})
        cls.day = date(2026, 3, 10)
        cls.invoice = cls._invoice("out_invoice", 1000.0, cls.tax_sale_a, analytic=cls.branch_a)
        cls.bill = cls._invoice("in_invoice", 400.0, cls.tax_purchase_a, partner=cls.partner_b)
        cls.opts = {"date_from": "2026-03-01", "date_to": "2026-03-31"}

    @classmethod
    def _invoice(cls, move_type, amount, tax, partner=None, analytic=None, day=None):
        move = cls.env["account.move"].create({
            "move_type": move_type, "partner_id": (partner or cls.partner_a).id,
            "invoice_date": day or cls.day, "date": day or cls.day,
            "invoice_line_ids": [Command.create({
                "name": "خدمة", "quantity": 1, "price_unit": amount, "tax_ids": [Command.set(tax.ids)],
                "analytic_distribution": {str(analytic.id): 100} if analytic else False,
            })],
        })
        move.action_post()
        return move

    def _row(self, report, key):
        return next(r for r in report["rows"] if r["key"] == key)

    # ------------------------------------------------------------------ statements
    def test_profit_and_loss(self):
        rep = self.Report.get_report("pl", self.opts)
        self.assertEqual(self._row(rep, "revenue")["values"][0], 1000.0)
        self.assertEqual(self._row(rep, "net")["values"][0], 600.0)

    def test_profit_and_loss_compare(self):
        rep = self.Report.get_report("pl", dict(self.opts, compare="previous_period"))
        self.assertEqual(len(rep["columns"]), 3)
        self.assertEqual(rep["columns"][1]["date_from"], "2026-02-01")
        self.assertEqual(self._row(rep, "revenue")["values"][1], 0.0)

    def test_branch_filter(self):
        rep_a = self.Report.get_report("pl", dict(self.opts, analytic_ids=[self.branch_a.id]))
        rep_b = self.Report.get_report("pl", dict(self.opts, analytic_ids=[self.branch_b.id]))
        self.assertEqual(self._row(rep_a, "revenue")["values"][0], 1000.0)
        self.assertEqual(self._row(rep_b, "revenue")["values"][0], 0.0)
        by_branch = self.Report.get_report("pl_branch", self.opts)
        names = [c["label"] for c in by_branch["columns"]]
        self.assertIn("فرع العليا", names)
        idx = names.index("فرع العليا")
        self.assertEqual(self._row(by_branch, "revenue")["values"][idx], 1000.0)
        self.assertEqual(self._row(by_branch, "revenue")["values"][-1], 1000.0)

    def test_balance_sheet_balances(self):
        rep = self.Report.get_report("bs", {"date_to": "2026-03-31"})
        self.assertTrue(rep["balanced"])
        self.assertEqual(self._row(rep, "current_earnings")["values"][0], 600.0)
        # receivable 1150 + input VAT 60 = payable 460 + output VAT 150 + profit 600
        self.assertEqual(self._row(rep, "assets")["values"][0], 1210.0)
        self.assertEqual(self._row(rep, "liabilities")["values"][0], 610.0)

    def test_trial_balance(self):
        rep = self.Report.get_report("tb", self.opts)
        total = self._row(rep, "total")["values"]
        self.assertAlmostEqual(total[0], 0.0)
        self.assertAlmostEqual(total[1], total[2])
        self.assertAlmostEqual(total[3], 0.0)
        # next year: last year's profit shows as undistributed opening balance
        rep = self.Report.get_report("tb", {"date_from": "2027-01-01", "date_to": "2027-01-31", "hide_zero": False})
        self.assertAlmostEqual(self._row(rep, "undistributed")["values"][0], -600.0)
        self.assertAlmostEqual(self._row(rep, "total")["values"][0], 0.0)

    def test_general_ledger_lines(self):
        rep = self.Report.get_report("gl", self.opts)
        row = next(r for r in rep["rows"] if r.get("account_id") == self.revenue.id)
        detail = self.Report.get_detail("gl", self.opts, row)
        self.assertEqual(detail["lines"][-1]["balance"], -1000.0)

    def test_partner_ledger_and_aged(self):
        rep = self.Report.get_report("partner", self.opts)
        row = next(r for r in rep["rows"] if r.get("partner_id") == self.partner_a.id)
        self.assertEqual(row["values"][3], 1150.0)
        aged = self.Report.get_report("aged", {"date_to": "2026-03-31"})
        self.assertEqual(self._row(aged, "total")["values"][-1], 1150.0)
        payable = self.Report.get_report("aged", {"date_to": "2026-03-31", "partner_type": "payable"})
        self.assertEqual(self._row(payable, "total")["values"][-1], 460.0)

    def test_vat_return(self):
        rep = self.Report.get_report("tax", self.opts)
        labels = [c["expression_label"] for c in rep["columns"]]
        sa_1 = next(r for r in rep["rows"] if r["name"].startswith("1."))
        sa_7 = next(r for r in rep["rows"] if r["name"].startswith("7."))
        self.assertEqual(sa_1["values"][labels.index("base")], 1000.0)
        self.assertEqual(sa_1["values"][labels.index("tax")], 150.0)
        self.assertEqual(sa_7["values"][labels.index("base")], 400.0)
        self.assertEqual(sa_7["values"][labels.index("tax")], 60.0)
        net = [r for r in rep["rows"] if r["values"][labels.index("tax")] == 90.0]
        self.assertTrue(net, "net VAT due (150 - 60) must appear")

    # ------------------------------------------------------------------ bank reconciliation
    def _st_line(self, amount, label="حوالة", partner=None):
        return self.env["account.bank.statement.line"].create({
            "journal_id": self.company_data["default_journal_bank"].id, "date": self.day,
            "payment_ref": label, "amount": amount, "partner_id": partner and partner.id})

    def test_bank_reconciliation_full(self):
        line = self._st_line(1150.0, "INV %s" % self.invoice.name)
        candidates = line.resta_rec_candidates()
        receivable = self.invoice.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
        self.assertEqual(candidates[0]["id"], receivable.id)
        line.resta_reconcile([receivable.id])
        self.assertTrue(line.is_reconciled)
        self.assertIn(self.invoice.payment_state, ("paid", "in_payment"))

    def test_bank_reconciliation_partial_and_fee(self):
        receivable = self.invoice.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
        line = self._st_line(500.0, partner=self.partner_a)
        line.resta_reconcile([receivable.id])
        self.assertTrue(line.is_reconciled)
        self.assertEqual(self.invoice.amount_residual, 650.0)
        fee = self._st_line(-20.0, "رسوم بنكية")
        fee.resta_reconcile([], {"account_id": self.expense.id, "label": "رسوم"})
        self.assertTrue(fee.is_reconciled)
        fee.resta_unreconcile()
        self.assertFalse(fee.is_reconciled)

    def test_aged_as_of_before_payment(self):
        receivable = self.invoice.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
        line = self.env["account.bank.statement.line"].create({
            "journal_id": self.company_data["default_journal_bank"].id, "date": date(2026, 4, 5),
            "payment_ref": "دفعة", "amount": 1150.0})
        line.resta_reconcile([receivable.id])
        before = self.Report.get_report("aged", {"date_to": "2026-03-31"})
        after = self.Report.get_report("aged", {"date_to": "2026-04-30"})
        self.assertEqual(self._row(before, "total")["values"][-1], 1150.0)
        self.assertEqual(self._row(after, "total")["values"][-1], 0.0)

    # ------------------------------------------------------------------ assets
    def _asset_type(self, **vals):
        fixed = self.env["account.account"].create({"code": "151099", "name": "معدات مطبخ", "account_type": "asset_fixed"})
        accum = self.env["account.account"].create({"code": "151199", "name": "مجمع إهلاك معدات", "account_type": "asset_fixed"})
        dep = self.env["account.account"].create({"code": "651099", "name": "مصروف إهلاك", "account_type": "expense_depreciation"})
        return self.env["resta.asset.type"].create(dict({
            "name": "معدات مطبخ", "months": 12, "prorata": False, "asset_account_id": fixed.id,
            "depreciation_account_id": accum.id, "expense_account_id": dep.id,
            "journal_id": self.company_data["default_journal_misc"].id}, **vals))

    def test_asset_straight_line_and_dispose(self):
        atype = self._asset_type()
        asset = self.env["resta.asset"].create({"name": "فرن", "type_id": atype.id, "original_value": 12000,
                                                "acquisition_date": date(2026, 1, 1)})
        asset.action_compute_board()
        self.assertEqual(len(asset.line_ids), 12)
        self.assertEqual(set(asset.line_ids.mapped("amount")), {1000.0})
        asset.state = "open"
        asset._post_due(date(2026, 3, 31))
        self.assertEqual(asset.depreciated_value, 3000.0)
        self.assertEqual(asset.book_value, 9000.0)
        move = asset._dispose(date(2026, 4, 15), self.expense)
        self.assertEqual(asset.state, "disposed")
        self.assertEqual(move.state, "posted")
        self.assertFalse(asset.line_ids.filtered(lambda l: not l.move_id))

    def test_asset_prorata(self):
        atype = self._asset_type(prorata=True)
        asset = self.env["resta.asset"].create({"name": "ثلاجة", "type_id": atype.id, "original_value": 3100,
                                                "acquisition_date": date(2026, 1, 17)})
        asset.action_compute_board()
        self.assertEqual(len(asset.line_ids), 13)
        self.assertAlmostEqual(sum(asset.line_ids.mapped("amount")), 3100.0)
        self.assertLess(asset.line_ids[0].amount, asset.line_ids[1].amount)

    def test_asset_from_bill(self):
        atype = self._asset_type()
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.partner_b.id, "invoice_date": self.day,
            "invoice_line_ids": [Command.create({"name": "خلاط صناعي", "quantity": 1, "price_unit": 6000,
                                                 "account_id": atype.asset_account_id.id, "tax_ids": False})]})
        bill.action_post()
        self.assertEqual(len(bill.resta_asset_ids), 1)
        self.assertEqual(bill.resta_asset_ids.original_value, 6000.0)
        self.assertEqual(bill.resta_asset_ids.state, "draft")

    # ------------------------------------------------------------------ closing
    def test_lock_dates(self):
        wiz = self.env["resta.lock.dates"].create({"fiscalyear_lock_date": date(2026, 3, 31)})
        wiz.action_apply()
        self.assertEqual(self.env.company.fiscalyear_lock_date, date(2026, 3, 31))
        with self.assertRaises(UserError):
            self.invoice.button_draft()

    def test_accountant_group_selectable(self):
        group = self.env.ref("account.group_account_user")
        self.assertTrue(group.privilege_id)
        self.assertIn(group, self.env.ref("account.group_account_manager").all_implied_ids)
