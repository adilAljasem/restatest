from datetime import date

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.resta_payroll.wizard.wizards import eosb_award


@tagged("post_install", "-at_install", "resta_payroll")
class TestRestaPayroll(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("resta_payroll.group_payroll_manager") | \
            cls.env.ref("hr_holidays.group_hr_holidays_manager")
        env = cls.env
        Account = env["account.account"]
        cls.payable = Account.create({"code": "210900", "name": "رواتب مستحقة", "account_type": "liability_current",
                                      "reconcile": True})
        cls.gosi_payable = Account.create({"code": "210910", "name": "تأمينات مستحقة", "account_type": "liability_current"})
        cls.provision = Account.create({"code": "220900", "name": "مخصص نهاية الخدمة", "account_type": "liability_non_current"})
        expense = cls.company_data["default_account_expense"]
        cls.env.company.write({
            "resta_payroll_journal_id": cls.company_data["default_journal_misc"].id,
            "resta_salary_account_id": expense.id, "resta_allowance_account_id": expense.id,
            "resta_gosi_expense_account_id": expense.id, "resta_gosi_payable_account_id": cls.gosi_payable.id,
            "resta_salary_payable_account_id": cls.payable.id, "resta_eosb_expense_account_id": expense.id,
            "resta_eosb_provision_account_id": cls.provision.id,
        })
        cls.sa = env.ref("base.sa")
        cls.eg = env.ref("base.eg")
        cls.saudi = cls._employee("أحمد", cls.sa, 6000, 1500, 600, date(2020, 1, 1))
        cls.expat = cls._employee("Mohamed", cls.eg, 4000, 1000, 0, date(2023, 5, 1))

    @classmethod
    def _employee(cls, name, country, wage, housing, transport, start, **extra):
        return cls.env["hr.employee"].create(dict({
            "name": name, "country_id": country.id, "wage": wage, "contract_date_start": start,
            "resta_housing_allowance": housing, "resta_transport_allowance": transport,
        }, **extra))

    def _slip(self, employee, d_from=date(2026, 3, 1), d_to=date(2026, 3, 31), inputs=None):
        slip = self.env["resta.payslip"].create({"employee_id": employee.id, "date_from": d_from, "date_to": d_to,
                                                 "input_ids": [(0, 0, i) for i in inputs or []]})
        slip.compute_sheet()
        return slip

    def _line(self, slip, code):
        return sum(slip.line_ids.filtered(lambda l: l.code == code).mapped("amount"))

    def test_saudi_full_month(self):
        slip = self._slip(self.saudi)
        self.assertEqual(self.saudi.resta_gosi_scheme, "old")
        self.assertEqual(slip.gross, 8100.0)
        self.assertEqual(self._line(slip, "GOSI_EMP"), -731.25)
        self.assertEqual(slip.net, 7368.75)
        self.assertEqual(self._line(slip, "GOSI_ER"), 881.25)

    def test_non_saudi_employer_hazards_only(self):
        slip = self._slip(self.expat)
        self.assertEqual(self.expat.resta_gosi_scheme, "non_saudi")
        self.assertEqual(self._line(slip, "GOSI_EMP"), 0.0)
        self.assertEqual(self._line(slip, "GOSI_ER"), 100.0)
        self.assertEqual(slip.net, 5000.0)

    def test_new_gosi_law_steps(self):
        emp = self._employee("سارة", self.sa, 10000, 2500, 0, date(2025, 1, 1), resta_gosi_date=date(2025, 1, 1))
        self.assertEqual(emp.resta_gosi_scheme, "new")
        before = self._slip(emp, date(2026, 6, 1), date(2026, 6, 30))
        after = self._slip(emp, date(2026, 7, 1), date(2026, 7, 31))
        self.assertEqual(self._line(before, "GOSI_EMP"), -1281.25)  # 10.25% of 12,500
        self.assertEqual(self._line(after, "GOSI_EMP"), -1343.75)   # 10.75% of 12,500

    def test_gosi_cap(self):
        emp = self._employee("مدير", self.sa, 50000, 0, 0, date(2020, 1, 1))
        slip = self._slip(emp)
        self.assertEqual(self._line(slip, "GOSI_EMP"), -4387.5)  # 9.75% of the 45,000 cap

    def test_join_mid_month(self):
        emp = self._employee("جديد", self.sa, 6000, 0, 0, date(2026, 3, 16))
        slip = self._slip(emp)
        self.assertEqual(slip.paid_days, 16.0)
        self.assertEqual(self._line(slip, "BASIC"), 3200.0)

    def test_overtime_and_absence(self):
        ot = self.env.ref("resta_payroll.input_overtime")
        absence = self.env.ref("resta_payroll.input_absence")
        slip = self._slip(self.saudi, inputs=[{"type_id": ot.id, "quantity": 10},
                                              {"type_id": absence.id, "quantity": 2}])
        # law: hourly wage (8100/30/8 = 33.75) + 50% of basic hourly (25 / 2) = 46.25
        self.assertEqual(self._line(slip, "OT"), 462.5)
        self.assertEqual(self._line(slip, "ABSENCE"), -540.0)
        self.env.company.resta_overtime_method = "basic150"
        slip.compute_sheet()
        self.assertEqual(self._line(slip, "OT"), 375.0)

    def test_unpaid_leave(self):
        leave_type = self.env["hr.leave.type"].create({"name": "بدون راتب", "resta_unpaid": True,
                                                       "requires_allocation": False})
        leave = self.env["hr.leave"].with_context(leave_skip_state_check=True).create({
            "employee_id": self.saudi.id, "holiday_status_id": leave_type.id,
            "request_date_from": date(2026, 3, 10), "request_date_to": date(2026, 3, 12)})
        leave.sudo().action_approve()
        if leave.state != "validate":
            leave.sudo().action_validate()
        slip = self._slip(self.saudi)
        self.assertEqual(slip.unpaid_leave_days, leave.number_of_days)
        self.assertEqual(self._line(slip, "UNPAID"), -270.0 * leave.number_of_days)

    def test_batch_entry_and_payment(self):
        batch = self.env["resta.payslip.batch"].create({"date_from": date(2026, 3, 1), "date_to": date(2026, 3, 31)})
        batch.action_generate()
        self.assertEqual(set(batch.slip_ids.employee_id.ids), {self.saudi.id, self.expat.id})
        batch.action_confirm()
        self.assertEqual(batch.state, "confirmed")
        move = batch.move_id
        self.assertEqual(move.state, "posted")
        payable = move.line_ids.filtered(lambda l: l.account_id == self.payable)
        self.assertAlmostEqual(-sum(payable.mapped("balance")), batch.total_net)
        gosi = move.line_ids.filtered(lambda l: l.account_id == self.gosi_payable)
        self.assertAlmostEqual(-sum(gosi.mapped("balance")), 731.25 + 881.25 + 100.0)
        self.assertTrue(move.line_ids.filtered(lambda l: l.account_id == self.provision))
        bank = self.company_data["default_journal_bank"]
        batch._register_payment(bank, date(2026, 4, 1))
        self.assertEqual(batch.state, "paid")
        self.assertTrue(all(payable.mapped("reconciled")))
        self.assertEqual(set(batch.slip_ids.mapped("state")), {"paid"})

    def test_eosb_rules(self):
        self.assertEqual(eosb_award(10000, 7, "termination")[0], 45000.0)
        self.assertEqual(eosb_award(10000, 3, "resignation")[0], 5000.0)
        self.assertEqual(eosb_award(10000, 1.5, "resignation")[0], 0.0)
        self.assertAlmostEqual(eosb_award(10000, 7, "resignation")[0], 30000.0)
        self.assertEqual(eosb_award(10000, 12, "resignation")[0], 25000.0 + 70000.0)

    def test_eosb_wizard_adds_to_payslip(self):
        wiz = self.env["resta.eosb"].create({"employee_id": self.saudi.id, "end_date": date(2026, 3, 31),
                                             "reason": "termination"})
        self.assertGreater(wiz.years, 6.2)
        self.assertEqual(wiz.wage, 8100.0)
        action = wiz.action_add_to_payslip()
        slip = self.env["resta.payslip"].browse(action["res_id"])
        self.assertEqual(self._line(slip, "EOSB_PAY"), round(wiz.amount, 2))
