from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestEmployeeLoan(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref("employee_loan_management.group_employee_loan_manager")
            | cls.env.ref("account.group_account_manager")
        )
        cls.employee_partner = cls.env["res.partner"].create(
            {"name": "Loan Employee"}
        )
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Loan Employee",
                "work_contact_id": cls.employee_partner.id,
                "company_id": cls.company.id,
            }
        )
        cls.loan_account = cls.env["account.account"].create(
            {
                "name": "Employee Loans",
                "code": "106001",
                "account_type": "asset_current",
                "company_ids": [Command.set(cls.company.ids)],
            }
        )
        cls.salary_input_type = cls.env["hr.payslip.input.type"].create(
            {
                "name": "Loan Deduction",
                "code": "LOAN_DEDUCTION",
                "available_in_attachments": True,
            }
        )
        cls.company.write(
            {
                "employee_loan_account_id": cls.loan_account.id,
                "employee_loan_salary_input_type_id": cls.salary_input_type.id,
            }
        )
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.bank_journal.outbound_payment_method_line_ids[0].payment_account_id = (
            cls.bank_journal.default_account_id
        )
        cls.bank_journal.inbound_payment_method_line_ids[0].payment_account_id = (
            cls.bank_journal.default_account_id
        )

    def _create_loan(self, amount=500, count=5, start=date(2026, 8, 28)):
        return self.env["employee.loan"].create(
            {
                "employee_id": self.employee.id,
                "loan_amount": amount,
                "no_of_installments": count,
                "payment_start_date": start,
                "company_id": self.company.id,
            }
        )

    def _approve_loan(self, loan):
        loan.action_compute_installments()
        loan.action_send_to_approve()
        loan.action_approve()
        return loan

    def _create_payment(self, loan, installment=False):
        payment_kind = "repayment" if installment else "disbursement"
        payment_methods = (
            self.bank_journal.inbound_payment_method_line_ids
            if installment
            else self.bank_journal.outbound_payment_method_line_ids
        )
        wizard = self.env["employee.loan.payment.wizard"].create(
            {
                "loan_id": loan.id,
                "installment_id": installment.id if installment else False,
                "payment_kind": payment_kind,
                "payment_date": date(2026, 8, 28),
                "journal_id": self.bank_journal.id,
                "payment_method_line_id": payment_methods[0].id,
            }
        )
        action = wizard.action_create_payment()
        self.assertEqual(action, {"type": "ir.actions.client", "tag": "reload"})
        return installment.payment_id if installment else loan.disbursement_payment_id

    def test_sequence_defaults_and_constraints(self):
        loan = self._create_loan()
        self.assertRegex(loan.name, r"^LO/\d{5}$")
        self.assertEqual(loan.applicant_id, self.env.user)
        self.assertEqual(loan.department_id, self.employee.department_id)
        with self.assertRaises((UserError, ValidationError)):
            self._create_loan(amount=0)
        with self.assertRaises((UserError, ValidationError)):
            self._create_loan(count=0)

    def test_compute_installments_is_monthly_exact_and_idempotent(self):
        loan = self._create_loan(amount=500.01, count=3, start=date(2026, 1, 31))
        loan.action_compute_installments()
        first_line_ids = loan.installment_ids.ids
        self.assertEqual(len(first_line_ids), 3)
        self.assertEqual(
            loan.installment_ids.mapped("payment_date"),
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
        )
        self.assertEqual(sum(loan.installment_ids.mapped("amount")), 500.01)

        loan.action_compute_installments()
        self.assertEqual(len(loan.installment_ids), 3)
        self.assertNotEqual(first_line_ids, loan.installment_ids.ids)
        self.assertEqual(sum(loan.installment_ids.mapped("amount")), 500.01)

    def test_workflow_and_approved_values_are_locked(self):
        loan = self._approve_loan(self._create_loan())
        self.assertEqual(loan.state, "approved")
        with self.assertRaises(UserError):
            loan.loan_amount = 600
        with self.assertRaises(UserError):
            loan.installment_ids[0].amount = 120

    def test_disbursement_and_direct_installment_payment(self):
        loan = self._approve_loan(self._create_loan())
        disbursement = self._create_payment(loan)
        loan_line = disbursement.move_id.line_ids.filtered(
            lambda line: line.account_id == self.loan_account
        )
        self.assertEqual(loan_line.balance, 500)
        self.assertTrue(loan.is_disbursed)

        with self.assertRaises(UserError):
            self._create_payment(loan)

        installment = loan.installment_ids[0]
        collection = self._create_payment(loan, installment)
        collection_loan_line = collection.move_id.line_ids.filtered(
            lambda line: line.account_id == self.loan_account
        )
        self.assertEqual(collection_loan_line.balance, -100)
        self.assertTrue(installment.paid)
        self.assertEqual(loan.total_paid_amount, 100)
        self.assertEqual(loan.balance_amount, 400)

        with self.assertRaises(UserError):
            self._create_payment(loan, installment)

    def test_due_salary_deduction_is_created_once(self):
        loan = self._approve_loan(
            self._create_loan(amount=200, count=2, start=date(2026, 8, 1))
        )
        self._create_payment(loan)
        installment = loan.installment_ids[0]
        installment.salary_deduction = True

        self.env["employee.loan.installment"]._cron_create_due_salary_attachments()
        attachment = installment.salary_attachment_id
        self.assertEqual(installment.payroll_state, "sent")
        self.assertEqual(attachment.monthly_amount, 100)
        self.assertEqual(attachment.employee_loan_installment_id, installment)

        self.env["employee.loan.installment"]._cron_create_due_salary_attachments()
        self.assertEqual(
            self.env["hr.salary.attachment"].search_count(
                [("employee_loan_installment_id", "=", installment.id)]
            ),
            1,
        )

    def test_user_approver_and_record_rules(self):
        user_group = self.env.ref(
            "employee_loan_management.group_employee_loan_user"
        )
        approver_group = self.env.ref(
            "employee_loan_management.group_employee_loan_approver"
        )
        loan_user = self.env["res.users"].with_context(
            no_reset_password=True
        ).create(
            {
                "name": "Employee Loan User",
                "login": "employee.loan.user@test.example",
                "group_ids": [Command.set(user_group.ids)],
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
            }
        )
        approver = self.env["res.users"].with_context(
            no_reset_password=True
        ).create(
            {
                "name": "Employee Loan Approver",
                "login": "employee.loan.approver@test.example",
                "group_ids": [Command.set(approver_group.ids)],
                "company_id": self.company.id,
                "company_ids": [Command.set(self.company.ids)],
            }
        )
        own_loan = self._create_loan()
        own_loan.applicant_id = loan_user
        other_loan = self._create_loan()

        self.assertEqual(own_loan.with_user(loan_user).name, own_loan.name)
        with self.assertRaises(AccessError):
            other_loan.with_user(loan_user).read(["name"])
        self.assertEqual(other_loan.with_user(approver).name, other_loan.name)

        own_loan.action_compute_installments()
        own_loan.action_send_to_approve()
        with self.assertRaises(AccessError):
            own_loan.with_user(loan_user).action_approve()
        own_loan.with_user(approver).action_approve()
        self.assertEqual(own_loan.state, "approved")
