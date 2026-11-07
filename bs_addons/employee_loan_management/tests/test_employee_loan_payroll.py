from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.hr_payroll.tests.common import TestPayslipBase


@tagged("post_install", "-at_install")
class TestEmployeeLoanPayroll(TestPayslipBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref(
            "employee_loan_management.group_employee_loan_manager"
        )
        cls.input_type = cls.env["hr.payslip.input.type"].create(
            {
                "name": "Loan Deduction",
                "code": "LOAN_DEDUCTION",
                "available_in_attachments": True,
            }
        )
        cls.env["hr.salary.rule"].create(
            {
                "name": "Loan Deduction",
                "sequence": 140,
                "amount_select": "code",
                "amount_python_compute": (
                    "result = -inputs['LOAN_DEDUCTION'].amount "
                    "if 'LOAN_DEDUCTION' in inputs else 0"
                ),
                "code": "LOAN_DEDUCTION",
                "category_id": cls.env.ref("hr_payroll.DED").id,
                "struct_id": cls.developer_pay_structure.id,
            }
        )
        cls.company_us.employee_loan_salary_input_type_id = cls.input_type

    def test_validated_payslip_marks_installment_paid_once(self):
        today = fields.Date.today()
        loan = self.env["employee.loan"].create(
            {
                "employee_id": self.richard_emp.id,
                "loan_amount": 100,
                "no_of_installments": 1,
                "payment_start_date": today,
                "company_id": self.company_us.id,
            }
        )
        loan.action_compute_installments()
        loan.action_send_to_approve()
        loan.action_approve()
        installment = loan.installment_ids
        attachment = self.env["hr.salary.attachment"].create(
            {
                "employee_ids": [Command.set(self.richard_emp.ids)],
                "company_id": self.company_us.id,
                "description": "Loan payroll integration test",
                "other_input_type_id": self.input_type.id,
                "duration_type": "one",
                "monthly_amount": installment.amount,
                "total_amount": installment.amount,
                "date_start": today,
                "employee_loan_installment_id": installment.id,
            }
        )
        installment.write(
            {
                "salary_deduction": True,
                "salary_attachment_id": attachment.id,
                "payroll_state": "sent",
            }
        )

        payslip = self.env["hr.payslip"].create(
            {
                "name": "Loan Deduction Payslip",
                "employee_id": self.richard_emp.id,
                "date_from": today.replace(day=1),
                "date_to": today.replace(day=1)
                + relativedelta(months=1, days=-1),
            }
        )
        payslip.compute_sheet()
        self.assertIn(attachment, payslip.salary_attachment_ids)
        payslip.action_payslip_done()

        self.assertTrue(installment.paid)
        self.assertEqual(installment.payroll_state, "processed")
        self.assertEqual(installment.payroll_payslip_id, payslip)
        self.assertEqual(loan.total_paid_amount, 100)
        self.assertEqual(loan.balance_amount, 0)
        self.assertEqual(attachment.paid_amount, 100)

        installment._mark_paid_from_payroll(payslip)
        self.assertEqual(attachment.paid_amount, 100)
