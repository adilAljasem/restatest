from datetime import date

from odoo import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestEmployeeLoanPayroll(AccountTestInvoicingCommon):
    """Loan installments flagged "salary deduction" are deducted by the Resta payslip."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref("employee_loan_management.group_employee_loan_manager")
            | cls.env.ref("resta_payroll.group_payroll_manager")
        )
        Account = cls.env["account.account"]
        expense = cls.company_data["default_account_expense"]
        cls.loan_account = Account.create({"name": "Employee Loans", "code": "106001",
                                           "account_type": "asset_receivable", "reconcile": True})
        payable = Account.create({"name": "Salaries Payable", "code": "210900",
                                  "account_type": "liability_current", "reconcile": True})
        gosi = Account.create({"name": "GOSI Payable", "code": "210910", "account_type": "liability_current"})
        cls.company.write({
            "employee_loan_account_id": cls.loan_account.id,
            "resta_payroll_journal_id": cls.company_data["default_journal_misc"].id,
            "resta_salary_account_id": expense.id, "resta_allowance_account_id": expense.id,
            "resta_gosi_expense_account_id": expense.id, "resta_gosi_payable_account_id": gosi.id,
            "resta_salary_payable_account_id": payable.id, "resta_eosb_accrual": False,
        })
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.bank_journal.outbound_payment_method_line_ids[0].payment_account_id = cls.bank_journal.default_account_id
        cls.employee = cls.env["hr.employee"].create({
            "name": "Loan Employee", "company_id": cls.company.id, "wage": 5000,
            "contract_date_start": date(2024, 1, 1), "country_id": cls.env.ref("base.eg").id,
        })

    def test_confirmed_payslip_marks_installment_paid_once(self):
        loan = self.env["employee.loan"].create({
            "employee_id": self.employee.id, "loan_amount": 300, "no_of_installments": 3,
            "payment_start_date": date(2026, 3, 15), "company_id": self.company.id,
        })
        loan.action_compute_installments()
        loan.action_send_to_approve()
        loan.action_approve()
        self.env["employee.loan.payment.wizard"].create({
            "loan_id": loan.id, "payment_kind": "disbursement", "payment_date": date(2026, 3, 1),
            "journal_id": self.bank_journal.id,
            "payment_method_line_id": self.bank_journal.outbound_payment_method_line_ids[0].id,
        }).action_create_payment()
        self.assertTrue(loan.is_disbursed)
        loan.installment_ids.write({"salary_deduction": True})
        first = loan.installment_ids.sorted("payment_date")[0]

        slip = self.env["resta.payslip"].create({
            "employee_id": self.employee.id, "date_from": date(2026, 3, 1), "date_to": date(2026, 3, 31),
        })
        slip.compute_sheet()
        self.assertEqual(sum(slip.line_ids.filtered(lambda l: l.code == "LOAN").mapped("amount")), -100.0)
        self.assertEqual(slip.net, 4900.0)
        slip.action_confirm()

        self.assertTrue(first.paid)
        self.assertEqual(first.payroll_state, "processed")
        self.assertEqual(first.payroll_payslip_id, slip)
        self.assertEqual(loan.total_paid_amount, 100)
        loan_line = slip.move_id.line_ids.filtered(lambda l: l.account_id == self.loan_account)
        self.assertEqual(loan_line.credit, 100.0)
        self.assertEqual(loan_line.partner_id, self.employee.work_contact_id)
        self.assertFalse(loan.installment_ids.filtered(lambda i: i != first and i.payroll_state != "none"))

        first._mark_paid_from_payroll(slip)
        self.assertEqual(loan.total_paid_amount, 100)
